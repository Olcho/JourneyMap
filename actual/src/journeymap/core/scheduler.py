"""Deterministic scheduling of hidden future system-event inputs."""

import heapq
from dataclasses import dataclass

from journeymap.core.canonical import JsonObject, clone_json_object


@dataclass(frozen=True, slots=True)
class ScheduledEventSpec:
    """Versioned scenario input before a run-local sequence is assigned."""

    due_time: int
    priority: int
    event_type: str
    schema_version: int
    payload: JsonObject
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.due_time, int)
            or isinstance(self.due_time, bool)
            or self.due_time < 0
        ):
            raise ValueError("due_time must be a non-negative integer")
        if not isinstance(self.priority, int) or isinstance(self.priority, bool):
            raise ValueError("priority must be an integer")
        if not self.event_type:
            raise ValueError("event_type must not be empty")
        if (
            not isinstance(self.schema_version, int)
            or isinstance(self.schema_version, bool)
            or self.schema_version <= 0
        ):
            raise ValueError("schema_version must be a positive integer")
        object.__setattr__(self, "payload", clone_json_object(self.payload))


@dataclass(frozen=True, slots=True)
class ScheduledEvent:
    """A hidden scheduler item ordered by time, priority, then insertion."""

    scheduled_event_id: str
    run_id: str
    due_time: int
    priority: int
    insertion_sequence: int
    event_type: str
    schema_version: int
    payload: JsonObject
    correlation_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", clone_json_object(self.payload))

    @property
    def ordering_key(self) -> tuple[int, int, int]:
        return (self.due_time, self.priority, self.insertion_sequence)

    def detached(self) -> "ScheduledEvent":
        return ScheduledEvent(
            scheduled_event_id=self.scheduled_event_id,
            run_id=self.run_id,
            due_time=self.due_time,
            priority=self.priority,
            insertion_sequence=self.insertion_sequence,
            event_type=self.event_type,
            schema_version=self.schema_version,
            payload=self.payload,
            correlation_id=self.correlation_id,
        )


@dataclass(frozen=True, slots=True)
class ScenarioSchedule:
    """Versioned ordered system-event input for replay."""

    scenario_id: str
    scenario_version: str
    events: tuple[ScheduledEventSpec, ...]

    def __post_init__(self) -> None:
        if not self.scenario_id or not self.scenario_version:
            raise ValueError("scenario schedule identity and version must not be empty")


class DeterministicScheduler:
    """A heap whose complete key never depends on object comparison or hashes."""

    def __init__(self, run_id: str) -> None:
        self._run_id = run_id
        self._next_insertion_sequence = 0
        self._items: list[tuple[tuple[int, int, int], ScheduledEvent]] = []

    def schedule(self, spec: ScheduledEventSpec) -> ScheduledEvent:
        insertion_sequence = self._next_insertion_sequence
        self._next_insertion_sequence += 1
        scheduled_event_id = f"{self._run_id}:scheduled-event:{insertion_sequence:08d}"
        event = ScheduledEvent(
            scheduled_event_id=scheduled_event_id,
            run_id=self._run_id,
            due_time=spec.due_time,
            priority=spec.priority,
            insertion_sequence=insertion_sequence,
            event_type=spec.event_type,
            schema_version=spec.schema_version,
            payload=spec.payload,
            correlation_id=spec.correlation_id or scheduled_event_id,
        )
        heapq.heappush(self._items, (event.ordering_key, event))
        return event.detached()

    def pop_next_due(self, through_time: int) -> ScheduledEvent | None:
        if not isinstance(through_time, int) or isinstance(through_time, bool):
            raise ValueError("through_time must be an integer")
        if not self._items or self._items[0][0][0] > through_time:
            return None
        return heapq.heappop(self._items)[1]

    def peek_next_due(self, through_time: int) -> ScheduledEvent | None:
        if not isinstance(through_time, int) or isinstance(through_time, bool):
            raise ValueError("through_time must be an integer")
        if not self._items or self._items[0][0][0] > through_time:
            return None
        return self._items[0][1]

    @property
    def pending(self) -> tuple[ScheduledEvent, ...]:
        return tuple(item[1].detached() for item in sorted(self._items, key=lambda item: item[0]))
