"""Separated dispatch, atomic mutation, event ordering, and replay tests."""

from collections.abc import Callable
from typing import cast

import pytest

from journeymap.bootstrap import create_kernel
from journeymap.core.canonical import JsonObject, JsonValue, state_digest
from journeymap.core.events import (
    EventBus,
    EventDeliveryError,
    EventDraft,
    EventEnvelope,
    EventSourceKind,
)
from journeymap.core.handlers import (
    ActionRegistry,
    ActionRequest,
    ResolutionContext,
    SystemEventRegistry,
    TransitionPlan,
    ValidationContext,
)
from journeymap.core.kernel import (
    InitialStateMismatchError,
    ReentrantMutationError,
    SimulationKernel,
)
from journeymap.core.replay import ReplayHarness, ReplayInput
from journeymap.core.run import RunManifest
from journeymap.core.scheduler import ScenarioSchedule, ScheduledEvent, ScheduledEventSpec


def required_int(value: JsonValue | None, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


class AddActionHandler:
    @property
    def handler_id(self) -> str:
        return "test.add-action.v1"

    def validate(self, request: ActionRequest, context: ValidationContext) -> None:
        required_int(request.payload.get("amount"), "amount")
        required_int(context.state.get("total", 0), "total")

    def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
        amount = required_int(request.payload.get("amount"), "amount")
        total = required_int(context.state.get("total", 0), "total")
        draw = context.rng.randbelow(10)
        new_total = total + amount + draw
        return TransitionPlan(
            set_values={"total": new_total},
            events=(
                EventDraft("ActionApplied", 1, {"total": new_total}),
                EventDraft("ActionAudit", 1, {"draw": draw}),
            ),
        )


class AddSystemEventHandler:
    @property
    def handler_id(self) -> str:
        return "test.add-system.v1"

    def validate(self, event: ScheduledEvent, context: ValidationContext) -> None:
        required_int(event.payload.get("amount"), "amount")
        required_int(context.state.get("total", 0), "total")

    def resolve(self, event: ScheduledEvent, context: ResolutionContext) -> TransitionPlan:
        amount = required_int(event.payload.get("amount"), "amount")
        total = required_int(context.state.get("total", 0), "total")
        new_total = total + amount
        return TransitionPlan(
            set_values={"total": new_total},
            events=(EventDraft("SystemApplied", 1, {"total": new_total}),),
        )


class FailingActionHandler:
    @property
    def handler_id(self) -> str:
        return "test.failing-action.v1"

    def validate(self, request: ActionRequest, context: ValidationContext) -> None:
        pass

    def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
        context.rng.next_u64()
        context.state["unauthorized"] = True
        raise RuntimeError("resolution failed")


class MutatingActionInputHandler:
    @property
    def handler_id(self) -> str:
        return "test.mutating-action-input.v1"

    def validate(self, request: ActionRequest, context: ValidationContext) -> None:
        request.payload["amount"] = 100

    def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
        amount = required_int(request.payload.get("amount"), "amount")
        request.payload["amount"] = 200
        return TransitionPlan(set_values={"total": amount})


class FailingSystemEventHandler:
    def __init__(self) -> None:
        self.attempts = 0

    @property
    def handler_id(self) -> str:
        return "test.failing-system.v1"

    def validate(self, event: ScheduledEvent, context: ValidationContext) -> None:
        pass

    def resolve(self, event: ScheduledEvent, context: ResolutionContext) -> TransitionPlan:
        self.attempts += 1
        context.rng.next_u64()
        context.state["unauthorized"] = True
        raise RuntimeError("system resolution failed")


class MutatingSystemInputHandler:
    @property
    def handler_id(self) -> str:
        return "test.mutating-system-input.v1"

    def validate(self, event: ScheduledEvent, context: ValidationContext) -> None:
        event.payload["amount"] = 100

    def resolve(self, event: ScheduledEvent, context: ResolutionContext) -> TransitionPlan:
        amount = required_int(event.payload.get("amount"), "amount")
        event.payload["amount"] = 200
        return TransitionPlan(set_values={"total": amount})


class SchedulingSystemEventHandler:
    def __init__(self) -> None:
        self.kernel: SimulationKernel | None = None

    @property
    def handler_id(self) -> str:
        return "test.scheduling-system.v1"

    def validate(self, event: ScheduledEvent, context: ValidationContext) -> None:
        pass

    def resolve(self, event: ScheduledEvent, context: ResolutionContext) -> TransitionPlan:
        if self.kernel is None:
            raise RuntimeError("test kernel was not bound")
        self.kernel.schedule(
            ScheduledEventSpec(
                due_time=context.simulation_time,
                priority=0,
                event_type="TEST_SCHEDULE",
                schema_version=1,
                payload={},
            )
        )
        return TransitionPlan()


def manifest_for(initial_state: JsonObject, *, seed: int = 1234) -> RunManifest:
    return RunManifest(
        run_id="run-replay",
        scenario_id="scenario-test",
        scenario_version="1",
        engine_version="test-engine-1",
        schema_version=1,
        seed=seed,
        start_time=0,
        initial_state_digest=state_digest(initial_state),
    )


def kernel_factory(initial_state: JsonObject) -> Callable[[], SimulationKernel]:
    def factory() -> SimulationKernel:
        actions = ActionRegistry()
        actions.register("TEST_ADD", 1, AddActionHandler())
        system_events = SystemEventRegistry()
        system_events.register("TEST_ADD", 1, AddSystemEventHandler())
        return create_kernel(
            manifest=manifest_for(initial_state),
            initial_state=initial_state,
            action_registry=actions,
            system_event_registry=system_events,
        )

    return factory


def test_manifest_binds_the_exact_initial_state() -> None:
    manifest = manifest_for({"total": 1})

    with pytest.raises(InitialStateMismatchError, match="initial state digest mismatch"):
        create_kernel(manifest=manifest, initial_state={"total": 2})


def test_invalid_advance_tick_is_rejected_before_due_events_are_processed() -> None:
    kernel = kernel_factory({"total": 0})()
    kernel.boot()
    scheduled = kernel.schedule(ScheduledEventSpec(0, 0, "TEST_ADD", 1, {"amount": 2}))

    with pytest.raises(ValueError, match="target_time must be an integer"):
        kernel.advance_to(cast(int, float("nan")))

    assert kernel.simulation_time == 0
    assert kernel.state_snapshot == {"total": 0}
    assert kernel.pending_scheduled_events == (scheduled,)
    kernel.close()


def test_action_request_requires_an_opaque_observation_reference() -> None:
    with pytest.raises(ValueError, match="identity fields"):
        ActionRequest(
            action_request_id="action-1",
            run_id="run-replay",
            actor_id="actor-1",
            based_on_observation_id="",
            submitted_at=0,
            action_type="TEST_ADD",
            schema_version=1,
            payload={},
        )


def test_action_and_system_event_use_separate_dispatch_paths() -> None:
    kernel = kernel_factory({"total": 0})()
    kernel.boot()
    kernel.schedule(ScheduledEventSpec(0, 0, "TEST_ADD", 1, {"amount": 2}))

    system_outcomes = kernel.advance_to(0)
    action_result = kernel.submit_action(
        ActionRequest(
            action_request_id="action-1",
            run_id="run-replay",
            actor_id="actor-1",
            based_on_observation_id="observation-1",
            submitted_at=0,
            action_type="TEST_ADD",
            schema_version=1,
            payload={"amount": 3},
        )
    )

    assert system_outcomes[0].handler_id == "test.add-system.v1"
    assert action_result.handler_id == "test.add-action.v1"
    assert len(kernel.system_event_outcomes) == 1
    assert len(kernel.action_results) == 1
    assert [event.source_kind for event in kernel.events] == [
        EventSourceKind.SCHEDULED_EVENT,
        EventSourceKind.ACTION,
        EventSourceKind.ACTION,
    ]
    kernel.close()


def test_failed_resolution_cannot_mutate_state_rng_or_event_log() -> None:
    actions = ActionRegistry()
    actions.register("TEST_FAIL", 1, FailingActionHandler())
    actions.register("TEST_ADD", 1, AddActionHandler())
    initial_state: JsonObject = {"total": 0}
    kernel = create_kernel(
        manifest=manifest_for(initial_state),
        initial_state=initial_state,
        action_registry=actions,
    )
    kernel.boot()
    before = (
        kernel.state_snapshot,
        kernel.state_digest,
        kernel.rng_snapshot,
        kernel.events,
        kernel.transition_sequence,
        kernel.pending_scheduled_events,
        kernel.action_results,
        kernel.system_event_outcomes,
        kernel.simulation_time,
    )

    with pytest.raises(RuntimeError, match="resolution failed"):
        kernel.submit_action(
            ActionRequest(
                action_request_id="action-fail",
                run_id="run-replay",
                actor_id="actor-1",
                based_on_observation_id="observation-fail",
                submitted_at=0,
                action_type="TEST_FAIL",
                schema_version=1,
                payload={},
            )
        )

    assert (
        kernel.state_snapshot,
        kernel.state_digest,
        kernel.rng_snapshot,
        kernel.events,
        kernel.transition_sequence,
        kernel.pending_scheduled_events,
        kernel.action_results,
        kernel.system_event_outcomes,
        kernel.simulation_time,
    ) == before

    result = kernel.submit_action(
        ActionRequest(
            action_request_id="action-after-failure",
            run_id="run-replay",
            actor_id="actor-1",
            based_on_observation_id="observation-after-failure",
            submitted_at=0,
            action_type="TEST_ADD",
            schema_version=1,
            payload={"amount": 1},
        )
    )
    assert result.transition.transition_id == "run-replay:transition:00000001"
    assert result.emitted_event_ids == (
        "run-replay:event:00000001",
        "run-replay:event:00000002",
    )
    assert kernel.state_snapshot == {"total": 6}
    assert kernel.rng_snapshot.draw_count == 1
    kernel.close()


def test_handler_cannot_mutate_the_recorded_action_request() -> None:
    actions = ActionRegistry()
    actions.register("TEST_MUTATE_INPUT", 1, MutatingActionInputHandler())
    initial_state: JsonObject = {"total": 0}
    kernel = create_kernel(
        manifest=manifest_for(initial_state),
        initial_state=initial_state,
        action_registry=actions,
    )
    request = ActionRequest(
        action_request_id="action-input",
        run_id="run-replay",
        actor_id="actor-1",
        based_on_observation_id="observation-input",
        submitted_at=0,
        action_type="TEST_MUTATE_INPUT",
        schema_version=1,
        payload={"amount": 1},
    )
    kernel.boot()

    kernel.submit_action(request)

    assert request.payload == {"amount": 1}
    assert kernel.state_snapshot == {"total": 1}
    kernel.close()


def test_failed_system_resolution_does_not_consume_the_scheduled_input() -> None:
    actions = ActionRegistry()
    actions.register("TEST_ADD", 1, AddActionHandler())
    system_events = SystemEventRegistry()
    handler = FailingSystemEventHandler()
    system_events.register("TEST_FAIL", 1, handler)
    initial_state: JsonObject = {"total": 0}
    kernel = create_kernel(
        manifest=manifest_for(initial_state),
        initial_state=initial_state,
        action_registry=actions,
        system_event_registry=system_events,
    )
    kernel.boot()
    scheduled = kernel.schedule(ScheduledEventSpec(2, 0, "TEST_FAIL", 1, {}))
    before = (
        kernel.state_digest,
        kernel.rng_snapshot,
        kernel.events,
        kernel.transition_sequence,
        kernel.action_results,
        kernel.system_event_outcomes,
        kernel.simulation_time,
    )

    with pytest.raises(RuntimeError, match="system resolution failed"):
        kernel.advance_to(2)

    assert (
        kernel.state_digest,
        kernel.rng_snapshot,
        kernel.events,
        kernel.transition_sequence,
        kernel.action_results,
        kernel.system_event_outcomes,
        kernel.simulation_time,
    ) == before
    assert kernel.pending_scheduled_events == (scheduled,)
    assert kernel.simulation_time == 0
    assert handler.attempts == 1

    with pytest.raises(RuntimeError, match="system resolution failed"):
        kernel.advance_to(2)

    assert handler.attempts == 2
    assert kernel.simulation_time == 0
    result = kernel.submit_action(
        ActionRequest(
            action_request_id="action-after-system-failure",
            run_id="run-replay",
            actor_id="actor-1",
            based_on_observation_id="observation-after-system-failure",
            submitted_at=0,
            action_type="TEST_ADD",
            schema_version=1,
            payload={"amount": 1},
        )
    )
    assert result.transition.transition_id == "run-replay:transition:00000001"
    assert result.emitted_event_ids == (
        "run-replay:event:00000001",
        "run-replay:event:00000002",
    )
    later = kernel.schedule(ScheduledEventSpec(3, 0, "TEST_FAIL", 1, {}))
    assert later.scheduled_event_id == "run-replay:scheduled-event:00000001"
    kernel.close()


def test_handler_cannot_mutate_the_recorded_scheduled_input() -> None:
    system_events = SystemEventRegistry()
    system_events.register("TEST_MUTATE_INPUT", 1, MutatingSystemInputHandler())
    initial_state: JsonObject = {"total": 0}
    kernel = create_kernel(
        manifest=manifest_for(initial_state),
        initial_state=initial_state,
        system_event_registry=system_events,
    )
    kernel.boot()
    scheduled = kernel.schedule(ScheduledEventSpec(0, 0, "TEST_MUTATE_INPUT", 1, {"amount": 1}))

    kernel.advance_to(0)

    assert scheduled.payload == {"amount": 1}
    assert kernel.state_snapshot == {"total": 1}
    kernel.close()


def test_system_handler_cannot_schedule_reentrantly_at_the_same_tick() -> None:
    handler = SchedulingSystemEventHandler()
    system_events = SystemEventRegistry()
    system_events.register("TEST_SCHEDULE", 1, handler)
    initial_state: JsonObject = {"total": 0}
    kernel = create_kernel(
        manifest=manifest_for(initial_state),
        initial_state=initial_state,
        system_event_registry=system_events,
    )
    handler.kernel = kernel
    kernel.boot()
    scheduled = kernel.schedule(ScheduledEventSpec(0, 0, "TEST_SCHEDULE", 1, {}))

    with pytest.raises(ReentrantMutationError, match="during handler or subscriber"):
        kernel.advance_to(0)

    assert kernel.state_snapshot == initial_state
    assert kernel.rng_snapshot.draw_count == 0
    assert kernel.events == ()
    assert kernel.transition_sequence == 0
    assert kernel.system_event_outcomes == ()
    assert kernel.pending_scheduled_events == (scheduled,)
    next_event = kernel.schedule(ScheduledEventSpec(0, 1, "TEST_SCHEDULE", 1, {}))
    assert next_event.scheduled_event_id == "run-replay:scheduled-event:00000001"
    kernel.close()


def test_subscriber_failure_is_reported_after_system_transition_commit() -> None:
    calls: list[str] = []
    bus = EventBus()
    kernel_ref: SimulationKernel | None = None

    def failing_subscriber(_event: EventEnvelope) -> None:
        calls.append("failing")
        if kernel_ref is None:
            raise RuntimeError("test kernel was not bound")
        kernel_ref.schedule(ScheduledEventSpec(0, 0, "TEST_ADD", 1, {"amount": 9}))

    def later_subscriber(_event: EventEnvelope) -> None:
        calls.append("later")

    bus.subscribe(
        event_type="SystemApplied",
        priority=0,
        module_id="alpha",
        subscriber_id="failing",
        subscriber=failing_subscriber,
    )
    bus.subscribe(
        event_type="SystemApplied",
        priority=1,
        module_id="beta",
        subscriber_id="later",
        subscriber=later_subscriber,
    )
    system_events = SystemEventRegistry()
    system_events.register("TEST_ADD", 1, AddSystemEventHandler())
    initial_state: JsonObject = {"total": 0}
    kernel = create_kernel(
        manifest=manifest_for(initial_state),
        initial_state=initial_state,
        system_event_registry=system_events,
        event_bus=bus,
    )
    kernel_ref = kernel
    kernel.boot()
    kernel.schedule(ScheduledEventSpec(0, 0, "TEST_ADD", 1, {"amount": 2}))

    with pytest.raises(EventDeliveryError) as caught:
        kernel.advance_to(0)

    assert caught.value.event_id == "run-replay:event:00000001"
    assert caught.value.subscriber_key == (0, "alpha", "failing")
    assert isinstance(caught.value.__cause__, ReentrantMutationError)
    assert calls == ["failing"]
    assert kernel.state_snapshot == {"total": 2}
    assert kernel.transition_sequence == 1
    assert len(kernel.system_event_outcomes) == 1
    assert [event.event_id for event in kernel.events] == ["run-replay:event:00000001"]
    assert kernel.pending_scheduled_events == ()
    assert kernel.advance_to(0) == ()
    next_event = kernel.schedule(ScheduledEventSpec(0, 0, "TEST_ADD", 1, {"amount": 3}))
    assert next_event.scheduled_event_id == "run-replay:scheduled-event:00000001"
    kernel.close()


def test_replay_repeats_handler_results_event_order_and_digest() -> None:
    initial_state: JsonObject = {"total": 0}
    replay_input = ReplayInput(
        schedule=ScenarioSchedule(
            scenario_id="scenario-test",
            scenario_version="1",
            events=(
                ScheduledEventSpec(5, 10, "TEST_ADD", 1, {"amount": 2}),
                ScheduledEventSpec(5, 0, "TEST_ADD", 1, {"amount": 1}),
            ),
        ),
        actions=(
            ActionRequest(
                "action-1",
                "run-replay",
                "actor-1",
                "observation-1",
                5,
                "TEST_ADD",
                1,
                {"amount": 3},
            ),
            ActionRequest(
                "action-2",
                "run-replay",
                "actor-1",
                "observation-2",
                7,
                "TEST_ADD",
                1,
                {"amount": 4},
            ),
        ),
        advance_to=9,
    )
    harness = ReplayHarness(kernel_factory(initial_state))

    first = harness.run(replay_input)
    second = harness.run(replay_input)

    assert first == second
    assert [outcome.scheduled_event_id for outcome in first.system_event_outcomes] == [
        "run-replay:scheduled-event:00000001",
        "run-replay:scheduled-event:00000000",
    ]
    assert [event.event_type for event in first.events] == [
        "SystemApplied",
        "SystemApplied",
        "ActionApplied",
        "ActionAudit",
        "ActionApplied",
        "ActionAudit",
    ]
    assert [event.event_sequence for event in first.events] == list(range(1, 7))
    assert [event.event_id for event in first.events] == [
        f"run-replay:event:{sequence:08d}" for sequence in range(1, 7)
    ]
    assert [event.transition_id for event in first.events] == [
        "run-replay:transition:00000001",
        "run-replay:transition:00000002",
        "run-replay:transition:00000003",
        "run-replay:transition:00000003",
        "run-replay:transition:00000004",
        "run-replay:transition:00000004",
    ]
    assert [result.transition.sequence for result in first.action_results] == [3, 4]
    assert [result.transition.transition_id for result in first.action_results] == [
        "run-replay:transition:00000003",
        "run-replay:transition:00000004",
    ]
    assert first.final_state == {"total": 19}
    assert first.final_state_digest == (
        "e6b168b4f7f29e13fa8d1ae18fd32d4485993c948d845f53dcbccbdd783bb7c5"
    )
    assert first.final_state_digest == state_digest(first.final_state)
    assert first.final_simulation_time == 9
    assert first.rng_draw_count == 2


def test_replay_rejects_a_different_schedule_version() -> None:
    initial_state: JsonObject = {"total": 0}
    replay_input = ReplayInput(
        schedule=ScenarioSchedule("scenario-test", "2", ()),
        actions=(),
        advance_to=0,
    )

    with pytest.raises(ValueError, match="schedule does not match"):
        ReplayHarness(kernel_factory(initial_state)).run(replay_input)
