"""Closed actor-visible experiences, separate from historical Observation memory.

The trusted turn caller supplies receipts after confirming engine submission.
This module has no world, ResearchView, provider or scheduler capability.
"""

import json
from dataclasses import asdict, dataclass, field
from typing import Protocol, cast

from journeymap.application.contracts import normalize_request, validate_action_contract
from journeymap.application.reasons import FALLBACK_REASONS, VISIBLE_DOMAIN_REASONS
from journeymap.core.canonical import JsonObject, canonical_json
from journeymap.core.controller import ControllerActionResult
from journeymap.core.handlers import ActionRequest, ActionStatus
from journeymap.core.observations import Observation, validate_observation_v1

EVENT_TRACE_VERSION = 1
EVENT_MEMORY_PROMPT_VERSION = "alderwick-event-memory-decision-1"
EVENT_MEMORY_PROTOCOL_VERSION = "alderwick-event-memory-phase1-1"


def observation_snapshot(observation: Observation) -> JsonObject:
    return {
        "observation_id": observation.observation_id,
        "run_id": observation.run_id,
        "actor_id": observation.actor_id,
        "observation_sequence": observation.observation_sequence,
        "simulation_time": observation.simulation_time,
        "schema_version": observation.schema_version,
        "content_digest": observation.content_digest,
        "content": observation.content,
    }


@dataclass(frozen=True, slots=True)
class EventTrace:
    """Immutable JSON snapshot; nested reads cannot mutate the archived intent."""

    event_trace_id: str
    sequence: int
    run_id: str
    actor_id: str
    decision_opportunity_id: str
    opportunity_sequence: int
    attempt: int
    observation: Observation
    _action_json: str = field(repr=False)
    actor_visible_receipt: ControllerActionResult
    opened_at: int
    closed_at: int
    memory_eligible: bool = True
    schema_version: int = EVENT_TRACE_VERSION

    @property
    def action_request(self) -> ActionRequest:
        return ActionRequest(**json.loads(self._action_json))

    def to_json(self) -> JsonObject:
        data = cast(JsonObject, asdict(self))
        del data["_action_json"]
        data["action_request"] = cast(JsonObject, asdict(self.action_request))
        data["observation"] = observation_snapshot(self.observation)
        receipt = cast(JsonObject, asdict(self.actor_visible_receipt))
        receipt["status"] = str(self.actor_visible_receipt.status)
        data["actor_visible_receipt"] = receipt
        return data


@dataclass(frozen=True, slots=True)
class EventMemoryContext:
    current: Observation
    decision_opportunity_id: str
    opportunity_sequence: int
    attempt: int
    prior: tuple[EventTrace, ...] = ()

    def __post_init__(self) -> None:
        validate_observation_v1(self.current)
        object.__setattr__(self, "prior", tuple(self.prior))
        previous_sequence = previous_close = 0
        for trace in self.prior:
            if (
                type(trace) is not EventTrace
                or trace.run_id != self.current.run_id
                or trace.actor_id != self.current.actor_id
                or not trace.memory_eligible
                or not previous_sequence < trace.sequence
                or not trace.opportunity_sequence < self.opportunity_sequence
                or not trace.observation.observation_sequence < self.current.observation_sequence
                or not previous_close <= trace.closed_at <= self.current.simulation_time
            ):
                raise ValueError("event memory requires closed same-actor/run prior experiences")
            previous_sequence, previous_close = trace.sequence, trace.closed_at


class EventMemoryPolicy(Protocol):
    policy_id: str
    policy_version: str

    def select_events(self, context: EventMemoryContext) -> tuple[EventTrace, ...]: ...


class RecencyEventMemory:
    """Recency-based Event Memory, fixed k=1, ordered by closure sequence."""

    policy_id = "Recency-based Event Memory"
    policy_version = "recency-event-memory-k1-1"

    def select_events(self, context: EventMemoryContext) -> tuple[EventTrace, ...]:
        return context.prior[-1:]


class EventTraceArchive:
    """One actor/run session. Failed attempts retain explicit opportunity lineage.

    A closed experience ends an opportunity even at the same tick. Before a
    closure, only a changed tick or perceived content opens another opportunity.
    Content digest is a change detector, never the identity or deduplication key.
    """

    def __init__(self) -> None:
        self._traces: list[EventTrace] = []
        self._context: EventMemoryContext | None = None
        self._closed = False

    @property
    def traces(self) -> tuple[EventTrace, ...]:
        return tuple(self._traces)

    def begin(self, observation: Observation) -> EventMemoryContext:
        validate_observation_v1(observation)
        old = self._context
        if old is not None and (
            (old.current.run_id, old.current.actor_id) != (observation.run_id, observation.actor_id)
            or observation.observation_sequence < old.current.observation_sequence
            or observation.simulation_time < old.current.simulation_time
            or (self._traces and observation.simulation_time < self._traces[-1].closed_at)
            or (
                self._closed
                and observation.observation_sequence <= old.current.observation_sequence
            )
        ):
            raise ValueError("event archive requires a forward same-actor/run session")
        new = (
            old is None
            or self._closed
            or old.current.simulation_time != observation.simulation_time
            or old.current.content_digest != observation.content_digest
        )
        sequence = (old.opportunity_sequence if old else 0) + int(new)
        context = EventMemoryContext(
            observation,
            f"{observation.run_id}:{observation.actor_id}:opportunity:{sequence:08d}",
            sequence,
            1 if new or old is None else old.attempt + 1,
            self.traces,
        )
        self._context, self._closed = context, False
        return context

    def close(
        self,
        observation: Observation,
        request: ActionRequest,
        receipt: ControllerActionResult,
        *,
        engine_submitted: bool,
    ) -> EventTrace:
        """Trusted receipt ingestion, never infers a receipt from a raw world result."""
        if engine_submitted is not True:
            raise ValueError("trace requires explicit confirmation of engine submission")
        request = normalize_request(request)
        validate_action_contract(request)
        action_json = canonical_json(cast(JsonObject, asdict(request)))
        if type(receipt) is not ControllerActionResult or (
            any(
                type(value) is not str or not value
                for value in (receipt.run_id, receipt.actor_id, receipt.action_request_id)
            )
            or receipt.run_id != observation.run_id
            or receipt.actor_id != observation.actor_id
            or receipt.action_request_id != request.action_request_id
            or request.run_id != observation.run_id
            or request.actor_id != observation.actor_id
            or request.based_on_observation_id != observation.observation_id
            or request.submitted_at != observation.simulation_time
            or type(receipt.started_at) is not int
            or receipt.started_at != request.submitted_at
            or type(receipt.resolved_at) is not int
            or receipt.resolved_at < receipt.started_at
            or type(receipt.schema_version) is not int
            or receipt.schema_version != 1
            or type(receipt.status) not in (str, ActionStatus)
            or receipt.status not in tuple(ActionStatus)
            or (
                receipt.reason_code is not None
                and (
                    type(receipt.reason_code) is not str
                    or receipt.reason_code not in VISIBLE_DOMAIN_REASONS | FALLBACK_REASONS
                )
            )
            or (receipt.status == ActionStatus.SUCCEEDED and receipt.reason_code is not None)
            or (receipt.status != ActionStatus.SUCCEEDED and receipt.reason_code is None)
            or (
                receipt.status == ActionStatus.REJECTED
                and receipt.resolved_at != receipt.started_at
            )
        ):
            raise ValueError("trace requires a bound actor-visible receipt")
        for trace in self._traces:
            if trace.action_request.action_request_id == request.action_request_id:
                if (
                    trace._action_json != action_json
                    or trace.observation != observation
                    or trace.actor_visible_receipt != receipt
                ):
                    raise ValueError("conflicting event trace retry")
                return trace
        context = self._context
        if context is None or self._closed or context.current != observation:
            raise ValueError("trace must close the active decision attempt")
        sequence = len(self._traces) + 1
        trace = EventTrace(
            f"{observation.run_id}:{observation.actor_id}:event-trace:{sequence:08d}",
            sequence,
            observation.run_id,
            observation.actor_id,
            context.decision_opportunity_id,
            context.opportunity_sequence,
            context.attempt,
            observation,
            action_json,
            receipt,
            observation.simulation_time,
            receipt.resolved_at,
        )
        self._traces.append(trace)
        self._closed = True
        return trace
