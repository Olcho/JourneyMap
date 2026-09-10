"""Separate actor-action and system-event handler contracts and registries."""

from dataclasses import dataclass, field
from typing import Protocol

from journeymap.core.canonical import JsonObject, clone_json_object
from journeymap.core.events import EventDraft
from journeymap.core.run import DeterministicRng, TransitionIdentity
from journeymap.core.scheduler import ScheduledEvent


@dataclass(frozen=True, slots=True)
class ActionRequest:
    """Minimal recorded actor intent used by the M1 replay stream."""

    action_request_id: str
    run_id: str
    actor_id: str
    based_on_observation_id: str
    submitted_at: int
    action_type: str
    schema_version: int
    payload: JsonObject
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.action_request_id
            or not self.run_id
            or not self.actor_id
            or not self.based_on_observation_id
        ):
            raise ValueError("action request identity fields must not be empty")
        if (
            not isinstance(self.submitted_at, int)
            or isinstance(self.submitted_at, bool)
            or self.submitted_at < 0
        ):
            raise ValueError("submitted_at must be a non-negative integer")
        if not self.action_type:
            raise ValueError("action_type must not be empty")
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version <= 0
        ):
            raise ValueError("schema_version must be a positive integer")
        object.__setattr__(self, "payload", clone_json_object(self.payload))

    def detached(self) -> "ActionRequest":
        """Return a payload-isolated copy for one handler invocation."""

        return ActionRequest(
            action_request_id=self.action_request_id,
            run_id=self.run_id,
            actor_id=self.actor_id,
            based_on_observation_id=self.based_on_observation_id,
            submitted_at=self.submitted_at,
            action_type=self.action_type,
            schema_version=self.schema_version,
            payload=self.payload,
            correlation_id=self.correlation_id,
        )


@dataclass(frozen=True, slots=True)
class ValidationContext:
    """Read-only-by-copy state made available during handler validation."""

    run_id: str
    simulation_time: int
    state: JsonObject


@dataclass(frozen=True, slots=True)
class ResolutionContext:
    """Isolated state and transactional RNG used during resolution."""

    run_id: str
    simulation_time: int
    state: JsonObject
    rng: DeterministicRng


@dataclass(frozen=True, slots=True)
class TransitionPlan:
    """Validated top-level canonical state changes and ordered events."""

    set_values: JsonObject = field(default_factory=dict)
    delete_keys: tuple[str, ...] = ()
    events: tuple[EventDraft, ...] = ()

    def __post_init__(self) -> None:
        if len(set(self.delete_keys)) != len(self.delete_keys):
            raise ValueError("delete_keys must not contain duplicates")
        overlap = set(self.set_values).intersection(self.delete_keys)
        if overlap:
            raise ValueError(f"state keys cannot be set and deleted together: {sorted(overlap)!r}")
        object.__setattr__(self, "set_values", clone_json_object(self.set_values))


@dataclass(frozen=True, slots=True)
class ActionResult:
    action_request_id: str
    handler_id: str
    transition: TransitionIdentity
    state_digest_after: str
    emitted_event_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SystemEventOutcome:
    scheduled_event_id: str
    handler_id: str
    transition: TransitionIdentity
    state_digest_after: str
    emitted_event_ids: tuple[str, ...]


class ActionHandler(Protocol):
    """Validate and resolve an actor intent without direct canonical writes."""

    @property
    def handler_id(self) -> str: ...

    def validate(self, request: ActionRequest, context: ValidationContext) -> None: ...

    def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan: ...


class SystemEventHandler(Protocol):
    """Validate and resolve a system input without creating an ActionRequest."""

    @property
    def handler_id(self) -> str: ...

    def validate(self, event: ScheduledEvent, context: ValidationContext) -> None: ...

    def resolve(self, event: ScheduledEvent, context: ResolutionContext) -> TransitionPlan: ...


class HandlerRegistrationError(ValueError):
    """Raised when a handler key is invalid or duplicated."""


class HandlerNotFoundError(LookupError):
    """Raised when no handler exists for an exact type/schema pair."""


class ActionRegistry:
    """Registry exclusively for actor action handlers."""

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, int], ActionHandler] = {}

    def register(self, action_type: str, schema_version: int, handler: ActionHandler) -> None:
        key = _handler_key(action_type, schema_version)
        if key in self._handlers:
            raise HandlerRegistrationError(f"duplicate action handler: {key!r}")
        if not handler.handler_id:
            raise HandlerRegistrationError("handler_id must not be empty")
        self._handlers[key] = handler

    def get(self, action_type: str, schema_version: int) -> ActionHandler:
        key = _handler_key(action_type, schema_version)
        try:
            return self._handlers[key]
        except KeyError as error:
            raise HandlerNotFoundError(f"unknown action handler: {key!r}") from error


class SystemEventRegistry:
    """Registry exclusively for scheduled or naturally triggered system handlers."""

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, int], SystemEventHandler] = {}

    def register(
        self,
        event_type: str,
        schema_version: int,
        handler: SystemEventHandler,
    ) -> None:
        key = _handler_key(event_type, schema_version)
        if key in self._handlers:
            raise HandlerRegistrationError(f"duplicate system event handler: {key!r}")
        if not handler.handler_id:
            raise HandlerRegistrationError("handler_id must not be empty")
        self._handlers[key] = handler

    def get(self, event_type: str, schema_version: int) -> SystemEventHandler:
        key = _handler_key(event_type, schema_version)
        try:
            return self._handlers[key]
        except KeyError as error:
            raise HandlerNotFoundError(f"unknown system event handler: {key!r}") from error


def _handler_key(input_type: str, schema_version: int) -> tuple[str, int]:
    if not input_type:
        raise HandlerRegistrationError("handler input type must not be empty")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version <= 0
    ):
        raise HandlerRegistrationError("handler schema_version must be a positive integer")
    return (input_type, schema_version)
