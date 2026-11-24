"""Opt-in concurrent-v1 execution using the existing handler/commit boundary."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from typing import Protocol, runtime_checkable

from journeymap.core.canonical import (
    JsonObject,
    JsonValue,
    canonical_json,
    clone_json_object,
    state_digest,
)
from journeymap.core.events import EventBus, EventEnvelope, EventSourceKind
from journeymap.core.handlers import (
    ActionHandlerNotFoundError,
    ActionRegistry,
    ActionRequest,
    ActionResult,
    ActionStatus,
    ActionTiming,
    ActionValidationError,
    HandlerNotFoundError,
    SystemEventOutcome,
    SystemEventRegistry,
    TimedActionHandler,
)
from journeymap.core.kernel import SimulationKernel
from journeymap.core.modules import ModuleRegistry
from journeymap.core.persistence import Persistence
from journeymap.core.run import RunManifest
from journeymap.core.scheduler import ScheduledEvent, ScheduledEventSpec


@dataclass(frozen=True, slots=True)
class Admission:
    request_id: str
    actor_id: str
    started_at: int
    completes_at: int | None
    status: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class InFlight:
    request: ActionRequest
    timing: ActionTiming | None
    started_at: int
    completes_at: int
    handler_id: str
    absence: JsonObject | None = None

    def detached(self) -> "InFlight":
        return replace(
            self,
            request=self.request.detached(),
            timing=self.timing.detached() if self.timing else None,
            absence=clone_json_object(self.absence) if self.absence is not None else None,
        )


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    sequence: int
    tick: int
    kind: str
    source_ref: str
    correlation_id: str
    status: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class ExecutionCommand:
    kind: str
    tick: int
    request: ActionRequest | None = None
    schedule: ScheduledEventSpec | None = None

    def detached(self) -> "ExecutionCommand":
        return replace(
            self,
            request=self.request.detached() if self.request else None,
            schedule=replace(self.schedule) if self.schedule else None,
        )


@runtime_checkable
class AbsenceHandler(Protocol):
    """Module-owned description of an actor leaving ordinary spatial presence."""

    def absence(self, request: ActionRequest, timing: ActionTiming) -> JsonObject: ...


class ConcurrentKernel(SimulationKernel):
    """Single logical world; acceptance never advances time or resolves effects.

    All actions are exclusive per actor, including instantaneous handlers.
    Domain handlers still resolve against current truth, using _commit_plan.
    """

    def __init__(
        self,
        modules: ModuleRegistry,
        persistence: Persistence,
        manifest: RunManifest,
        initial_state: Mapping[str, JsonValue],
        action_registry: ActionRegistry,
        system_event_registry: SystemEventRegistry,
        event_bus: EventBus,
    ) -> None:
        if "_concurrent" in initial_state:
            raise ValueError("_concurrent is a reserved runtime projection")
        super().__init__(
            modules,
            persistence,
            manifest,
            initial_state,
            action_registry,
            system_event_registry,
            event_bus,
        )
        self._in_flight: dict[str, InFlight] = {}
        self._admissions: dict[str, tuple[str, Admission | None]] = {}
        self._journal: list[ExecutionRecord] = []
        self._commands: list[ExecutionCommand] = []
        self._priority_cursor = 0
        # Frozen order survives an interrupted batch, without repeating a commit.
        self._completion_batch: list[str] = []

    @property
    def in_flight(self) -> tuple[InFlight, ...]:
        return tuple(self._in_flight[a].detached() for a in sorted(self._in_flight))

    @property
    def journal(self) -> tuple[ExecutionRecord, ...]:
        return tuple(self._journal)

    @property
    def commands(self) -> tuple[ExecutionCommand, ...]:
        return tuple(command.detached() for command in self._commands)

    @property
    def perception_snapshot(self) -> JsonObject:
        world = self.state_snapshot
        # Current execution facts only. Future schedules remain separate.
        absent: JsonObject = {}
        for actor, flight in self._in_flight.items():
            if flight.absence is not None:
                absent[actor] = {
                    "started_at": flight.started_at,
                    "completes_at": flight.completes_at,
                    **flight.absence,
                }
        active: list[JsonValue] = [
            {
                "actor_id": f.request.actor_id,
                "action_type": f.request.action_type,
                "payload": f.request.payload,
                "started_at": f.started_at,
                "completes_at": f.completes_at,
            }
            for f in self.in_flight
        ]
        world["_concurrent"] = {"absent": absent, "active_actions": active}
        return world

    @property
    def execution_snapshot(self) -> JsonObject:
        """Research state for equality/checkpoints, distinct from domain digest."""
        # Canonical round-trip isolates nested dataclasses and enum values.
        import json

        return clone_json_object(
            json.loads(
                json.dumps(
                    {
                        "mode": "concurrent-v1",
                        "tick": self.simulation_time,
                        "world": super().state_snapshot,
                        "in_flight": [asdict(f) for f in self.in_flight],
                        "admissions": {
                            k: [fp, asdict(a) if a else None]
                            for k, (fp, a) in self._admissions.items()
                        },
                        "priority_cursor": self._priority_cursor,
                        "completion_batch": list(self._completion_batch),
                        "rng": asdict(self.rng_snapshot),
                        "transition_sequence": self.transition_sequence,
                        "scheduled": [asdict(e) for e in self.pending_scheduled_events],
                        "results": [asdict(r) for r in self.action_results],
                        "events": [asdict(e) for e in self.events],
                        "system_outcomes": [asdict(o) for o in self.system_event_outcomes],
                        "journal": [asdict(r) for r in self.journal],
                    },
                    ensure_ascii=False,
                )
            )
        )

    def submit_action(self, request: ActionRequest) -> ActionResult:
        raise RuntimeError("concurrent-v1 requires accept_action and explicit advance_to")

    def schedule(self, spec: ScheduledEventSpec) -> ScheduledEvent:
        event = super().schedule(spec)
        self._commands.append(
            ExecutionCommand("schedule", self.simulation_time, schedule=replace(spec))
        )
        return event

    def _record(
        self, kind: str, source: str, correlation: str, status: str, reason: str | None = None
    ) -> None:
        self._journal.append(
            ExecutionRecord(
                len(self._journal) + 1,
                self.simulation_time,
                kind,
                source,
                correlation,
                status,
                reason,
            )
        )

    def accept_action(self, request: ActionRequest) -> Admission:
        self._ensure_running()
        self._ensure_not_transitioning()
        request = request.detached()
        fingerprint = canonical_json(asdict(request))
        previous = self._admissions.get(request.action_request_id)
        if previous is not None:
            if previous[0] != fingerprint:
                return Admission(
                    request.action_request_id,
                    request.actor_id,
                    self.simulation_time,
                    None,
                    "REJECTED",
                    "REQUEST_ID_CONFLICT",
                )
            if previous[1] is None:
                raise RuntimeError("indeterminate admission; automatic retry forbidden")
            return previous[1]
        if request.run_id != self.manifest.run_id or request.submitted_at != self.simulation_time:
            raise ValueError("admission must belong to this run and current tick")
        if self._completion_batch or any(
            f.completes_at <= self.simulation_time for f in self.in_flight
        ):
            raise ValueError("advance_to(current tick) must drain existing completions first")
        if any(e.due_time <= self.simulation_time for e in self.pending_scheduled_events):
            raise ValueError("advance_to(current tick) must drain environment inputs first")
        self._transition_active = True
        try:
            try:
                handler = self._action_registry.get(request.action_type, request.schema_version)
            except HandlerNotFoundError as error:
                raise ActionHandlerNotFoundError(str(error)) from error
            # A dispatch miss has no engine effects; the live boundary records
            # UNKNOWN_ACTION. Do not poison the executable replay command stream.
            self._commands.append(ExecutionCommand("accept", self.simulation_time, request=request))
            self._admissions[request.action_request_id] = (fingerprint, None)
            try:
                if request.actor_id in self._in_flight:
                    raise ActionValidationError("ACTOR_BUSY")
                handler.validate(request.detached(), self._validation_context())
                timing = (
                    handler.prepare(request.detached(), self._validation_context()).detached()
                    if isinstance(handler, TimedActionHandler)
                    else None
                )
            except ActionValidationError as error:
                self._record_action_failure(
                    request, handler.handler_id, self.simulation_time, ActionStatus.REJECTED, error
                )
                admission = Admission(
                    request.action_request_id,
                    request.actor_id,
                    self.simulation_time,
                    None,
                    "REJECTED",
                    error.reason_code,
                )
                self._record(
                    "rejected",
                    request.action_request_id,
                    request.correlation_id or request.action_request_id,
                    admission.status,
                    admission.reason,
                )
            else:
                finish = self.simulation_time + (timing.duration if timing else 0)
                absence = (
                    handler.absence(request.detached(), timing.detached())
                    if timing is not None and isinstance(handler, AbsenceHandler)
                    else None
                )
                flight = InFlight(
                    request, timing, self.simulation_time, finish, handler.handler_id, absence
                ).detached()
                self._in_flight[request.actor_id] = flight
                admission = Admission(
                    request.action_request_id,
                    request.actor_id,
                    self.simulation_time,
                    finish,
                    "ACCEPTED",
                )
                self._record(
                    "accepted",
                    request.action_request_id,
                    request.correlation_id or request.action_request_id,
                    "ACCEPTED",
                )
            self._admissions[request.action_request_id] = (fingerprint, admission)
            return admission
        finally:
            self._transition_active = False

    def advance_to(self, target_time: int) -> tuple[SystemEventOutcome, ...]:
        self._ensure_running()
        self._ensure_not_transitioning()
        if type(target_time) is not int or target_time < self.simulation_time:
            raise ValueError("target tick must be an integer and cannot go backwards")
        self._commands.append(ExecutionCommand("advance", target_time))
        offset = len(self.system_event_outcomes)
        self._transition_active = True
        try:
            while True:
                due = [f.completes_at for f in self._in_flight.values()]
                due.extend(e.due_time for e in self.pending_scheduled_events)
                if not due or min(due) > target_time:
                    break
                tick = min(due)
                self._advance_to(tick)  # existing system dispatch/rollback/delivery semantics
                if not self._completion_batch:
                    actors = sorted({*self._in_flight})
                    # Rotate over the actual competing cohort, independently of input order.
                    ready = [a for a in actors if self._in_flight[a].completes_at == tick]
                    if ready:
                        pivot = self._priority_cursor % len(ready)
                        self._completion_batch = ready[pivot:] + ready[:pivot]
                        self._priority_cursor += 1
                while self._completion_batch:
                    actor = self._completion_batch[0]
                    self._complete(self._in_flight[actor])
            self._advance_to(target_time)
            return self.system_event_outcomes[offset:]
        finally:
            self._transition_active = False

    def advance_next(self) -> int | None:
        times = [f.completes_at for f in self.in_flight]
        times.extend(e.due_time for e in self.pending_scheduled_events)
        if not times:
            return None
        self.advance_to(min(times))
        return self.simulation_time

    def _complete(self, flight: InFlight) -> None:
        request = flight.request
        handler = self._action_registry.get(request.action_type, request.schema_version)
        events: tuple[EventEnvelope, ...]
        try:
            if flight.timing is not None and isinstance(handler, TimedActionHandler):
                handler.validate_completion(
                    request.detached(), flight.timing.detached(), self._validation_context()
                )
            else:
                handler.validate(request.detached(), self._validation_context())
        except ActionValidationError as error:
            result = self._record_action_failure(
                request, handler.handler_id, flight.started_at, ActionStatus.FAILED, error
            )
            events = ()
        else:
            candidate_rng = self._run.rng.clone()
            plan = handler.resolve(request.detached(), self._resolution_context(candidate_rng))
            transition, digest, events = self._commit_plan(
                plan=plan,
                source_kind=EventSourceKind.ACTION,
                source_ref=request.action_request_id,
                causation_id=request.action_request_id,
                correlation_id=request.correlation_id or request.action_request_id,
                rng_after=candidate_rng.snapshot(),
            )
            result = ActionResult(
                request.action_request_id,
                handler.handler_id,
                transition,
                digest,
                tuple(e.event_id for e in events),
                ActionStatus.SUCCEEDED,
                None,
                flight.started_at,
                self.simulation_time,
            )
            self._action_results.append(result)
        del self._in_flight[request.actor_id]
        self._completion_batch.pop(0)
        self._record(
            "completed",
            request.action_request_id,
            request.correlation_id or request.action_request_id,
            result.status.value,
            result.reason_code,
        )
        # Commit and removal precede delivery; a delivery defect cannot repeat completion.
        self._publish_events(events)

    def _dispatch_system_event(
        self,
        scheduled: ScheduledEvent,
    ) -> tuple[SystemEventOutcome, tuple[EventEnvelope, ...]]:
        outcome, events = super()._dispatch_system_event(scheduled)
        self._record(
            "environment", scheduled.scheduled_event_id, scheduled.correlation_id, "COMMITTED"
        )
        return outcome, events


def execution_digest(kernel: ConcurrentKernel) -> str:
    return state_digest(kernel.execution_snapshot)
