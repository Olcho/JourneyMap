"""M4 atomicity, recovery and actor-scope adversarial cases."""

from dataclasses import replace

import pytest
from test_alderwick import execution, intent, obj, section

from journeymap.application.observations import ObservationPipeline
from journeymap.application.perception import perceive
from journeymap.bootstrap import (
    create_alderwick_application,
    create_alderwick_kernel,
    create_application,
    create_kernel,
)
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.core.controller import GameSubmissionError
from journeymap.core.events import EventBus, EventDeliveryError, EventEnvelope
from journeymap.core.handlers import ResolutionContext, SystemEventRegistry, TransitionPlan
from journeymap.core.observations import PerceptionContext
from journeymap.core.scheduler import ScheduledEvent
from journeymap.modules.knowledge import KnowledgeLedger, KnowledgeRecord
from journeymap.modules.movement.transitions import close_routes
from journeymap.scenarios.alderwick.bridge import (
    AFFECTED_ROUTES,
    CollapseBridgeHandler,
    perceive_bridge,
)
from journeymap.scenarios.alderwick.fixture import initial_world, scenario_schedule
from journeymap.scenarios.alderwick.knowledge import project_bridge_knowledge


@pytest.mark.parametrize(
    "fault", ["bridge", "route", "cost", "endpoint", "witness", "payload", "resolve", "event"]
)
def test_collapse_failure_never_partially_commits_or_consumes_schedule(fault: str) -> None:
    state = initial_world()
    if fault == "bridge":
        obj(obj(state["alderwick"])["east_bridge"])["condition"] = "damaged"
    elif fault in {"route", "cost", "endpoint"}:
        routes = obj(obj(state["movement"])["routes"])
        if fault == "route":
            del routes[AFFECTED_ROUTES[-1]]  # first candidate route was already processed
        elif fault == "cost":
            obj(routes[AFFECTED_ROUTES[-1]])["traversal_cost"] = True
        else:
            obj(routes[AFFECTED_ROUTES[-1]])["destination"] = "missing"
    elif fault == "witness":
        obj(obj(obj(state["movement"])["positions"])["hugh"])["actor_id"] = "stranger"

    class FaultHandler(CollapseBridgeHandler):
        def resolve(self, event: ScheduledEvent, context: ResolutionContext) -> TransitionPlan:
            plan = super().resolve(event, context)
            if fault == "resolve":
                context.rng.next_u64()
                raise ValueError("after both candidate mutations")
            if fault == "event":
                plan.events[0].payload["invalid"] = float("nan")
            return plan

    systems = SystemEventRegistry()
    systems.register("CollapseEastBridge", 1, FaultHandler())
    kernel = create_kernel(initial_state=state, system_event_registry=systems)
    kernel.boot()
    try:
        spec = scenario_schedule().events[0]
        if fault == "payload":
            spec = replace(spec, payload={"bridge_id": "east-bridge", "extra": True})
        kernel.schedule(spec)
        before = execution(kernel)
        for _ in range(2):
            with pytest.raises(ValueError):
                kernel.advance_to(3)
            assert execution(kernel) == before
            assert kernel.action_results == ()
    finally:
        kernel.close()


def test_movement_closure_helper_detaches_and_preserves_unrelated_owned_fields() -> None:
    state = initial_world()
    movement = obj(state["movement"])
    route = obj(obj(movement["routes"])[AFFECTED_ROUTES[0]])
    route["extension"] = {"private": [1]}
    before = canonical_json(state)
    changed = close_routes(state, AFFECTED_ROUTES)
    assert canonical_json(state) == before
    assert changed["positions"] == movement["positions"]
    modified = obj(obj(changed["routes"])[AFFECTED_ROUTES[0]])
    assert modified["extension"] == {"private": [1]}
    obj(modified["extension"]).clear()
    assert route["extension"] == {"private": [1]}
    for ids in ((), (AFFECTED_ROUTES[0], AFFECTED_ROUTES[0])):
        with pytest.raises(ValueError):
            close_routes(state, ids)


@pytest.mark.parametrize("event_type", ["BridgeCollapsed", "ActorMoved"])
def test_delivery_failure_preserves_committed_knowledge_and_never_reexecutes(
    event_type: str,
) -> None:
    bus = EventBus()
    calls = []

    def broken(event: EventEnvelope) -> None:
        calls.append(event.event_id)
        raise RuntimeError("SECRET")

    bus.subscribe(
        event_type=event_type,
        priority=-100,
        module_id="test",
        subscriber_id="broken",
        subscriber=broken,
    )
    kernel = create_alderwick_kernel(event_bus=bus)
    kernel.boot()
    try:
        kernel.schedule(scenario_schedule().events[0])
        app, research = create_alderwick_application(kernel)
        if event_type == "BridgeCollapsed":
            with pytest.raises(EventDeliveryError):
                kernel.advance_to(8)
            actor = "hugh"
            assert kernel.simulation_time == 3
            assert research.action_traces == ()
        else:
            kernel.advance_to(3)
            actor = "thomas"
            game = app.game_for(actor)
            with pytest.raises(GameSubmissionError, match=r"^ENGINE_ERROR$"):
                game.submit(intent(game, "village-square-to-east-road"))
            assert research.action_traces[-1].result == research.action_results[-1]
        history = research.knowledge_history(actor)
        assert len(history) == 1 and history[0].source_ref == calls[0]
        assert section(app.game_for(actor).observe(), "knowledge") == {
            "records": [history[0].to_json()]
        }
        events = kernel.events
        kernel.advance_to(9)
        assert kernel.events == events and len(calls) == 1
        assert research.knowledge_history(actor) == history
    finally:
        kernel.close()


def test_projection_failure_is_a_read_failure_without_partial_records_or_world_rollback() -> None:
    fail = True

    def projector(events: tuple[EventEnvelope, ...]) -> tuple[KnowledgeRecord, ...]:
        projected = project_bridge_knowledge(events)
        if fail:
            raise ValueError("projection failure after preparing records")
        return projected

    kernel = create_alderwick_kernel()
    kernel.boot()
    try:
        app, research = create_application(
            kernel, knowledge_projector=projector, perception_extension=perceive_bridge
        )
        kernel.schedule(scenario_schedule().events[0])
        kernel.advance_to(3)
        before = execution(kernel)
        with pytest.raises(ValueError, match="projection failure"):
            research.knowledge_history("hugh")
        with pytest.raises(GameSubmissionError, match=r"^OBSERVATION_UNAVAILABLE$"):
            app.game_for("hugh").observe()
        assert research.observations == () and execution(kernel) == before
        fail = False
        assert app.game_for("hugh").observe().observation_sequence == 1
        assert len(research.knowledge_history("hugh")) == 1
        assert execution(kernel) == before
    finally:
        kernel.close()


def test_perception_filters_secrets_before_any_contributor_and_remote_truth_is_irrelevant() -> None:
    state = initial_world()
    bridge = obj(obj(state["alderwick"])["east_bridge"])
    bridge["condition"] = "collapsed"
    bridge["future_repair"] = "SECRET"
    bridge["witness_actor_ids"] = ["SECRET"]
    context = perceive(
        run_id="run",
        actor_id="hugh",
        simulation_time=3,
        world=state,
        knowledge=KnowledgeLedger("run", 0),
        extension=perceive_bridge,
    )
    assert context.perceived == {
        "self": {"entity_id": "hugh", "entity_type": "person"},
        "movement": {"location_id": "east-road"},
        "bridge": {"bridge_id": "east-bridge", "condition": "collapsed"},
    }
    pipeline = ObservationPipeline()

    def inspect(context: PerceptionContext) -> JsonObject:
        assert "SECRET" not in canonical_json(context.perceived)
        return context.perceived

    pipeline.register(priority=0, module_id="test", contributor_id="inspect", contributor=inspect)
    pipeline.assemble(context)
    remote = perceive(
        run_id="run",
        actor_id="marta",
        simulation_time=3,
        world=state,
        knowledge=KnowledgeLedger("run", 0),
        extension=perceive_bridge,
    )
    state["alderwick"] = {"invalid_hidden_truth": "SECRET"}
    assert remote == perceive(
        run_id="run",
        actor_id="marta",
        simulation_time=3,
        world=state,
        knowledge=KnowledgeLedger("run", 0),
        extension=perceive_bridge,
    )
    with pytest.raises(ValueError):
        perceive(
            run_id="run",
            actor_id="hugh",
            simulation_time=3,
            world=state,
            knowledge=KnowledgeLedger("run", 0),
            extension=perceive_bridge,
        )


def test_perception_extension_cannot_replace_self_scope_or_alias_world() -> None:
    state = initial_world()
    before = canonical_json(state)

    def unsafe(world: JsonObject, actor: str) -> JsonObject:
        world.clear()
        return {"self": {"entity_id": "wrong"}}

    with pytest.raises(ValueError, match="replace existing scopes"):
        perceive(
            run_id="run",
            actor_id="hugh",
            simulation_time=0,
            world=state,
            knowledge=KnowledgeLedger("run", 0),
            extension=unsafe,
        )
    assert canonical_json(state) == before


@pytest.mark.parametrize("bad", [None, True, [], "damaged"])
def test_visible_invalid_bridge_never_becomes_perceived_fact(bad: JsonValue) -> None:
    state = initial_world()
    obj(obj(state["alderwick"])["east_bridge"])["condition"] = bad
    with pytest.raises(ValueError):
        perceive_bridge(state, "hugh")
