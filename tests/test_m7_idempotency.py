"""Live attempts, resolved engine inputs and safe retry are distinct contracts."""

from dataclasses import asdict, replace

import pytest
from test_game_and_research import execution, future, intent, make_kernel

from journeymap.bootstrap import create_application
from journeymap.core.actions import WaitHandler
from journeymap.core.controller import GameSubmissionError
from journeymap.core.events import EventBus, EventEnvelope
from journeymap.core.handlers import ActionRequest, ActionStatus, ResolutionContext, TransitionPlan
from journeymap.core.replay import ReplayHarness, ReplayInput
from journeymap.core.scheduler import ScenarioSchedule


@pytest.mark.parametrize("status", list(ActionStatus))
def test_exact_retry_returns_original_receipt_without_any_engine_consumption(status: ActionStatus) -> None:
    kernel = make_kernel()
    kernel.boot()
    try:
        app, research = create_application(kernel)
        game = app.game_for("a")
        observation = game.observe()
        if status is ActionStatus.FAILED:
            kernel.schedule(future(2))
            request = intent(observation, action="MOVE", payload={"route_id": "xy"})
        else:
            request = intent(observation, payload={"duration": 0 if status is ActionStatus.REJECTED else 1})
        first = game.submit(request)
        assert first.status is status
        before = execution(kernel)
        for _ in range(3):
            assert game.submit(request.detached()) == first
            assert execution(kernel) == before
        assert len(research.action_traces) == 4
        assert research.action_traces[0].engine_submitted
        assert all(
            not trace.engine_submitted and trace.result is None and trace.retry_of_attempt == 1
            for trace in research.action_traces[1:]
        )
    finally:
        kernel.close()


@pytest.mark.parametrize("field,value", [
    ("payload", {"duration": 2}), ("actor_id", "b"), ("run_id", "other"),
    ("based_on_observation_id", "other"), ("submitted_at", 1),
    ("action_type", "MOVE"), ("schema_version", 2), ("correlation_id", "changed"),
])
def test_conflicting_id_never_executes_or_replaces_original(field: str, value: object) -> None:
    kernel = make_kernel()
    kernel.boot()
    try:
        app, research = create_application(kernel)
        game = app.game_for("a")
        request = intent(game.observe())
        receipt = game.submit(request)
        before = execution(kernel)
        conflict = game.submit(replace(request, **{field: value}))
        assert conflict.status is ActionStatus.REJECTED
        assert conflict.reason_code == "REQUEST_ID_CONFLICT"
        assert game.submit(request) == receipt
        assert execution(kernel) == before
        assert research.action_traces[1].result is None
    finally:
        kernel.close()


def test_canonical_object_order_and_detachment_do_not_change_identity() -> None:
    kernel = make_kernel()
    kernel.boot()
    try:
        app, _ = create_application(kernel)
        game = app.game_for("a")
        request = intent(game.observe(), action="UNKNOWN", payload={"b": [1, {"d": 2, "c": 3}], "a": "한"})
        receipt = game.submit(request)
        before = execution(kernel)
        retry = replace(request, payload={"a": "한", "b": [1, {"c": 3, "d": 2}]})
        assert game.submit(retry) == receipt
        request.payload.clear()
        assert game.submit(request).reason_code == "REQUEST_ID_CONFLICT"
        assert game.submit(retry) == receipt
        assert execution(kernel) == before
    finally:
        kernel.close()


@pytest.mark.parametrize("denial", ["wrong_actor", "wrong_run", "time", "observation", "unknown_version"])
def test_boundary_denials_are_final_even_after_time_changes(denial: str) -> None:
    kernel = make_kernel()
    kernel.boot()
    try:
        app, _ = create_application(kernel)
        game = app.game_for("a")
        request = intent(game.observe())
        changes: dict[str, object] = {
            "wrong_actor": {"actor_id": "b"}, "wrong_run": {"run_id": "wrong"},
            "time": {"submitted_at": 1}, "observation": {"based_on_observation_id": "missing"},
            "unknown_version": {"schema_version": 2},
        }
        update = changes[denial]
        assert isinstance(update, dict)
        request = replace(request, **update)
        receipt = game.submit(request)
        assert receipt.status is ActionStatus.REJECTED
        kernel.advance_to(1)
        before = execution(kernel)
        assert game.submit(request) == receipt
        assert execution(kernel) == before
    finally:
        kernel.close()


def test_shared_run_id_space_cannot_return_another_ports_receipt() -> None:
    kernel = make_kernel()
    kernel.boot()
    try:
        app, _ = create_application(kernel)
        a, b = app.game_for("a"), app.game_for("b")
        request = intent(a.observe())
        first = a.submit(request)
        before = execution(kernel)
        for forged in (request, replace(intent(b.observe()), action_request_id=request.action_request_id)):
            receipt = b.submit(forged)
            assert receipt.actor_id == "b" and receipt.reason_code == "REQUEST_ID_CONFLICT"
        assert app.game_for("a").submit(request) == first
        assert execution(kernel) == before
    finally:
        kernel.close()


def test_post_commit_delivery_error_retry_recovers_only_current_committed_result() -> None:
    bus = EventBus()
    calls: list[str] = []

    def broken(event: EventEnvelope) -> None:
        calls.append(event.event_id)
        raise RuntimeError("PRIVATE")

    bus.subscribe(event_type=None, priority=0, module_id="test", subscriber_id="broken", subscriber=broken)
    kernel = make_kernel(bus=bus)
    kernel.boot()
    try:
        app, research = create_application(kernel)
        game = app.game_for("a")
        game.submit(replace(intent(game.observe()), action_request_id="prior"))
        request = intent(game.observe(), action="MOVE", payload={"route_id": "xy"})
        with pytest.raises(GameSubmissionError, match="^ENGINE_ERROR$"):
            game.submit(request)
        original = research.action_traces[-1]
        assert original.result == kernel.action_results[-1]
        assert original.controller_result is None and original.error_type == "EventDeliveryError"
        before = execution(kernel)
        receipt = game.submit(request)
        assert asdict(receipt) == {
            "action_request_id": "request-1", "run_id": "run-m3", "actor_id": "a",
            "status": "SUCCEEDED", "reason_code": None, "started_at": 1, "resolved_at": 5,
            "schema_version": 1,
        }
        assert game.submit(request) == receipt
        assert execution(kernel) == before and len(calls) == 1
        assert research.action_traces[-1].retry_of_attempt == 2
    finally:
        kernel.close()


@pytest.mark.parametrize("system_commit", [False, True])
def test_indeterminate_engine_error_consumes_id_without_reexecuting(system_commit: bool) -> None:
    calls: list[int] = []

    class Broken(WaitHandler):
        def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
            calls.append(context.simulation_time)
            context.rng.next_u64()
            raise RuntimeError("PRIVATE")

    kernel = make_kernel(action_handler=Broken())
    kernel.boot()
    try:
        if system_commit:
            kernel.schedule(future(1))
        app, research = create_application(kernel)
        game = app.game_for("a")
        request = intent(game.observe())
        with pytest.raises(GameSubmissionError, match="^ENGINE_ERROR$"):
            game.submit(request)
        before = execution(kernel)
        assert kernel.action_results == ()
        assert len(kernel.system_event_outcomes) == int(system_commit)
        for _ in range(2):
            with pytest.raises(GameSubmissionError, match="^ENGINE_ERROR$"):
                game.submit(request)
            assert execution(kernel) == before
        assert calls == [1]
        assert research.action_traces[-1].error_type == "IndeterminateRequest"
        assert game.submit(replace(request, payload={"duration": 2})).reason_code == "REQUEST_ID_CONFLICT"
    finally:
        kernel.close()


def test_system_delivery_error_without_action_result_is_indeterminate() -> None:
    bus = EventBus()

    def broken(event: EventEnvelope) -> None:
        raise ValueError("PRIVATE")

    bus.subscribe(event_type=None, priority=0, module_id="t", subscriber_id="t", subscriber=broken)
    kernel = make_kernel(bus=bus)
    kernel.boot()
    try:
        kernel.schedule(future(1))
        app, _ = create_application(kernel)
        game = app.game_for("a")
        request = intent(game.observe())
        with pytest.raises(GameSubmissionError, match="^ENGINE_ERROR$"):
            game.submit(request)
        before = execution(kernel)
        with pytest.raises(GameSubmissionError, match="^ENGINE_ERROR$"):
            game.submit(request)
        assert execution(kernel) == before and kernel.action_results == ()
        assert len(kernel.events) == 1
    finally:
        kernel.close()


def test_live_retries_are_not_replayed_as_new_engine_inputs() -> None:
    kernel = make_kernel()
    kernel.boot()
    try:
        schedule = ScenarioSchedule("m3-fixture", "1", (future(2),))
        kernel.schedule(schedule.events[0])
        app, research = create_application(kernel)
        game = app.game_for("a")
        for i, (action, payload) in enumerate((
            ("WAIT", {"duration": 0}), ("MOVE", {"route_id": "xy"}), ("WAIT", {"duration": 1}),
        )):
            request = replace(intent(game.observe(), action=action, payload=payload), action_request_id=f"r{i}")
            receipt = game.submit(request)
            assert game.submit(request) == receipt
            assert game.submit(replace(request, correlation_id="conflict")).reason_code == "REQUEST_ID_CONFLICT"
        requests = tuple(t.request for t in research.action_traces if t.engine_submitted and t.result is not None)
        assert len(requests) == 3 and len(research.action_traces) == 9
        inputs = ReplayInput(schedule, requests, kernel.simulation_time)
        first = ReplayHarness(make_kernel).run(inputs)
        assert first == ReplayHarness(make_kernel).run(inputs)
        assert first.action_results == kernel.action_results
        assert first.events == kernel.events and first.system_event_outcomes == kernel.system_event_outcomes
        assert first.final_state == kernel.state_snapshot
        assert first.final_state_digest == kernel.state_digest
        assert first.final_simulation_time == kernel.simulation_time
        assert first.rng_draw_count == kernel.rng_snapshot.draw_count
    finally:
        kernel.close()
