"""Live actor resource perception, source/sink accounting and recorded engine replay."""

import pytest
from test_alderwick import execution, obj, section
from test_resources import local_world

from journeymap.application.observations import ObservationPipeline
from journeymap.application.perception import perceive
from journeymap.bootstrap import (
    create_alderwick_application,
    create_alderwick_kernel,
    create_application,
)
from journeymap.core.canonical import JsonObject, canonical_json
from journeymap.core.controller import GamePort
from journeymap.core.handlers import ActionRequest, ActionStatus
from journeymap.core.observations import PerceptionContext
from journeymap.core.replay import ReplayHarness, ReplayInput, ReplayReport
from journeymap.examples.alderwick_resources import RESOURCE_PATH
from journeymap.modules.knowledge import KnowledgeLedger
from journeymap.modules.knowledge.projection import KnowledgeProjection
from journeymap.modules.survival.models import SurvivalState, actor_survival
from journeymap.modules.trade.perception import perceive_trade
from journeymap.scenarios.alderwick.fixture import (
    initial_knowledge,
    initial_world,
    scenario_schedule,
)
from journeymap.scenarios.alderwick.knowledge import project_bridge_knowledge
from journeymap.scenarios.alderwick.resources import (
    perceive_resources,
    resource_schedule,
    resource_world,
)


def live_intent(game: GamePort, action: str, payload: JsonObject) -> ActionRequest:
    observation = game.observe()
    return ActionRequest(
        f"{observation.observation_id}:{action}",
        observation.run_id,
        observation.actor_id,
        observation.observation_id,
        observation.simulation_time,
        action,
        1,
        payload,
    )


def run_live() -> tuple[object, ...]:
    kernel = create_alderwick_kernel(resources=True)
    kernel.boot()
    try:
        schedule = resource_schedule()
        for scheduled in schedule.events:
            kernel.schedule(scheduled)
        app, research = create_alderwick_application(kernel, resources=True)
        game = app.game_for("stranger")
        initial = game.observe()
        assert section(initial, "inventory") == {"inventory": {"bread": 0}}
        assert section(initial, "survival") == {"survival": {"hunger": 20, "fatigue": 10}}
        assert section(initial, "trade") == {"trade": {"wallet": 10, "offers": []}}
        states = []
        for action, data in RESOURCE_PATH:
            before = kernel.state_snapshot
            observation = game.observe()
            if action == "BUY":
                assert section(observation, "trade") == {
                    "trade": {
                        "wallet": 10,
                        "offers": [
                            {
                                "offer_id": "edwin-bread",
                                "seller_id": "edwin",
                                "item_id": "bread",
                                "unit_price": 2,
                                "active": True,
                            }
                        ],
                    }
                }
            result = game.submit(
                ActionRequest(
                    f"{observation.observation_id}:{action}",
                    observation.run_id,
                    "stranger",
                    observation.observation_id,
                    observation.simulation_time,
                    action,
                    1,
                    data,
                )
            )
            assert result.status == ActionStatus.SUCCEEDED
            states.append(kernel.state_snapshot)
            if action in {"BUY", "CONSUME", "REST"}:
                assert all(event.event_type == "SurvivalAdvanced" for event in kernel.events[-6:-1])
            if action == "BUY":
                assert obj(kernel.state_snapshot["trade"])["wallets"] == {
                    "stranger": 6,
                    "edwin": 4,
                    "marta": 0,
                    "hugh": 0,
                    "thomas": 0,
                }
                assert obj(obj(kernel.state_snapshot["inventory"])["owners"])["stranger"] == {
                    "bread": 2
                }
                assert obj(obj(kernel.state_snapshot["inventory"])["owners"])["edwin"] == {
                    "bread": 3
                }
            if action == "CONSUME":
                event = kernel.events[-1]
                assert event.payload == {
                    "actor_id": "stranger",
                    "item_id": "bread",
                    "quantity": 1,
                    "hunger_before": 34,
                    "hunger_after": 24,
                }
                assert event.source_ref == research.action_traces[-1].request.action_request_id
                assert kernel.state_snapshot["trade"] == before["trade"]
            if action == "REST":
                assert kernel.events[-1].payload == {
                    "actor_id": "stranger",
                    "duration": 3,
                    "fatigue_before": 20,
                    "fatigue_after": 11,
                }
                assert kernel.state_snapshot["trade"] == before["trade"]
                assert kernel.state_snapshot["inventory"] == before["inventory"]
        before_observe = execution(kernel)
        final = game.observe()
        game.observe()
        assert execution(kernel) == before_observe
        assert section(final, "inventory") == {"inventory": {"bread": 1}}
        assert actor_survival(kernel.state_snapshot, "stranger") == SurvivalState(30, 11)
        assert kernel.simulation_time == 10
        assert len(kernel.system_event_outcomes) == 11  # ten ticks and the M4 collapse
        requests = tuple(trace.request for trace in research.action_traces)
        replay_input = ReplayInput(schedule, requests, 10)
        harness = ReplayHarness(lambda: create_alderwick_kernel(resources=True))
        replay = harness.run(replay_input)
        assert replay == harness.run(replay_input)
        assert replay == ReplayReport(
            kernel.action_results,
            kernel.system_event_outcomes,
            kernel.events,
            kernel.state_snapshot,
            kernel.state_digest,
            10,
            kernel.rng_snapshot.draw_count,
        )
        projected = KnowledgeProjection(
            KnowledgeLedger(kernel.manifest.run_id, 0, initial_knowledge(kernel.manifest.run_id)),
            replay.events,
            project_bridge_knowledge,
        )
        assert projected.history() == app.knowledge_snapshot().history()
        assert len(research.knowledge_history("hugh")) == 1
        assert [
            event.payload["quantity"]
            for event in kernel.events
            if event.event_type == "ItemConsumed"
        ] == [1]
        return replay, research.observations, research.action_traces, projected.history(), states
    finally:
        kernel.close()


def test_live_buy_consume_rest_replays_and_fresh_runs_match() -> None:
    assert run_live() == run_live()


def test_resource_perception_filters_hidden_counterfactuals_before_contributors() -> None:
    state = local_world()
    own = perceive_resources(state, "stranger")
    assert set(own) == {"bridge", "inventory", "survival", "trade"}
    # Hidden quantities, wallet, effects, metadata and a remote offer are counterfactuals.
    obj(obj(state["inventory"])["owners"])["edwin"] = {"private": "SECRET"}
    obj(obj(state["trade"])["wallets"])["edwin"] = "SECRET"
    obj(state["survival"])["consumables"] = {"bread": "SECRET"}
    obj(obj(state["survival"])["actors"])["hugh"] = "SECRET"
    obj(obj(state["inventory"])["items"])["bread"] = {"item_id": "bread", "secret": "SECRET"}
    obj(obj(obj(state["survival"])["actors"])["stranger"])["secret"] = "SECRET"
    offers = obj(obj(state["trade"])["offers"])
    obj(offers["edwin-bread"])["private"] = "SECRET"
    offers["remote-secret"] = {
        "offer_id": "remote-secret",
        "seller_id": "hugh",
        "active": True,
        "item_id": "SECRET",
        "unit_price": "SECRET",
    }
    assert perceive_resources(state, "stranger") == own
    context = perceive(
        run_id="r",
        actor_id="stranger",
        simulation_time=0,
        world=state,
        knowledge=KnowledgeLedger("r", 0),
        extension=perceive_resources,
    )
    pipeline = ObservationPipeline()

    def inspect(safe: PerceptionContext) -> JsonObject:
        assert "SECRET" not in canonical_json(safe.perceived)
        assert "consumables" not in canonical_json(safe.perceived)
        assert safe.perceived["inventory"] == {"bread": 2}
        return safe.perceived

    pipeline.register(priority=0, module_id="test", contributor_id="inspect", contributor=inspect)
    pipeline.assemble(context)
    obj(own["inventory"])["bread"] = 999
    assert obj(perceive_resources(state, "stranger")["inventory"])["bread"] == 2


def test_local_active_offer_is_public_even_when_seller_stock_is_zero() -> None:
    state = local_world()
    before = perceive_trade(state, "stranger")
    obj(obj(state["inventory"])["owners"])["edwin"] = {"bread": 0}
    assert perceive_trade(state, "stranger") == before
    obj(obj(obj(state["trade"])["offers"])["edwin-bread"])["active"] = False
    assert perceive_trade(state, "stranger")["offers"] == []
    assert perceive_trade(resource_world(), "stranger")["offers"] == []


def test_resource_composition_is_explicit_and_schedule_is_versioned_and_finite() -> None:
    assert set(resource_world()) - set(initial_world()) == {"inventory", "survival", "trade"}
    for social in (False, True):
        kernel = create_alderwick_kernel(social=social)
        kernel.boot()
        try:
            app, _ = create_alderwick_application(kernel, social=social)
            encoded = canonical_json(app.game_for("stranger").observe().content)
            assert all(name not in encoded for name in ("survival", "inventory", "trade"))
            game = app.game_for("stranger")
            assert (
                game.submit(
                    live_intent(game, "BUY", {"offer_id": "edwin-bread", "quantity": 1})
                ).reason_code
                == "UNKNOWN_ACTION"
            )
            with pytest.raises(ValueError):
                create_alderwick_application(kernel, resources=True)
        finally:
            kernel.close()
    kernel = create_alderwick_kernel(resources=True)
    kernel.boot()
    try:
        generic, _ = create_application(kernel)
        assert "inventory" not in canonical_json(generic.game_for("stranger").observe().content)
        for event in resource_schedule().events:
            kernel.schedule(event)
        kernel.advance_to(20)
        before = kernel.state_snapshot
        kernel.advance_to(100)
        assert kernel.state_snapshot == before
        assert kernel.pending_scheduled_events == ()
    finally:
        kernel.close()
    with pytest.raises(ValueError, match="schedule"):
        ReplayHarness(lambda: create_alderwick_kernel(resources=True)).run(
            ReplayInput(scenario_schedule(), (), 0)
        )


@pytest.mark.parametrize(
    "action,data,reason",
    [
        ("BUY", {"offer_id": "edwin-bread", "quantity": 1}, "OUT_OF_RANGE"),
        ("CONSUME", {"item_id": "bread", "quantity": 1}, "INSUFFICIENT_QUANTITY"),
        ("REST", {"duration": 0}, "INVALID_DURATION"),
    ],
)
def test_live_failure_reason_trace_and_rejected_request_replay(
    action: str,
    data: JsonObject,
    reason: str,
) -> None:
    kernel = create_alderwick_kernel(resources=True)
    kernel.boot()
    try:
        app, research = create_alderwick_application(kernel, resources=True)
        game = app.game_for("stranger")
        before = execution(kernel)
        result = game.submit(live_intent(game, action, data))
        assert result.status == ActionStatus.REJECTED and result.reason_code == reason
        assert execution(kernel) == before
        assert research.action_traces[-1].result == kernel.action_results[-1]
        replay = ReplayHarness(lambda: create_alderwick_kernel(resources=True)).run(
            ReplayInput(resource_schedule(), (research.action_traces[-1].request,), 0)
        )
        assert replay.action_results == kernel.action_results
        assert replay.final_state == kernel.state_snapshot
    finally:
        kernel.close()


def test_social_and_resource_composition_keeps_direct_and_informed_provenance() -> None:
    from journeymap.adapters.social_npc import SocialNpcController
    from journeymap.modules.social.knowledge import project_informed_knowledge
    from journeymap.scenarios.alderwick.social import NPC_ACTIVATIONS, social_initial_knowledge

    kernel = create_alderwick_kernel(resources=True, social=True)
    kernel.boot()
    try:
        for event in resource_schedule().events:
            kernel.schedule(event)
        app, research = create_alderwick_application(kernel, resources=True, social=True)
        kernel.advance_to(3)
        for actor in NPC_ACTIVATIONS:
            game = app.game_for(actor)
            assert (
                game.submit(SocialNpcController().decide(game.observe())).status
                == ActionStatus.SUCCEEDED
            )
        replay = ReplayHarness(lambda: create_alderwick_kernel(resources=True, social=True)).run(
            ReplayInput(
                resource_schedule(),
                tuple(t.request for t in research.action_traces),
                kernel.simulation_time,
            )
        )
        assert replay.events == kernel.events and replay.final_state == kernel.state_snapshot
        projected = KnowledgeProjection(
            KnowledgeLedger(
                kernel.manifest.run_id, 0, social_initial_knowledge(kernel.manifest.run_id)
            ),
            replay.events,
            project_bridge_knowledge,
            event_rules=(project_informed_knowledge,),
        )
        assert projected.history() == app.knowledge_snapshot().history()
        assert any(
            record.source_kind == "INFORMED" for record in projected.for_actor("thomas").history()
        )
    finally:
        kernel.close()


def test_live_failed_and_rejected_resource_requests_replay_with_successes() -> None:
    from test_resources import kernel_for

    from journeymap.core.scheduler import ScenarioSchedule, ScheduledEventSpec

    changed = local_world()["inventory"]
    obj(obj(changed)["owners"])["edwin"] = {"bread": 0}
    schedule = ScenarioSchedule(
        "empty",
        "1",
        (
            ScheduledEventSpec(1, 0, "SurvivalTick", 1, {}),
            ScheduledEventSpec(1, 10, "TestReplace", 1, {"inventory": changed}),
            ScheduledEventSpec(2, 0, "SurvivalTick", 1, {}),
        ),
    )
    kernel = kernel_for()
    try:
        for scheduled in schedule.events:
            kernel.schedule(scheduled)
        pipeline = ObservationPipeline()
        pipeline.register(
            priority=0,
            module_id="test",
            contributor_id="scoped",
            contributor=lambda context: context.perceived,
        )
        app, research = create_application(
            kernel, pipeline=pipeline, perception_extension=perceive_resources
        )
        game = app.game_for("stranger")
        failed = game.submit(live_intent(game, "BUY", {"offer_id": "edwin-bread", "quantity": 1}))
        assert failed.status == ActionStatus.FAILED and failed.reason_code == "INSUFFICIENT_STOCK"
        rejected = game.submit(live_intent(game, "BUY", {"offer_id": "edwin-bread", "quantity": 1}))
        assert rejected.status == ActionStatus.REJECTED
        assert (
            game.submit(live_intent(game, "CONSUME", {"item_id": "bread", "quantity": 1})).status
            == ActionStatus.SUCCEEDED
        )
        assert (
            game.submit(live_intent(game, "REST", {"duration": 1})).status == ActionStatus.SUCCEEDED
        )
        replay = ReplayHarness(lambda: kernel_for(boot=False)).run(
            ReplayInput(schedule, tuple(trace.request for trace in research.action_traces), 3)
        )
        assert replay == ReplayReport(
            kernel.action_results,
            kernel.system_event_outcomes,
            kernel.events,
            kernel.state_snapshot,
            kernel.state_digest,
            3,
            kernel.rng_snapshot.draw_count,
        )
        assert len(research.action_traces) == 4
        assert not any(event.event_type == "ItemPurchased" for event in kernel.events)
    finally:
        kernel.close()
