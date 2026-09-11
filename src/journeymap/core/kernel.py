"""Deterministic, domain-neutral simulation kernel."""

from collections.abc import Mapping

from journeymap.core.canonical import (
    JsonObject,
    JsonValue,
    clone_json_object,
    clone_json_value,
    state_digest,
)
from journeymap.core.events import EventBus, EventEnvelope, EventSourceKind
from journeymap.core.handlers import (
    ActionHandlerNotFoundError,
    ActionRegistry,
    ActionRequest,
    ActionResult,
    ActionStatus,
    ActionValidationError,
    HandlerNotFoundError,
    ResolutionContext,
    SystemEventOutcome,
    SystemEventRegistry,
    TimedActionHandler,
    TransitionPlan,
    ValidationContext,
)
from journeymap.core.modules import ModuleRegistry
from journeymap.core.persistence import Persistence
from journeymap.core.run import (
    DeterministicRng,
    RngSnapshot,
    RunManifest,
    RunStatus,
    SimulationRun,
    TransitionIdentity,
)
from journeymap.core.scheduler import (
    DeterministicScheduler,
    ScheduledEvent,
    ScheduledEventSpec,
)


class InitialStateMismatchError(ValueError):
    """Raised when a manifest does not identify the supplied initial state."""


class ReentrantMutationError(RuntimeError):
    """Raised when a handler or subscriber re-enters a kernel mutation API."""


class SimulationKernel:
    """Coordinate deterministic services without owning domain behavior."""

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
        self._modules = modules
        self._persistence = persistence
        self._run = SimulationRun(manifest)
        self._state = clone_json_object(initial_state)
        actual_digest = state_digest(self._state)
        if actual_digest != manifest.initial_state_digest:
            raise InitialStateMismatchError(
                f"initial state digest mismatch: expected {manifest.initial_state_digest}, "
                f"got {actual_digest}"
            )
        self._action_registry = action_registry
        self._system_event_registry = system_event_registry
        self._event_bus = event_bus
        self._scheduler = DeterministicScheduler(manifest.run_id)
        self._events: list[EventEnvelope] = []
        self._action_results: list[ActionResult] = []
        self._system_event_outcomes: list[SystemEventOutcome] = []
        self._transition_active = False

    @property
    def is_booted(self) -> bool:
        return self._run.status is RunStatus.RUNNING

    @property
    def manifest(self) -> RunManifest:
        return self._run.manifest

    @property
    def module_ids(self) -> tuple[str, ...]:
        return self._modules.module_ids

    @property
    def simulation_time(self) -> int:
        return self._run.clock.now

    @property
    def state_snapshot(self) -> JsonObject:
        return clone_json_object(self._state)

    @property
    def state_digest(self) -> str:
        return state_digest(self._state)

    @property
    def rng_snapshot(self) -> RngSnapshot:
        return self._run.rng.snapshot()

    @property
    def transition_sequence(self) -> int:
        return self._run.transition_sequence

    @property
    def events(self) -> tuple[EventEnvelope, ...]:
        return tuple(event.detached() for event in self._events)

    @property
    def action_results(self) -> tuple[ActionResult, ...]:
        return tuple(self._action_results)

    @property
    def system_event_outcomes(self) -> tuple[SystemEventOutcome, ...]:
        return tuple(self._system_event_outcomes)

    @property
    def pending_scheduled_events(self) -> tuple[ScheduledEvent, ...]:
        return self._scheduler.pending

    def boot(self) -> None:
        if self._run.status is not RunStatus.CREATED:
            raise RuntimeError(f"cannot boot kernel in run status {self._run.status}")
        self._modules.validate_dependencies()
        self._persistence.initialize()
        self._run.start()

    def close(self) -> None:
        self._ensure_not_transitioning()
        if self._run.status is not RunStatus.RUNNING:
            return
        self._persistence.close()
        self._run.close()

    def schedule(self, spec: ScheduledEventSpec) -> ScheduledEvent:
        self._ensure_running()
        self._ensure_not_transitioning()
        if spec.due_time < self.simulation_time:
            raise ValueError("cannot schedule an event in the past")
        return self._scheduler.schedule(spec)

    def advance_to(self, target_time: int) -> tuple[SystemEventOutcome, ...]:
        """Process due system inputs before setting the clock to the target."""

        self._ensure_running()
        self._ensure_not_transitioning()
        self._transition_active = True
        try:
            return self._advance_to(target_time)
        finally:
            self._transition_active = False

    def _advance_to(self, target_time: int) -> tuple[SystemEventOutcome, ...]:
        """Internal advance under the public operation's reentrancy guard."""

        if not isinstance(target_time, int) or isinstance(target_time, bool):
            raise ValueError("target_time must be an integer")
        if target_time < self.simulation_time:
            raise ValueError("cannot advance the kernel backwards")

        outcomes: list[SystemEventOutcome] = []
        while (scheduled := self._scheduler.peek_next_due(target_time)) is not None:
            time_before = self.simulation_time
            self._run.clock.advance_to(scheduled.due_time)
            try:
                outcome, emitted_events = self._dispatch_system_event(scheduled)
            except Exception:
                self._run.clock._restore(time_before)
                raise
            consumed = self._scheduler.pop_next_due(target_time)
            if consumed != scheduled:
                raise RuntimeError("scheduler head changed during system event dispatch")
            self._system_event_outcomes.append(outcome)
            outcomes.append(outcome)
            self._publish_events(emitted_events)
        self._run.clock.advance_to(target_time)
        return tuple(outcomes)

    def submit_action(self, request: ActionRequest) -> ActionResult:
        """Process arrival, validate start, elapse duration, then commit completion."""

        self._ensure_running()
        self._ensure_not_transitioning()
        if request.run_id != self.manifest.run_id:
            raise ValueError(
                f"action run_id {request.run_id!r} does not match {self.manifest.run_id!r}"
            )
        self.advance_to(request.submitted_at)
        request = request.detached()
        started_at = self.simulation_time
        self._transition_active = True
        try:
            try:
                handler = self._action_registry.get(request.action_type, request.schema_version)
            except HandlerNotFoundError as error:
                raise ActionHandlerNotFoundError(str(error)) from error
            handler_id = handler.handler_id
            timing = None
            try:
                handler.validate(request.detached(), self._validation_context())
                if isinstance(handler, TimedActionHandler):
                    timing = handler.prepare(request.detached(), self._validation_context())
                    timing = timing.detached()
            except ActionValidationError as error:
                return self._record_action_failure(
                    request, handler_id, started_at, ActionStatus.REJECTED, error
                )
            if timing is not None and isinstance(handler, TimedActionHandler):
                # Independent system commits survive any later completion failure.
                self._advance_to(started_at + timing.duration)
                try:
                    handler.validate_completion(
                        request.detached(), timing.detached(), self._validation_context()
                    )
                except ActionValidationError as error:
                    return self._record_action_failure(
                        request, handler_id, started_at, ActionStatus.FAILED, error
                    )
            candidate_rng = self._run.rng.clone()
            plan = handler.resolve(request.detached(), self._resolution_context(candidate_rng))
            transition, digest_after, emitted_events = self._commit_plan(
                plan=plan,
                source_kind=EventSourceKind.ACTION,
                source_ref=request.action_request_id,
                causation_id=request.action_request_id,
                correlation_id=request.correlation_id or request.action_request_id,
                rng_after=candidate_rng.snapshot(),
            )
            result = ActionResult(
                action_request_id=request.action_request_id,
                handler_id=handler_id,
                transition=transition,
                state_digest_after=digest_after,
                emitted_event_ids=tuple(event.event_id for event in emitted_events),
                status=ActionStatus.SUCCEEDED,
                reason_code=None,
                started_at=started_at,
                resolved_at=self.simulation_time,
            )
            self._action_results.append(result)
            self._publish_events(emitted_events)
            return result
        finally:
            self._transition_active = False

    def _record_action_failure(
        self,
        request: ActionRequest,
        handler_id: str,
        started_at: int,
        status: ActionStatus,
        error: ActionValidationError,
    ) -> ActionResult:
        result = ActionResult(
            action_request_id=request.action_request_id,
            handler_id=handler_id,
            transition=None,
            state_digest_after=self.state_digest,
            emitted_event_ids=(),
            status=status,
            reason_code=error.reason_code,
            started_at=started_at,
            resolved_at=self.simulation_time,
        )
        self._action_results.append(result)
        return result

    def _dispatch_system_event(
        self,
        scheduled: ScheduledEvent,
    ) -> tuple[SystemEventOutcome, tuple[EventEnvelope, ...]]:
        handler = self._system_event_registry.get(
            scheduled.event_type,
            scheduled.schema_version,
        )
        handler_id = handler.handler_id
        handler.validate(scheduled.detached(), self._validation_context())
        candidate_rng = self._run.rng.clone()
        plan = handler.resolve(scheduled.detached(), self._resolution_context(candidate_rng))
        transition, digest_after, emitted_events = self._commit_plan(
            plan=plan,
            source_kind=EventSourceKind.SCHEDULED_EVENT,
            source_ref=scheduled.scheduled_event_id,
            causation_id=scheduled.scheduled_event_id,
            correlation_id=scheduled.correlation_id,
            rng_after=candidate_rng.snapshot(),
        )
        outcome = SystemEventOutcome(
            scheduled_event_id=scheduled.scheduled_event_id,
            handler_id=handler_id,
            transition=transition,
            state_digest_after=digest_after,
            emitted_event_ids=tuple(event.event_id for event in emitted_events),
        )
        return outcome, emitted_events

    def _commit_plan(
        self,
        *,
        plan: TransitionPlan,
        source_kind: EventSourceKind,
        source_ref: str,
        causation_id: str,
        correlation_id: str,
        rng_after: RngSnapshot,
    ) -> tuple[TransitionIdentity, str, tuple[EventEnvelope, ...]]:
        candidate_state = clone_json_object(self._state)
        for key in sorted(plan.delete_keys):
            candidate_state.pop(key, None)
        for key in sorted(plan.set_values):
            candidate_state[key] = clone_json_value(plan.set_values[key])
        digest_after = state_digest(candidate_state)

        transition = self._run.pending_transition(
            source_kind=source_kind,
            source_ref=source_ref,
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
        emitted_events: list[EventEnvelope] = []
        for offset, draft in enumerate(plan.events, start=1):
            event_id, event_sequence = self._run.pending_event_identity(offset)
            emitted_events.append(
                EventEnvelope(
                    event_id=event_id,
                    run_id=self.manifest.run_id,
                    event_sequence=event_sequence,
                    simulation_time=self.simulation_time,
                    event_type=draft.event_type,
                    schema_version=draft.schema_version,
                    source_kind=source_kind,
                    source_ref=source_ref,
                    transition_id=transition.transition_id,
                    causation_id=transition.transition_id,
                    correlation_id=correlation_id,
                    payload=draft.payload,
                )
            )

        self._run.commit_sequences(len(emitted_events))
        self._state = candidate_state
        self._run.rng.restore(rng_after)
        self._events.extend(emitted_events)
        return transition, digest_after, tuple(emitted_events)

    def _publish_events(self, events: tuple[EventEnvelope, ...]) -> None:
        for event in events:
            self._event_bus.publish(event)

    def _validation_context(self) -> ValidationContext:
        return ValidationContext(
            run_id=self.manifest.run_id,
            simulation_time=self.simulation_time,
            state=self.state_snapshot,
        )

    def _resolution_context(self, rng: DeterministicRng) -> ResolutionContext:
        return ResolutionContext(
            run_id=self.manifest.run_id,
            simulation_time=self.simulation_time,
            state=self.state_snapshot,
            rng=rng,
        )

    def _ensure_running(self) -> None:
        if not self.is_booted:
            raise RuntimeError("simulation kernel is not booted")

    def _ensure_not_transitioning(self) -> None:
        if self._transition_active:
            raise ReentrantMutationError(
                "kernel mutation APIs cannot be called during handler or subscriber execution"
            )
