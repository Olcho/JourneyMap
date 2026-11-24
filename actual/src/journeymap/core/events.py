"""Immutable event envelopes and a synchronous deterministic event bus."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from journeymap.core.canonical import JsonObject, clone_json_object


class EventSourceKind(StrEnum):
    ACTION = "ACTION"
    SCHEDULED_EVENT = "SCHEDULED_EVENT"


@dataclass(frozen=True, slots=True)
class EventDraft:
    """Handler-produced event content before the kernel assigns provenance."""

    event_type: str
    schema_version: int
    payload: JsonObject

    def __post_init__(self) -> None:
        if not self.event_type:
            raise ValueError("event_type must not be empty")
        if self.schema_version <= 0:
            raise ValueError("schema_version must be positive")
        object.__setattr__(self, "payload", clone_json_object(self.payload))


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """A committed fact with deterministic ordering and provenance."""

    event_id: str
    run_id: str
    event_sequence: int
    simulation_time: int
    event_type: str
    schema_version: int
    source_kind: EventSourceKind
    source_ref: str
    transition_id: str
    causation_id: str
    correlation_id: str
    payload: JsonObject

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", clone_json_object(self.payload))

    def detached(self) -> "EventEnvelope":
        """Return a copy whose payload cannot mutate the stored envelope."""

        return EventEnvelope(
            event_id=self.event_id,
            run_id=self.run_id,
            event_sequence=self.event_sequence,
            simulation_time=self.simulation_time,
            event_type=self.event_type,
            schema_version=self.schema_version,
            source_kind=self.source_kind,
            source_ref=self.source_ref,
            transition_id=self.transition_id,
            causation_id=self.causation_id,
            correlation_id=self.correlation_id,
            payload=self.payload,
        )


type EventSubscriber = Callable[[EventEnvelope], None]


class EventSubscriptionError(ValueError):
    """Raised when a subscriber ordering identity is invalid or duplicated."""


class EventDeliveryError(RuntimeError):
    """Raised after commit when a deterministic subscriber callback fails."""

    def __init__(
        self,
        event_id: str,
        subscriber_key: tuple[int, str, str],
    ) -> None:
        self.event_id = event_id
        self.subscriber_key = subscriber_key
        super().__init__(
            f"subscriber {subscriber_key!r} failed while delivering event {event_id!r}"
        )


@dataclass(frozen=True, slots=True)
class _Subscription:
    event_type: str | None
    priority: int
    module_id: str
    subscriber_id: str
    subscriber: EventSubscriber

    @property
    def ordering_key(self) -> tuple[int, str, str]:
        return (self.priority, self.module_id, self.subscriber_id)


class EventBus:
    """Publish events synchronously using explicit subscriber ordering keys."""

    def __init__(self) -> None:
        self._subscriptions: dict[tuple[int, str, str], _Subscription] = {}

    def subscribe(
        self,
        *,
        event_type: str | None,
        priority: int,
        module_id: str,
        subscriber_id: str,
        subscriber: EventSubscriber,
    ) -> None:
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise EventSubscriptionError("subscriber priority must be an integer")
        if (
            not isinstance(module_id, str)
            or not module_id
            or not isinstance(subscriber_id, str)
            or not subscriber_id
        ):
            raise EventSubscriptionError("module_id and subscriber_id must not be empty")
        key = (priority, module_id, subscriber_id)
        if key in self._subscriptions:
            raise EventSubscriptionError(f"duplicate subscriber ordering key: {key!r}")
        self._subscriptions[key] = _Subscription(
            event_type,
            priority,
            module_id,
            subscriber_id,
            subscriber,
        )

    def publish(self, event: EventEnvelope) -> None:
        """Call all matching subscribers before returning."""

        subscriptions = sorted(self._subscriptions.values(), key=lambda item: item.ordering_key)
        for subscription in subscriptions:
            if subscription.event_type is None or subscription.event_type == event.event_type:
                try:
                    subscription.subscriber(event.detached())
                except Exception as error:
                    raise EventDeliveryError(event.event_id, subscription.ordering_key) from error
