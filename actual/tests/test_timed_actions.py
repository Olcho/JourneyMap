"""Domain-neutral duration contract isolation and engine failure semantics."""

from dataclasses import replace
from typing import cast

import pytest

from journeymap.bootstrap import create_kernel
from journeymap.core.canonical import CanonicalValueError, JsonObject
from journeymap.core.events import EventBus, EventDeliveryError, EventDraft, EventEnvelope
from journeymap.core.handlers import (
    ActionRegistry,
    ActionRequest,
    ActionStatus,
    ActionTiming,
    ActionValidationError,
    ResolutionContext,
    SystemEventRegistry,
    TransitionPlan,
    ValidationContext,
)
from journeymap.core.kernel import ReentrantMutationError, SimulationKernel
from journeymap.core.run import DeterministicRng
from journeymap.core.scheduler import ScheduledEvent, ScheduledEventSpec


def request() -> ActionRequest:
    return ActionRequest("action", "run-default", "actor", "opaque", 0, "TIMED", 1, {"value": 1})


class TimedHandler:
    handler_id = "test.timed.v1"

    def validate(self, request: ActionRequest, context: ValidationContext) -> None:
        request.payload["value"] = 99
        context.state["value"] = 99

    def prepare(self, request: ActionRequest, context: ValidationContext) -> ActionTiming:
        assert request.payload == {"value": 1}
        assert context.state["value"] == 0
        context.state["value"] = 99
        return ActionTiming(10, {"initial": 0})

    def validate_completion(
        self, request: ActionRequest, timing: ActionTiming, context: ValidationContext
    ) -> None:
        assert timing.data == {"initial": 0}
        assert context.simulation_time == 10
        context.state["value"] = 99
        request.payload["value"] = 99
        timing.data["initial"] = 99

    def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
        assert request.payload == {"value": 1}
        assert context.state["value"] != 99
        return TransitionPlan(set_values={"draw": context.rng.next_u64()})


class SystemHandler:
    handler_id = "test.system.v1"

    def validate(self, event: ScheduledEvent, context: ValidationContext) -> None:
        pass

    def resolve(self, event: ScheduledEvent, context: ResolutionContext) -> TransitionPlan:
        draw = context.rng.next_u64()
        if event.payload.get("fail"):
            context.state["value"] = 99
            raise RuntimeError("system defect")
        return TransitionPlan(
            set_values={"value": 1, "system_draw": draw},
            events=(EventDraft("Changed", 1, {}),),
        )


def make_kernel(handler: TimedHandler, bus: EventBus | None = None) -> SimulationKernel:
    actions = ActionRegistry()
    actions.register("TIMED", 1, handler)
    systems = SystemEventRegistry()
    systems.register("SYSTEM", 1, SystemHandler())
    return create_kernel(
        initial_state={"value": 0},
        action_registry=actions,
        system_event_registry=systems,
        event_bus=bus,
    )


def test_timed_contexts_are_isolated_and_resolution_uses_post_system_rng() -> None:
    kernel = make_kernel(TimedHandler())
    kernel.boot()
    kernel.schedule(ScheduledEventSpec(5, 0, "SYSTEM", 1, {}))
    original_request = request()
    result = kernel.submit_action(original_request)
    expected_rng = DeterministicRng(0)
    system_draw = expected_rng.next_u64()
    action_draw = expected_rng.next_u64()
    assert result.status is ActionStatus.SUCCEEDED
    assert kernel.state_snapshot == {"value": 1, "system_draw": system_draw, "draw": action_draw}
    assert kernel.rng_snapshot == expected_rng.snapshot()
    assert original_request.payload == {"value": 1}
    kernel.close()


def audit_snapshot(kernel: SimulationKernel) -> tuple[object, ...]:
    return (
        kernel.simulation_time,
        kernel.state_snapshot,
        kernel.state_digest,
        kernel.rng_snapshot,
        kernel.transition_sequence,
        kernel.events,
        kernel.pending_scheduled_events,
        kernel.system_event_outcomes,
    )


@pytest.mark.parametrize(
    "phase", ["validate", "prepare", "completion", "resolve", "precommit-state", "precommit-event"]
)
def test_fault_injection_preserves_exact_independent_world_progress(phase: str) -> None:
    calls: list[str] = []

    class FaultHandler(TimedHandler):
        def validate(self, request: ActionRequest, context: ValidationContext) -> None:
            calls.append("validate")
            context.state["value"] = 99
            if phase == "validate":
                raise RuntimeError("audit fault")

        def prepare(self, request: ActionRequest, context: ValidationContext) -> ActionTiming:
            calls.append("prepare")
            assert context.state["value"] == 1
            context.state["value"] = 99
            if phase == "prepare":
                raise RuntimeError("audit fault")
            return ActionTiming(10)

        def validate_completion(
            self, request: ActionRequest, timing: ActionTiming, context: ValidationContext
        ) -> None:
            calls.append("completion")
            context.state["value"] = 99
            if phase == "completion":
                raise RuntimeError("audit fault")

        def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
            calls.append("resolve")
            context.rng.next_u64()
            context.state["value"] = 99
            if phase == "resolve":
                raise RuntimeError("audit fault")
            plan = TransitionPlan(
                set_values={"value": 2},
                events=(
                    EventDraft("First", 1, {}),
                    EventDraft("Second", 1, {}),
                ),
            )
            # A malformed handler can mutate its own plan after construction.
            # Even after preparing the first event ID, commit must stay atomic.
            if phase == "precommit-state":
                plan.set_values["value"] = float("nan")
            else:
                plan.events[1].payload["invalid"] = float("nan")
            return plan

    kernel = make_kernel(FaultHandler())
    control = make_kernel(TimedHandler())
    for instance in (kernel, control):
        instance.boot()
        instance.schedule(ScheduledEventSpec(100, 0, "SYSTEM", 1, {}))
        instance.schedule(ScheduledEventSpec(105, 0, "SYSTEM", 1, {}))
        instance.schedule(ScheduledEventSpec(120, 0, "SYSTEM", 1, {}))
    expected_time = 100 if phase in {"validate", "prepare"} else 110
    control.advance_to(expected_time)
    error = CanonicalValueError if phase.startswith("precommit") else RuntimeError
    with pytest.raises(error):
        kernel.submit_action(replace(request(), submitted_at=100))
    assert audit_snapshot(kernel) == audit_snapshot(control)
    assert kernel.action_results == ()
    if phase in {"validate", "prepare", "completion"}:
        assert "resolve" not in calls
    if phase in {"validate", "prepare"}:
        assert "completion" not in calls
    probe = ScheduledEventSpec(120, 1, "SYSTEM", 1, {})
    assert kernel.schedule(probe) == control.schedule(probe)
    # Subsequent explicit advancement consumes each remaining input once.
    kernel.advance_to(120)
    control.advance_to(120)
    assert audit_snapshot(kernel) == audit_snapshot(control)
    kernel.close()
    control.close()


@pytest.mark.parametrize("timed", [False, True])
def test_action_publication_failure_preserves_commit_and_stops_delivery(timed: bool) -> None:
    calls: list[str] = []

    class ImmediateHandler:
        handler_id = "test.publication.v1"

        def validate(self, request: ActionRequest, context: ValidationContext) -> None:
            pass

        def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
            return TransitionPlan(
                set_values={"draw": context.rng.next_u64()},
                events=(
                    EventDraft("First", 1, {}),
                    EventDraft("Second", 1, {}),
                ),
            )

    class DurationHandler(ImmediateHandler):
        def prepare(self, request: ActionRequest, context: ValidationContext) -> ActionTiming:
            return ActionTiming(10)

        def validate_completion(
            self, request: ActionRequest, timing: ActionTiming, context: ValidationContext
        ) -> None:
            pass

    def broken(event: EventEnvelope) -> None:
        calls.append(event.event_type)
        raise RuntimeError("subscriber defect")

    bus = EventBus()
    bus.subscribe(
        event_type=None, priority=0, module_id="test", subscriber_id="broken", subscriber=broken
    )
    actions = ActionRegistry()
    actions.register("TIMED", 1, DurationHandler() if timed else ImmediateHandler())
    kernel = create_kernel(action_registry=actions, event_bus=bus)
    kernel.boot()
    with pytest.raises(EventDeliveryError) as caught:
        kernel.submit_action(request())
    assert caught.value.event_id == "run-default:event:00000001"
    assert calls == ["First"]
    assert [event.event_type for event in kernel.events] == ["First", "Second"]
    assert kernel.transition_sequence == 1
    expected_rng = DeterministicRng(0)
    assert kernel.state_snapshot == {"draw": expected_rng.next_u64()}
    assert kernel.rng_snapshot == expected_rng.snapshot()
    assert kernel.simulation_time == (10 if timed else 0)
    (result,) = kernel.action_results
    assert result.status is ActionStatus.SUCCEEDED
    assert result.emitted_event_ids == tuple(event.event_id for event in kernel.events)
    before = audit_snapshot(kernel)
    assert kernel.advance_to(kernel.simulation_time) == ()
    assert audit_snapshot(kernel) == before
    assert calls == ["First"]
    kernel.close()


@pytest.mark.parametrize("duration", [0, -1, True, 1.5])
def test_invalid_timing_contract_is_a_programming_error(duration: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        ActionTiming(cast(int, duration))


def test_action_timing_detaches_handler_owned_data() -> None:
    data: JsonObject = {"nested": {"value": 1}}
    timing = ActionTiming(1, data)
    data["nested"] = 99
    detached = timing.detached()
    detached.data["nested"] = 100
    assert timing.data == {"nested": {"value": 1}}


@pytest.mark.parametrize("phase", ["prepare", "completion", "resolve"])
def test_timed_handler_cannot_reenter_kernel(phase: str) -> None:
    class ReentrantHandler(TimedHandler):
        def prepare(self, request: ActionRequest, context: ValidationContext) -> ActionTiming:
            if phase == "prepare":
                kernel.advance_to(1)
            return super().prepare(request, context)

        def validate_completion(
            self, request: ActionRequest, timing: ActionTiming, context: ValidationContext
        ) -> None:
            if phase == "completion":
                kernel.submit_action(request)

        def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
            kernel.schedule(ScheduledEventSpec(10, 0, "SYSTEM", 1, {}))
            return TransitionPlan()

    kernel = make_kernel(ReentrantHandler())
    kernel.boot()
    with pytest.raises(ReentrantMutationError):
        kernel.submit_action(request())
    assert kernel.simulation_time == (0 if phase == "prepare" else 10)
    assert kernel.state_snapshot == {"value": 0}
    assert kernel.transition_sequence == 0
    assert kernel.rng_snapshot.draw_count == 0
    assert kernel.pending_scheduled_events == ()
    assert kernel.action_results == ()
    kernel.close()


def test_expected_prepare_rejection_has_no_time_or_ids() -> None:
    class RejectedHandler(TimedHandler):
        def prepare(self, request: ActionRequest, context: ValidationContext) -> ActionTiming:
            raise ActionValidationError("CANNOT_START")

    kernel = make_kernel(RejectedHandler())
    kernel.boot()
    result = kernel.submit_action(request())
    assert result.status is ActionStatus.REJECTED
    assert result.reason_code == "CANNOT_START"
    assert kernel.simulation_time == 0
    assert kernel.transition_sequence == 0
    kernel.close()


def test_system_defect_during_action_preserves_prior_commits_and_pending_input() -> None:
    kernel = make_kernel(TimedHandler())
    kernel.boot()
    kernel.schedule(ScheduledEventSpec(3, 0, "SYSTEM", 1, {}))
    failed = kernel.schedule(ScheduledEventSpec(5, 0, "SYSTEM", 1, {"fail": True}))
    with pytest.raises(RuntimeError, match="system defect"):
        kernel.submit_action(request())
    assert kernel.simulation_time == 3
    assert kernel.pending_scheduled_events == (failed,)
    assert kernel.rng_snapshot.draw_count == 1
    assert kernel.state_snapshot["value"] == 1
    assert kernel.transition_sequence == 1
    assert len(kernel.events) == 1
    assert kernel.action_results == ()
    kernel.close()


def test_system_delivery_error_during_action_stops_at_committed_tick() -> None:
    bus = EventBus()

    def subscriber(event: EventEnvelope) -> None:
        kernel.advance_to(10)

    bus.subscribe(
        event_type=None,
        priority=0,
        module_id="test",
        subscriber_id="reenter",
        subscriber=subscriber,
    )
    kernel = make_kernel(TimedHandler(), bus)
    kernel.boot()
    kernel.schedule(ScheduledEventSpec(5, 0, "SYSTEM", 1, {}))
    with pytest.raises(EventDeliveryError) as caught:
        kernel.submit_action(request())
    assert isinstance(caught.value.__cause__, ReentrantMutationError)
    assert kernel.simulation_time == 5
    assert kernel.pending_scheduled_events == ()
    assert kernel.state_snapshot["value"] == 1
    assert kernel.rng_snapshot.draw_count == 1
    assert kernel.transition_sequence == 1
    assert kernel.action_results == ()
    assert kernel.advance_to(5) == ()
    kernel.close()


def test_completion_resolution_defect_discards_candidate_without_undoing_world() -> None:
    class BrokenHandler(TimedHandler):
        def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
            context.rng.next_u64()
            context.state["value"] = 99
            raise RuntimeError("completion defect")

    kernel = make_kernel(BrokenHandler())
    kernel.boot()
    kernel.schedule(ScheduledEventSpec(5, 0, "SYSTEM", 1, {}))
    with pytest.raises(RuntimeError, match="completion defect"):
        kernel.submit_action(request())
    assert kernel.simulation_time == 10
    assert kernel.state_snapshot["value"] == 1
    assert kernel.rng_snapshot.draw_count == 1
    assert kernel.transition_sequence == 1
    assert len(kernel.events) == 1
    assert kernel.action_results == ()
    assert kernel.pending_scheduled_events == ()
    kernel.close()


def test_same_tick_system_rescheduling_during_duration_obeys_m1_failure_rules() -> None:
    class ReschedulingHandler(SystemHandler):
        def resolve(self, event: ScheduledEvent, context: ResolutionContext) -> TransitionPlan:
            kernel.schedule(ScheduledEventSpec(context.simulation_time, 0, "SYSTEM", 1, {}))
            return super().resolve(event, context)

    actions = ActionRegistry()
    actions.register("TIMED", 1, TimedHandler())
    systems = SystemEventRegistry()
    systems.register("SYSTEM", 1, SystemHandler())
    systems.register("RESCHEDULE", 1, ReschedulingHandler())
    kernel = create_kernel(
        initial_state={"value": 0},
        action_registry=actions,
        system_event_registry=systems,
    )
    kernel.boot()
    kernel.schedule(ScheduledEventSpec(3, 0, "SYSTEM", 1, {}))
    pending = kernel.schedule(ScheduledEventSpec(5, 0, "RESCHEDULE", 1, {}))
    with pytest.raises(ReentrantMutationError):
        kernel.submit_action(request())
    assert kernel.simulation_time == 3
    assert kernel.state_snapshot["value"] == 1
    assert kernel.pending_scheduled_events == (pending,)
    assert kernel.rng_snapshot.draw_count == kernel.transition_sequence == 1
    assert kernel.action_results == ()
    assert len(kernel.events) == len(kernel.system_event_outcomes) == 1
    assert kernel.schedule(ScheduledEventSpec(10, 0, "SYSTEM", 1, {})).insertion_sequence == 2
    kernel.close()
