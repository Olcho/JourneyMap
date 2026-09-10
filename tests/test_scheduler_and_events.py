"""Deterministic scheduler and synchronous EventBus tests."""

from collections.abc import Iterable
from typing import cast

import pytest

from journeymap.core.events import (
    EventBus,
    EventEnvelope,
    EventSourceKind,
    EventSubscriptionError,
)
from journeymap.core.scheduler import DeterministicScheduler, ScheduledEventSpec


def scheduled_spec(*, due_time: int, priority: int, label: str) -> ScheduledEventSpec:
    return ScheduledEventSpec(
        due_time=due_time,
        priority=priority,
        event_type="TEST_SYSTEM_INPUT",
        schema_version=1,
        payload={"label": label},
    )


def test_scheduler_orders_by_time_priority_and_insertion_sequence() -> None:
    scheduler = DeterministicScheduler("run-order")
    scheduler.schedule(scheduled_spec(due_time=5, priority=10, label="inserted-first"))
    scheduler.schedule(scheduled_spec(due_time=5, priority=0, label="higher-priority"))
    scheduler.schedule(scheduled_spec(due_time=5, priority=10, label="inserted-last"))
    scheduler.schedule(scheduled_spec(due_time=4, priority=100, label="earlier"))

    popped = []
    while (event := scheduler.pop_next_due(5)) is not None:
        popped.append(event.payload["label"])

    assert popped == ["earlier", "higher-priority", "inserted-first", "inserted-last"]


def test_scheduler_does_not_release_future_inputs() -> None:
    scheduler = DeterministicScheduler("run-future")
    scheduler.schedule(scheduled_spec(due_time=6, priority=0, label="future"))

    assert scheduler.pop_next_due(5) is None
    assert len(scheduler.pending) == 1


@pytest.mark.parametrize(
    ("due_time", "priority"),
    [(1.5, 0), (0, 1.5), (True, 0), (0, False)],
)
def test_scheduler_specs_require_integer_ordering_fields(
    due_time: object,
    priority: object,
) -> None:
    with pytest.raises(ValueError, match="integer"):
        ScheduledEventSpec(
            due_time=cast(int, due_time),
            priority=cast(int, priority),
            event_type="TEST_SYSTEM_INPUT",
            schema_version=1,
            payload={},
        )


def event_envelope() -> EventEnvelope:
    return EventEnvelope(
        event_id="run:event:00000001",
        run_id="run",
        event_sequence=1,
        simulation_time=3,
        event_type="TEST_EVENT",
        schema_version=1,
        source_kind=EventSourceKind.ACTION,
        source_ref="action-1",
        transition_id="transition-1",
        causation_id="transition-1",
        correlation_id="action-1",
        payload={},
    )


def subscriber_order(registration_order: Iterable[str]) -> list[str]:
    calls: list[str] = []
    definitions = {
        "late": (20, "alpha", "late"),
        "module-b": (10, "beta", "first"),
        "module-a": (10, "alpha", "second"),
    }
    bus = EventBus()
    for name in registration_order:
        priority, module_id, subscriber_id = definitions[name]

        def subscriber(_event: EventEnvelope, *, label: str = name) -> None:
            calls.append(label)

        bus.subscribe(
            event_type="TEST_EVENT",
            priority=priority,
            module_id=module_id,
            subscriber_id=subscriber_id,
            subscriber=subscriber,
        )

    bus.publish(event_envelope())
    return calls


def test_event_bus_is_synchronous_and_independent_of_registration_order() -> None:
    expected = ["module-a", "module-b", "late"]

    assert subscriber_order(("late", "module-b", "module-a")) == expected
    assert subscriber_order(("module-a", "late", "module-b")) == expected


def test_event_bus_rejects_duplicate_ordering_keys() -> None:
    bus = EventBus()

    def subscriber(_event: EventEnvelope) -> None:
        pass

    bus.subscribe(
        event_type=None,
        priority=0,
        module_id="module",
        subscriber_id="subscriber",
        subscriber=subscriber,
    )

    with pytest.raises(EventSubscriptionError, match="duplicate subscriber ordering key"):
        bus.subscribe(
            event_type="TEST_EVENT",
            priority=0,
            module_id="module",
            subscriber_id="subscriber",
            subscriber=subscriber,
        )


def test_event_bus_rejects_non_integer_priority() -> None:
    bus = EventBus()

    with pytest.raises(EventSubscriptionError, match="priority must be an integer"):
        bus.subscribe(
            event_type=None,
            priority=cast(int, float("nan")),
            module_id="module",
            subscriber_id="subscriber",
            subscriber=lambda _event: None,
        )


def test_subscriber_receives_a_detached_event_payload() -> None:
    event = event_envelope()
    event.payload["nested"] = {"value": 1}
    bus = EventBus()

    def subscriber(delivered: EventEnvelope) -> None:
        nested = delivered.payload["nested"]
        assert isinstance(nested, dict)
        nested["value"] = 2

    bus.subscribe(
        event_type="TEST_EVENT",
        priority=0,
        module_id="module",
        subscriber_id="subscriber",
        subscriber=subscriber,
    )

    bus.publish(event)

    assert event.payload == {"nested": {"value": 1}}
