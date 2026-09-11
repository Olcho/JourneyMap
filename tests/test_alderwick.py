"""M4 Alderwick Bridge Integration gate and live Controller/replay evidence."""

from collections.abc import Iterator
from dataclasses import replace

import pytest

from journeymap.adapters.scripted import ScriptedController
from journeymap.application.research import ResearchView
from journeymap.application.session import SimulationApplication
from journeymap.bootstrap import create_alderwick_application, create_alderwick_kernel
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.core.controller import Controller, GamePort
from journeymap.core.events import EventSourceKind
from journeymap.core.handlers import ActionRequest, ActionStatus
from journeymap.core.kernel import SimulationKernel
from journeymap.core.observations import Observation
from journeymap.core.replay import ReplayHarness, ReplayInput
from journeymap.modules.knowledge import KnowledgeLedger
from journeymap.modules.knowledge.projection import KnowledgeProjection
from journeymap.scenarios.alderwick.bridge import AFFECTED_ROUTES
from journeymap.scenarios.alderwick.fixture import (
    ACTOR_LOCATIONS,
    LOCATIONS,
    initial_knowledge,
    initial_world,
    scenario_schedule,
)
from journeymap.scenarios.alderwick.knowledge import project_bridge_knowledge


def obj(value: JsonValue) -> JsonObject:
    assert isinstance(value, dict)
    return value


def section(observation: Observation, module: str) -> JsonObject:
    sections = observation.content["sections"]
    assert isinstance(sections, list)
    return next(obj(obj(s)["content"]) for s in sections if obj(s)["module_id"] == module)


def intent(game: GamePort, route: str) -> ActionRequest:
    observation = game.observe()
    return ActionRequest(
        f"{observation.observation_id}:move",
        observation.run_id,
        observation.actor_id,
        observation.observation_id,
        observation.simulation_time,
        "MOVE",
        1,
        {"route_id": route},
    )


def execution(kernel: SimulationKernel) -> tuple[object, ...]:
    return (
        kernel.state_snapshot,
        kernel.state_digest,
        kernel.simulation_time,
        kernel.rng_snapshot,
        kernel.transition_sequence,
        kernel.events,
        kernel.pending_scheduled_events,
        kernel.system_event_outcomes,
    )


@pytest.fixture
def live() -> Iterator[tuple[SimulationKernel, SimulationApplication, ResearchView]]:
    kernel = create_alderwick_kernel()
    kernel.boot()
    for event in scenario_schedule().events:
        kernel.schedule(event)
    app, research = create_alderwick_application(kernel)
    try:
        yield kernel, app, research
    finally:
        kernel.close()


def test_alderwick_fixture_is_small_connected_and_initially_intact() -> None:
    world = initial_world()
    movement = obj(world["movement"])
    assert set(obj(movement["locations"])) == set(LOCATIONS)
    assert len(obj(world["entities"])) == len(ACTOR_LOCATIONS) == 5
    assert len(obj(movement["routes"])) == 14
    reached = {"west-gate"}
    for _ in LOCATIONS:
        for route in obj(movement["routes"]).values():
            row = obj(route)
            assert row["passable"] is True and row["traversal_cost"] == 2
            if row["origin"] in reached:
                reached.add(str(row["destination"]))
    assert reached == set(LOCATIONS)
    assert world["alderwick"] == {
        "east_bridge": {"bridge_id": "east-bridge", "condition": "intact"}
    }


def test_future_hidden_collapse_atomic_witness_and_remote_unknown(
    live: tuple[SimulationKernel, SimulationApplication, ResearchView],
) -> None:
    kernel, app, research = live
    before = execution(kernel)
    for actor, _ in ACTOR_LOCATIONS:
        observation = app.game_for(actor).observe()
        assert "collapsed" not in canonical_json(observation.content)
        assert "CollapseEastBridge" not in canonical_json(observation.content)
        assert all(k.predicate != "condition" for k in research.knowledge_history(actor))
    assert execution(kernel) == before
    assert "collapsed" not in canonical_json(research.world_snapshot)
    scheduled = research.pending_scheduled_events[0]
    kernel.advance_to(2)
    assert research.events == ()
    kernel.advance_to(3)
    assert research.action_traces == ()
    assert research.action_results == ()
    assert len(research.events) == len(research.system_event_outcomes) == 1
    event = research.events[0]
    outcome = research.system_event_outcomes[0]
    assert event.event_type == "BridgeCollapsed" and event.schema_version == 1
    assert event.source_kind == EventSourceKind.SCHEDULED_EVENT
    assert event.source_ref == outcome.scheduled_event_id == scheduled.scheduled_event_id
    assert event.transition_id == outcome.transition.transition_id
    assert outcome.emitted_event_ids == (event.event_id,)
    assert outcome.state_digest_after == research.state_digest
    assert event.payload == {
        "bridge_id": "east-bridge",
        "condition": "collapsed",
        "closed_route_ids": list(AFFECTED_ROUTES),
        "witness_actor_ids": ["hugh"],
    }
    world = research.world_snapshot
    assert obj(obj(world["alderwick"])["east_bridge"])["condition"] == "collapsed"
    routes = obj(obj(world["movement"])["routes"])
    assert all(obj(routes[route])["passable"] is False for route in AFFECTED_ROUTES)
    assert obj(routes["village-square-to-east-road"])["passable"] is True
    witness = research.knowledge_history("hugh")
    assert len(witness) == 1
    assert witness[0].source_kind == "DIRECT_OBSERVATION"
    assert witness[0].source_ref == event.event_id and witness[0].learned_at == 3
    for actor in ("stranger", "marta", "edwin", "thomas"):
        assert all(k.predicate != "condition" for k in research.knowledge_history(actor))
        observation = app.game_for(actor).observe()
        assert section(observation, "alderwick") == {"bridge": {}}
        assert "collapsed" not in canonical_json(observation.content)
    observation = app.game_for("hugh").observe()
    assert section(observation, "alderwick") == {
        "bridge": {"bridge_id": "east-bridge", "condition": "collapsed"},
    }
    assert section(observation, "knowledge") == {"records": [witness[0].to_json()]}
    assert kernel.rng_snapshot.draw_count == 0


def test_later_discovery_persists_after_departure_and_reentry_deduplicates(
    live: tuple[SimulationKernel, SimulationApplication, ResearchView],
) -> None:
    kernel, app, research = live
    kernel.advance_to(3)
    game = app.game_for("thomas")
    assert research.knowledge_history("thomas") == ()
    game.submit(intent(game, "village-square-to-east-road"))
    learned = research.knowledge_history("thomas")
    event = research.events[-1]
    assert event.event_type == "ActorMoved"
    assert len(learned) == 1 and learned[0].source_ref == event.event_id
    assert learned[0].learned_at == 5
    observed = game.observe()
    assert section(observed, "alderwick")["bridge"] == {
        "bridge_id": "east-bridge",
        "condition": "collapsed",
    }
    assert section(observed, "knowledge")["records"] == [learned[0].to_json()]
    before = execution(kernel)
    rejected = game.submit(intent(game, "east-road-to-east-bridge"))
    assert rejected.status == ActionStatus.REJECTED and rejected.reason_code == "ROUTE_IMPASSABLE"
    assert execution(kernel) == before
    assert research.action_results[-1].handler_id == "movement.move.v1"
    game.submit(intent(game, "east-road-to-village-square"))
    assert section(game.observe(), "alderwick") == {"bridge": {}}
    assert research.knowledge_history("thomas") == learned
    game.submit(intent(game, "village-square-to-east-road"))
    assert research.knowledge_history("thomas") == learned
    assert research.events[0].payload["witness_actor_ids"] == ["hugh"]
    assert all(k.predicate != "condition" for k in research.knowledge_history("stranger"))


def test_same_tick_arrival_is_discovery_not_retroactive_witness() -> None:
    kernel = create_alderwick_kernel()
    kernel.boot()
    try:
        kernel.schedule(scenario_schedule(2).events[0])
        app, research = create_alderwick_application(kernel)
        game = app.game_for("thomas")
        game.submit(intent(game, "village-square-to-east-road"))
        assert [e.event_type for e in research.events] == ["BridgeCollapsed", "ActorMoved"]
        assert research.events[0].payload["witness_actor_ids"] == ["hugh"]
        assert research.knowledge_history("thomas")[0].source_ref == research.events[1].event_id
        assert research.knowledge_history("thomas")[0].learned_at == 2
    finally:
        kernel.close()


@pytest.mark.parametrize("collapse_tick", [1, 2])
def test_collapse_during_or_at_bridge_move_completion_keeps_system_commit(
    collapse_tick: int,
) -> None:
    kernel = create_alderwick_kernel()
    kernel.boot()
    try:
        kernel.schedule(scenario_schedule(collapse_tick).events[0])
        app, research = create_alderwick_application(kernel)
        game = app.game_for("hugh")
        result = game.submit(intent(game, "east-road-to-east-bridge"))
        assert result.status == ActionStatus.FAILED and result.reason_code == "ROUTE_IMPASSABLE"
        assert result.resolved_at == 2
        assert [e.event_type for e in research.events] == ["BridgeCollapsed"]
        assert (
            obj(obj(obj(kernel.state_snapshot["movement"])["positions"])["hugh"])["location_id"]
            == "east-road"
        )
        assert research.action_results[0].transition is None
        assert research.knowledge_history("hugh")[0].learned_at == collapse_tick
    finally:
        kernel.close()


def closed_loop() -> tuple[object, ...]:
    kernel = create_alderwick_kernel()
    kernel.boot()
    try:
        schedule = scenario_schedule()
        kernel.schedule(schedule.events[0])
        app, research = create_alderwick_application(kernel)
        controller: Controller = ScriptedController()
        game = app.game_for("stranger")
        for _ in range(3):
            observation = game.observe()
            request = controller.decide(observation)
            assert (
                request.run_id,
                request.actor_id,
                request.based_on_observation_id,
                request.submitted_at,
            ) == (
                observation.run_id,
                observation.actor_id,
                observation.observation_id,
                observation.simulation_time,
            )
            game.submit(request)
        game.observe()
        traces = research.action_traces
        assert [t.request.action_type for t in traces] == ["MOVE", "MOVE", "WAIT"]
        assert len(research.observations) == 4
        assert all(
            t.result is not None and t.result.status == ActionStatus.SUCCEEDED for t in traces
        )
        for trace in traces:
            assert trace.request.based_on_observation_id in {
                o.observation_id for o in research.observations
            }
        replay_input = ReplayInput(
            schedule, tuple(t.request for t in traces), kernel.simulation_time
        )
        report = ReplayHarness(create_alderwick_kernel).run(replay_input)
        assert report.action_results == research.action_results
        assert report.system_event_outcomes == research.system_event_outcomes
        assert report.events == research.events
        assert report.final_state == research.world_snapshot
        assert report.final_state_digest == research.state_digest
        assert report.final_simulation_time == research.simulation_time == 5
        assert report.rng_draw_count == research.rng_snapshot.draw_count
        projected = KnowledgeProjection(
            KnowledgeLedger(kernel.manifest.run_id, 0, initial_knowledge(kernel.manifest.run_id)),
            report.events,
            project_bridge_knowledge,
        )
        for actor, _ in ACTOR_LOCATIONS:
            assert projected.for_actor(actor).history() == research.knowledge_history(actor)
        return research.observations, traces, report, projected.history(), research.rng_snapshot
    finally:
        kernel.close()


def test_scripted_closed_loop_engine_replay_knowledge_rebuild_and_fresh_run_determinism() -> None:
    assert closed_loop() == closed_loop()


def test_controller_has_no_hidden_capability_and_uses_observation_content(
    live: tuple[SimulationKernel, SimulationApplication, ResearchView],
) -> None:
    kernel, app, _ = live
    controller = ScriptedController()
    assert {name for name in dir(controller) if not name.startswith("_")} == {"decide"}
    assert not hasattr(controller, "__dict__")
    assert {name for name in dir(app.game_for("stranger")) if not name.startswith("_")} == {
        "observe",
        "submit",
    }
    observation = app.game_for("stranger").observe()
    before = execution(kernel)
    assert controller.decide(observation) == controller.decide(observation)
    assert controller.decide(observation).action_type == "MOVE"
    empty = Observation(observation.run_id, observation.actor_id, 99, 0, {"sections": []})
    assert controller.decide(empty).action_type == "WAIT"
    # Same location/knowledge, only the delivered visible condition differs.
    visible = observation.content
    sections = visible["sections"]
    assert isinstance(sections, list)
    for section in sections:
        if isinstance(section, dict) and section.get("module_id") == "alderwick":
            section["content"] = {"bridge": {"condition": "collapsed"}}
    changed = Observation(observation.run_id, observation.actor_id, 100, 0, visible)
    assert controller.decide(changed).action_type == "WAIT"
    assert execution(kernel) == before
    assert (
        app.game_for("stranger").submit(controller.decide(empty)).reason_code
        == "UNKNOWN_OBSERVATION"
    )
    forged = replace(controller.decide(observation), actor_id="hugh")
    assert app.game_for("stranger").submit(forged).reason_code == "WRONG_ACTOR"
    system = replace(
        controller.decide(observation),
        action_type="CollapseEastBridge",
        payload={"bridge_id": "east-bridge"},
    )
    assert app.game_for("stranger").submit(system).reason_code == "UNKNOWN_ACTION"
    assert execution(kernel) == before
