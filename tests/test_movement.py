"""M2 spatial ownership, timed completion, atomicity, and replay contracts."""

from collections.abc import Callable
from dataclasses import fields
from typing import cast

import pytest

from journeymap.bootstrap import create_kernel
from journeymap.core.actions import WaitHandler
from journeymap.core.canonical import JsonObject, JsonValue, state_digest
from journeymap.core.entities import Entity, entity_state, get_entity
from journeymap.core.events import EventDraft, EventSourceKind
from journeymap.core.handlers import (
    ActionRegistry,
    ActionRequest,
    ActionStatus,
    ResolutionContext,
    SystemEventRegistry,
    TransitionPlan,
    ValidationContext,
)
from journeymap.core.kernel import SimulationKernel
from journeymap.core.replay import ReplayHarness, ReplayInput
from journeymap.core.run import DeterministicRng, RunManifest
from journeymap.core.scheduler import ScenarioSchedule, ScheduledEvent, ScheduledEventSpec
from journeymap.modules.movement import (
    ActorPosition,
    Location,
    MovementModule,
    Route,
    movement_state,
)


def world() -> JsonObject:
    return {
        "entities": entity_state((Entity("actor", "person"), Entity("other", "person"))),
        "movement": movement_state(
            locations=(Location("a"), Location("b"), Location("c")),
            routes=(Route("ab", "a", "b", 10), Route("ba", "b", "a", 4)),
            positions=(ActorPosition("actor", "a"), ActorPosition("other", "c")),
        ),
        "unrelated": {"value": 1},
    }


def obj(value: JsonValue) -> JsonObject:
    assert isinstance(value, dict)
    return value


def table(state: JsonObject, name: str) -> JsonObject:
    return obj(obj(state["movement"])[name])


def request(
    action_type: str = "MOVE",
    payload: JsonObject | None = None,
    *,
    tick: int = 0,
    actor: str = "actor",
    request_id: str = "action-1",
) -> ActionRequest:
    return ActionRequest(
        request_id,
        "run-m2",
        actor,
        "opaque-observation",
        tick,
        action_type,
        1,
        {"route_id": "ab"} if payload is None else payload,
        "correlation-m2",
    )


class ChangeWorldHandler:
    """Test-only system mutation; no production closure/bridge scenario."""

    handler_id = "test.change-world.v1"

    def validate(self, event: ScheduledEvent, context: ValidationContext) -> None:
        assert event.payload.get("change") in {
            "close",
            "open",
            "cost",
            "destination",
            "position",
            "entity",
            "location",
            "marker",
        }

    def resolve(self, event: ScheduledEvent, context: ResolutionContext) -> TransitionPlan:
        state = context.state
        change = event.payload["change"]
        route = obj(table(state, "routes").get("ab", {}))
        if change == "close":
            route["passable"] = False
        elif change == "open":
            route["passable"] = True
        elif change == "cost":
            route["traversal_cost"] = event.payload["value"]
        elif change == "destination":
            route["destination"] = "c"
        elif change == "position":
            table(state, "positions")["actor"] = ActorPosition("actor", "c").to_json()
        elif change == "entity":
            del obj(state["entities"])["actor"]
        elif change == "location":
            del table(state, "locations")["b"]
        else:
            obj(state["unrelated"])["value"] = event.payload["value"]
        draw = context.rng.next_u64()
        return TransitionPlan(
            set_values=state,
            events=(EventDraft("WorldChanged", 1, {"change": change, "draw": draw}),),
        )


def factory(initial: JsonObject | None = None) -> SimulationKernel:
    state = world() if initial is None else initial
    actions = ActionRegistry()
    movement = MovementModule()
    movement.register_actions(actions)
    actions.register("WAIT", 1, WaitHandler())
    systems = SystemEventRegistry()
    systems.register("CHANGE", 1, ChangeWorldHandler())
    return create_kernel(
        initial_state=state,
        manifest=RunManifest(
            "run-m2", "test-m2", "1", "test-engine-m2", 1, 1234, 0, state_digest(state)
        ),
        modules=(movement,),
        action_registry=actions,
        system_event_registry=systems,
    )


@pytest.fixture
def kernel() -> SimulationKernel:
    return factory()


def change(
    tick: int, kind: str, *, priority: int = 0, value: JsonValue = None
) -> ScheduledEventSpec:
    return ScheduledEventSpec(tick, priority, "CHANGE", 1, {"change": kind, "value": value})


def execution_state(kernel: SimulationKernel) -> tuple[object, ...]:
    """Research-only action results are intentionally excluded from atomicity."""
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


def test_minimal_identity_and_spatial_models() -> None:
    assert [field.name for field in fields(Entity)] == ["entity_id", "entity_type"]
    state = world()
    assert get_entity(state, "actor") == Entity("actor", "person")
    assert get_entity(state, "absent") is None
    assert table(state, "locations")["a"] == {"location_id": "a"}
    assert table(state, "routes")["ab"] == {
        "route_id": "ab",
        "origin": "a",
        "destination": "b",
        "traversal_cost": 10,
        "passable": True,
    }
    assert table(state, "positions")["actor"] == {"actor_id": "actor", "location_id": "a"}
    assert "position" not in obj(obj(state["entities"])["actor"])


@pytest.mark.parametrize("cost", [0, -1, True, 1.5, "10", None])
def test_route_rejects_invalid_cost_at_construction(cost: object) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        Route("ab", "a", "b", cast(int, cost))


@pytest.mark.parametrize(
    "build",
    [
        lambda: Entity("", "person"),
        lambda: Entity("actor", ""),
        lambda: Location(""),
        lambda: ActorPosition("", "a"),
        lambda: ActorPosition("actor", ""),
        lambda: Route("ab", "", "b", 1),
        lambda: Route("ab", "a", "b", 1, cast(bool, 1)),
        lambda: entity_state((Entity("a", "person"), Entity("a", "person"))),
    ],
)
def test_invalid_model_identity_is_rejected(build: Callable[[], object]) -> None:
    with pytest.raises(ValueError):
        build()


@pytest.mark.parametrize(
    ("locations", "routes", "positions"),
    [
        ((Location("a"), Location("a")), (), ()),
        ((Location("a"),), (Route("ab", "a", "b", 1),), ()),
        ((Location("a"),), (), (ActorPosition("actor", "b"),)),
        ((Location("a"),), (Route("aa", "a", "a", 1), Route("aa", "a", "a", 1)), ()),
        ((Location("a"),), (), (ActorPosition("actor", "a"), ActorPosition("actor", "a"))),
    ],
)
def test_spatial_builder_rejects_duplicate_ids_and_missing_locations(
    locations: tuple[Location, ...], routes: tuple[Route, ...], positions: tuple[ActorPosition, ...]
) -> None:
    with pytest.raises(ValueError):
        movement_state(locations=locations, routes=routes, positions=positions)


def test_valid_move_and_explicit_reverse_route(kernel: SimulationKernel) -> None:
    kernel.boot()
    before_entities = kernel.state_snapshot["entities"]
    first = kernel.submit_action(request())
    assert first.status is ActionStatus.SUCCEEDED
    assert first.reason_code is None
    assert (first.started_at, first.resolved_at, kernel.simulation_time) == (0, 10, 10)
    assert (
        table(kernel.state_snapshot, "positions")["actor"] == ActorPosition("actor", "b").to_json()
    )
    assert kernel.state_snapshot["entities"] == before_entities
    assert (
        table(kernel.state_snapshot, "positions")["other"] == ActorPosition("other", "c").to_json()
    )
    assert kernel.module_ids == ("movement",)
    assert first.transition is not None
    assert first.transition.sequence == 1
    assert first.emitted_event_ids == ("run-m2:event:00000001",)
    event = kernel.events[0]
    assert event.simulation_time == 10
    assert event.source_kind is EventSourceKind.ACTION
    assert event.source_ref == "action-1"
    assert event.correlation_id == "correlation-m2"
    assert event.payload == {
        "actor_id": "actor",
        "route_id": "ab",
        "origin": "a",
        "destination": "b",
    }

    rejected = kernel.submit_action(request(tick=10, request_id="wrong-direction"))
    assert rejected.reason_code == "WRONG_ORIGIN"
    assert kernel.simulation_time == 10
    reverse = kernel.submit_action(
        request(payload={"route_id": "ba"}, tick=10, request_id="return")
    )
    assert reverse.status is ActionStatus.SUCCEEDED
    assert kernel.simulation_time == 14
    assert (
        table(kernel.state_snapshot, "positions")["actor"] == ActorPosition("actor", "a").to_json()
    )
    kernel.close()


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("actor", "UNKNOWN_ACTOR"),
        ("position", "MISSING_POSITION"),
        ("route", "UNKNOWN_ROUTE"),
        ("origin-location", "UNKNOWN_LOCATION"),
        ("destination-location", "UNKNOWN_LOCATION"),
        ("position-location", "UNKNOWN_LOCATION"),
        ("wrong-origin", "WRONG_ORIGIN"),
        ("impassable", "ROUTE_IMPASSABLE"),
        ("bad-cost", "INVALID_TRAVERSAL_COST"),
        ("bad-passability", "INVALID_PASSABILITY"),
    ],
)
def test_invalid_move_preserves_all_execution_state(case: str, reason: str) -> None:
    state = world()
    if case == "actor":
        del obj(state["entities"])["actor"]
    elif case == "position":
        del table(state, "positions")["actor"]
    elif case == "route":
        del table(state, "routes")["ab"]
    elif case == "origin-location":
        del table(state, "locations")["a"]
    elif case == "destination-location":
        del table(state, "locations")["b"]
    elif case == "position-location":
        obj(table(state, "positions")["actor"])["location_id"] = "missing"
    elif case == "wrong-origin":
        obj(table(state, "positions")["actor"])["location_id"] = "c"
    elif case == "impassable":
        obj(table(state, "routes")["ab"])["passable"] = False
    elif case == "bad-passability":
        obj(table(state, "routes")["ab"])["passable"] = 1
    else:
        obj(table(state, "routes")["ab"])["traversal_cost"] = 0
    kernel = factory(state)
    kernel.boot()
    kernel.schedule(change(1, "marker", value=2))
    before = execution_state(kernel)
    result = kernel.submit_action(request())
    assert result.status is ActionStatus.REJECTED
    assert result.reason_code == reason
    assert result.transition is None
    assert result.emitted_event_ids == ()
    assert result.started_at == result.resolved_at == 0
    assert execution_state(kernel) == before
    assert result.state_digest_after == state_digest(state)
    assert kernel.action_results == (result,)
    # Rejection consumes neither scheduler IDs nor transition/event IDs.
    later = kernel.schedule(change(2, "marker", value=3))
    assert later.insertion_sequence == 1
    kernel.advance_to(2)
    assert [event.event_sequence for event in kernel.events] == [1, 2]
    assert [outcome.transition.sequence for outcome in kernel.system_event_outcomes] == [1, 2]
    kernel.close()


@pytest.mark.parametrize("cost", [-1, True, 1.5, "10", None])
def test_move_revalidates_raw_canonical_traversal_cost(cost: JsonValue) -> None:
    state = world()
    obj(table(state, "routes")["ab"])["traversal_cost"] = cost
    kernel = factory(state)
    kernel.boot()
    before = execution_state(kernel)
    assert kernel.submit_action(request()).reason_code == "INVALID_TRAVERSAL_COST"
    assert execution_state(kernel) == before
    kernel.close()


@pytest.mark.parametrize(
    ("action_type", "payload", "reason"),
    [
        ("MOVE", {}, "INVALID_PAYLOAD"),
        ("MOVE", {"destination": "b"}, "INVALID_PAYLOAD"),
        ("MOVE", {"route_id": "ab", "extra": 1}, "INVALID_PAYLOAD"),
        ("MOVE", {"route_id": 1}, "INVALID_PAYLOAD"),
        ("MOVE", {"route_id": "missing"}, "UNKNOWN_ROUTE"),
        ("WAIT", {}, "INVALID_PAYLOAD"),
        ("WAIT", {"duration": 1, "extra": 1}, "INVALID_PAYLOAD"),
        *[
            ("WAIT", {"duration": value}, "INVALID_DURATION")
            for value in (0, -1, True, 1.5, "1", None)
        ],
    ],
)
def test_payload_rejection_is_atomic(
    kernel: SimulationKernel, action_type: str, payload: JsonObject, reason: str
) -> None:
    kernel.boot()
    kernel.schedule(change(1, "marker", value=2))
    before = execution_state(kernel)
    result = kernel.submit_action(request(action_type, payload))
    assert result.status is ActionStatus.REJECTED
    assert result.reason_code == reason
    assert execution_state(kernel) == before
    kernel.close()


def test_wait_only_consumes_time_without_domain_mutation(kernel: SimulationKernel) -> None:
    kernel.boot()
    state_before = kernel.state_snapshot
    rng_before = kernel.rng_snapshot
    result = kernel.submit_action(request("WAIT", {"duration": 7}))
    assert result.status is ActionStatus.SUCCEEDED
    assert (result.started_at, result.resolved_at) == (0, 7)
    assert kernel.simulation_time == 7
    assert kernel.state_snapshot == state_before
    assert result.state_digest_after == state_digest(state_before)
    assert kernel.rng_snapshot == rng_before
    assert result.emitted_event_ids == ()
    assert kernel.events == ()
    kernel.close()


@pytest.mark.parametrize("actor_exists", [True, False])
def test_wait_requires_identity_but_no_spatial_state(actor_exists: bool) -> None:
    state: JsonObject = {"entities": entity_state((Entity("actor", "person"),))}
    if not actor_exists:
        state["entities"] = {}
    kernel = factory(state)
    kernel.boot()
    result = kernel.submit_action(request("WAIT", {"duration": 1}))
    assert result.status is (ActionStatus.SUCCEEDED if actor_exists else ActionStatus.REJECTED)
    assert result.reason_code == (None if actor_exists else "UNKNOWN_ACTOR")
    assert kernel.simulation_time == (1 if actor_exists else 0)
    assert kernel.state_snapshot == state
    kernel.close()


@pytest.mark.parametrize("action_type", ["MOVE", "WAIT"])
def test_system_events_during_duration_and_at_completion_run_first(
    kernel: SimulationKernel, action_type: str
) -> None:
    kernel.boot()
    kernel.schedule(change(10, "marker", priority=2, value=4))
    kernel.schedule(change(10, "marker", priority=1, value=2))
    kernel.schedule(change(10, "marker", priority=1, value=3))
    kernel.schedule(change(5, "marker", value=1))
    future = kernel.schedule(change(11, "marker", value=99))
    payload = {"route_id": "ab"} if action_type == "MOVE" else {"duration": 10}
    result = kernel.submit_action(request(action_type, cast(JsonObject, payload)))
    assert result.status is ActionStatus.SUCCEEDED
    assert result.transition is not None
    assert result.transition.sequence == 5
    assert result.resolved_at == 10
    assert kernel.state_snapshot["unrelated"] == {"value": 4}
    assert kernel.rng_snapshot.draw_count == 4
    assert kernel.pending_scheduled_events == (future,)
    assert [o.scheduled_event_id for o in kernel.system_event_outcomes] == [
        f"run-m2:scheduled-event:{index:08d}" for index in (3, 1, 2, 0)
    ]
    assert [e.simulation_time for e in kernel.events[:4]] == [5, 10, 10, 10]
    if action_type == "MOVE":
        assert kernel.events[-1].event_type == "ActorMoved"
        assert kernel.events[-1].event_sequence == 5
    kernel.close()


@pytest.mark.parametrize("closure_tick", [5, 10])
def test_closure_keeps_elapsed_time_system_mutation_and_failed_result(
    kernel: SimulationKernel, closure_tick: int
) -> None:
    kernel.boot()
    scheduled = kernel.schedule(change(closure_tick, "close"))
    result = kernel.submit_action(request())
    assert result.status is ActionStatus.FAILED
    assert result.reason_code == "ROUTE_IMPASSABLE"
    assert result.transition is None
    assert result.emitted_event_ids == ()
    assert (result.started_at, result.resolved_at, kernel.simulation_time) == (0, 10, 10)
    assert obj(table(kernel.state_snapshot, "routes")["ab"])["passable"] is False
    assert (
        table(kernel.state_snapshot, "positions")["actor"] == ActorPosition("actor", "a").to_json()
    )
    assert kernel.pending_scheduled_events == ()
    assert kernel.system_event_outcomes[0].scheduled_event_id == scheduled.scheduled_event_id
    assert kernel.transition_sequence == 1
    assert kernel.rng_snapshot.draw_count == 1
    assert [event.event_type for event in kernel.events] == ["WorldChanged"]
    assert result.state_digest_after == kernel.state_digest
    assert kernel.action_results == (result,)
    wait = kernel.submit_action(request("WAIT", {"duration": 1}, tick=10, request_id="wait"))
    assert wait.transition is not None
    assert wait.transition.sequence == 2
    kernel.close()


@pytest.mark.parametrize(
    ("kind", "value", "reason"),
    [
        ("destination", None, "ROUTE_CHANGED"),
        ("position", None, "WRONG_ORIGIN"),
        ("entity", None, "UNKNOWN_ACTOR"),
        ("location", None, "UNKNOWN_LOCATION"),
        ("cost", 0, "INVALID_TRAVERSAL_COST"),
    ],
)
def test_completion_rechecks_current_world(
    kernel: SimulationKernel, kind: str, value: JsonValue, reason: str
) -> None:
    kernel.boot()
    kernel.schedule(change(5, kind, value=value))
    result = kernel.submit_action(request())
    assert result.status is ActionStatus.FAILED
    assert result.reason_code == reason
    assert kernel.simulation_time == 10
    assert result.emitted_event_ids == ()
    assert [event.event_type for event in kernel.events] == ["WorldChanged"]
    expected = "c" if kind == "position" else "a"
    assert obj(table(kernel.state_snapshot, "positions")["actor"])["location_id"] == expected
    kernel.close()


def test_duration_is_fixed_at_start_and_completion_uses_current_passability(
    kernel: SimulationKernel,
) -> None:
    kernel.boot()
    kernel.schedule(change(3, "close"))
    kernel.schedule(change(5, "cost", value=99))
    kernel.schedule(change(10, "open"))
    result = kernel.submit_action(request())
    assert result.status is ActionStatus.SUCCEEDED
    assert kernel.simulation_time == 10
    assert obj(table(kernel.state_snapshot, "routes")["ab"])["traversal_cost"] == 99
    kernel.close()


def test_submission_time_world_progress_precedes_start_validation(kernel: SimulationKernel) -> None:
    kernel.boot()
    kernel.schedule(change(5, "close"))
    result = kernel.submit_action(request(tick=5))
    assert result.status is ActionStatus.REJECTED
    assert result.reason_code == "ROUTE_IMPASSABLE"
    assert result.started_at == result.resolved_at == 5
    assert kernel.simulation_time == 5
    assert len(kernel.system_event_outcomes) == 1
    assert kernel.transition_sequence == 1
    kernel.close()


def test_m2_replay_repeats_success_rejection_and_elapsed_failure() -> None:
    inputs = ReplayInput(
        ScenarioSchedule("test-m2", "1", (change(15, "close"), change(20, "marker", value=8))),
        (
            request(request_id="move-success"),
            request(tick=10, request_id="reject"),
            request(payload={"route_id": "ba"}, tick=10, request_id="return"),
            request(tick=14, request_id="elapsed-failure"),
            request("WAIT", {"duration": 2}, tick=24, request_id="wait"),
        ),
        advance_to=26,
    )
    first = ReplayHarness(factory).run(inputs)
    second = ReplayHarness(factory).run(inputs)
    reordered = world()
    reordered["entities"] = dict(reversed(list(obj(reordered["entities"]).items())))
    reordered["movement"] = dict(reversed(list(obj(reordered["movement"]).items())))
    third = ReplayHarness(lambda: factory(reordered)).run(inputs)
    assert first == second == third
    assert [r.status for r in first.action_results] == [
        ActionStatus.SUCCEEDED,
        ActionStatus.REJECTED,
        ActionStatus.SUCCEEDED,
        ActionStatus.FAILED,
        ActionStatus.SUCCEEDED,
    ]
    assert [e.event_type for e in first.events] == [
        "ActorMoved",
        "ActorMoved",
        "WorldChanged",
        "WorldChanged",
    ]
    assert [e.event_id for e in first.events] == [
        f"run-m2:event:{index:08d}" for index in range(1, 5)
    ]
    assert [r.transition.sequence if r.transition else None for r in first.action_results] == [
        1,
        None,
        2,
        None,
        5,
    ]
    expected = world()
    obj(table(expected, "routes")["ab"])["passable"] = False
    expected["unrelated"] = {"value": 8}
    assert first.final_state == expected
    assert first.final_state_digest == state_digest(expected)
    assert first.final_simulation_time == 26
    assert first.rng_draw_count == 2


@pytest.mark.parametrize("action_type", ["MOVE", "WAIT"])
def test_rejection_matches_post_submission_world_snapshot(action_type: str) -> None:
    kernel = factory()
    control = factory()
    for instance in (kernel, control):
        instance.boot()
        instance.schedule(change(99, "marker", value=2))
        instance.schedule(change(100, "close"))
        instance.schedule(change(105, "marker", value=3))
    control.advance_to(100)
    payload: JsonObject = {"route_id": "ab"} if action_type == "MOVE" else {"duration": True}
    result = kernel.submit_action(request(action_type, payload, tick=100))
    assert result.status is ActionStatus.REJECTED
    assert result.started_at == result.resolved_at == 100
    assert execution_state(kernel) == execution_state(control)
    assert result.transition is None
    assert kernel.schedule(change(106, "marker")) == control.schedule(change(106, "marker"))
    kernel.close()
    control.close()


@pytest.mark.parametrize("entity_type", ["person", "object"])
def test_self_route_and_entity_type_metadata_do_not_add_implicit_restrictions(
    entity_type: str,
) -> None:
    state = world()
    obj(state["entities"])["actor"] = Entity("actor", entity_type).to_json()
    table(state, "routes")["aa"] = Route("aa", "a", "a", 1).to_json()
    kernel = factory(state)
    kernel.boot()
    result = kernel.submit_action(request(payload={"route_id": "aa"}))
    assert result.status is ActionStatus.SUCCEEDED
    assert kernel.simulation_time == 1
    assert kernel.state_snapshot == state
    assert kernel.events[0].payload["origin"] == kernel.events[0].payload["destination"] == "a"
    kernel.close()


@pytest.mark.parametrize(
    ("collection", "record_id", "field", "value", "reason"),
    [
        ("positions", "actor", "actor_id", "other", "INVALID_POSITION"),
        ("positions", "actor", "location_id", None, "INVALID_POSITION"),
        ("routes", "ab", "route_id", "ba", "INVALID_ROUTE"),
        ("routes", "ab", "origin", "missing", "UNKNOWN_LOCATION"),
        ("routes", "ab", "destination", None, "INVALID_ROUTE"),
        ("locations", "b", "location_id", "a", "INVALID_LOCATION"),
    ],
)
def test_corrupt_spatial_identity_is_rejected_atomically(
    collection: str,
    record_id: str,
    field: str,
    value: JsonValue,
    reason: str,
) -> None:
    state = world()
    obj(table(state, collection)[record_id])[field] = value
    kernel = factory(state)
    kernel.boot()
    before = execution_state(kernel)
    result = kernel.submit_action(request())
    assert result.status is ActionStatus.REJECTED
    assert result.reason_code == reason
    assert execution_state(kernel) == before
    kernel.close()


def test_replay_with_completion_tick_events_compares_full_rng_state() -> None:
    instances: list[SimulationKernel] = []

    def capture_factory() -> SimulationKernel:
        instance = factory()
        instances.append(instance)
        return instance

    inputs = ReplayInput(
        ScenarioSchedule(
            "test-m2",
            "1",
            (
                change(105, "close"),
                change(112, "open"),
                change(122, "marker", priority=2, value=9),
                change(122, "marker", priority=1, value=7),
                change(122, "marker", priority=1, value=8),
            ),
        ),
        (
            request(tick=100, request_id="elapsed-failure"),
            request(tick=110, request_id="reject"),
            request("WAIT", {"duration": 2}, tick=110, request_id="wait"),
            request(tick=112, request_id="success"),
        ),
        advance_to=122,
    )
    harness = ReplayHarness(capture_factory)
    first = harness.run(inputs)
    second = harness.run(inputs)
    assert first == second
    assert [result.status for result in first.action_results] == [
        ActionStatus.FAILED,
        ActionStatus.REJECTED,
        ActionStatus.SUCCEEDED,
        ActionStatus.SUCCEEDED,
    ]
    assert [(result.started_at, result.resolved_at) for result in first.action_results] == [
        (100, 110),
        (110, 110),
        (110, 112),
        (112, 122),
    ]
    assert [outcome.scheduled_event_id for outcome in first.system_event_outcomes] == [
        f"run-m2:scheduled-event:{index:08d}" for index in (0, 1, 3, 4, 2)
    ]
    assert [event.event_type for event in first.events] == ["WorldChanged"] * 5 + ["ActorMoved"]
    expected_rng = DeterministicRng(1234)
    for _ in range(5):
        expected_rng.next_u64()
    assert instances[0].rng_snapshot == instances[1].rng_snapshot == expected_rng.snapshot()
    assert first.rng_draw_count == 5
    assert first.final_simulation_time == 122
    assert first.final_state["unrelated"] == {"value": 9}
    assert first.final_state_digest == state_digest(first.final_state)
