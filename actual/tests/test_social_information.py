"""M5 live authority, social information provenance and replay research gate."""

from collections.abc import Iterator
from dataclasses import replace

import pytest
from test_alderwick import execution, intent, obj, section

from journeymap.adapters.social_npc import SocialNpcController
from journeymap.application.observations import ObservationPipeline
from journeymap.application.research import ResearchView
from journeymap.application.session import SimulationApplication
from journeymap.bootstrap import (
    create_alderwick_application,
    create_alderwick_kernel,
    create_application,
)
from journeymap.core.canonical import JsonObject, canonical_json
from journeymap.core.controller import GamePort, GameSubmissionError
from journeymap.core.events import EventBus, EventEnvelope
from journeymap.core.handlers import ActionRequest, ActionStatus
from journeymap.core.kernel import SimulationKernel
from journeymap.core.observations import Observation, PerceptionContext
from journeymap.core.replay import ReplayHarness, ReplayInput
from journeymap.modules.knowledge import KnowledgeLedger, KnowledgeRecord
from journeymap.modules.knowledge.projection import KnowledgeProjection
from journeymap.modules.social.knowledge import project_informed_knowledge
from journeymap.scenarios.alderwick.fixture import scenario_schedule
from journeymap.scenarios.alderwick.knowledge import project_bridge_knowledge
from journeymap.scenarios.alderwick.social import NPC_ACTIVATIONS, social_initial_knowledge


@pytest.fixture
def live() -> Iterator[tuple[SimulationKernel, SimulationApplication, ResearchView]]:
    kernel = create_alderwick_kernel(social=True)
    kernel.boot()
    kernel.schedule(scenario_schedule().events[0])
    app, research = create_alderwick_application(kernel, social=True)
    try:
        yield kernel, app, research
    finally:
        kernel.close()


def action(observation: Observation, kind: str, data: JsonObject) -> ActionRequest:
    return ActionRequest(
        f"{observation.observation_id}:{kind}",
        observation.run_id,
        observation.actor_id,
        observation.observation_id,
        observation.simulation_time,
        kind,
        1,
        data,
    )


def send(game: GamePort, kind: str, data: JsonObject) -> ActionRequest:
    request = action(game.observe(), kind, data)
    assert game.submit(request).status == ActionStatus.SUCCEEDED
    return request


def meet(kernel: SimulationKernel, app: SimulationApplication) -> None:
    kernel.advance_to(3)
    game = app.game_for("hugh")
    assert game.submit(intent(game, "east-road-to-village-square")).status == ActionStatus.SUCCEEDED


def direct(research: ResearchView) -> KnowledgeRecord:
    return next(
        record for record in research.knowledge_history("hugh") if record.predicate == "condition"
    )


def inform(app: SimulationApplication, record: KnowledgeRecord, target: str) -> ActionRequest:
    return send(
        app.game_for(record.actor_id),
        "INFORM",
        {
            "target_actor_id": target,
            "claim_record_id": record.knowledge_record_id,
        },
    )


def rebuild(events: tuple[EventEnvelope, ...]) -> KnowledgeProjection:
    return KnowledgeProjection(
        KnowledgeLedger("run-alderwick", 0, social_initial_knowledge("run-alderwick")),
        events,
        project_bridge_knowledge,
        event_rules=(project_informed_knowledge,),
    )


def test_npc_closed_loop_replay_fresh_run_and_research_chain() -> None:
    def run() -> tuple[object, ...]:
        kernel = create_alderwick_kernel(social=True)
        kernel.boot()
        try:
            kernel.schedule(scenario_schedule().events[0])
            app, research = create_alderwick_application(kernel, social=True)
            kernel.advance_to(3)
            controller = SocialNpcController()
            assert {name for name in dir(controller) if not name.startswith("_")} == {"decide"}
            assert not hasattr(controller, "__dict__")
            for actor in NPC_ACTIVATIONS:
                game = app.game_for(actor)
                observed = game.observe()
                request = controller.decide(observed)
                assert request == controller.decide(observed)
                if request.action_type == "ASK":
                    assert "collapsed" not in canonical_json(observed.content)
                before_world = kernel.state_snapshot
                assert game.submit(request).status == ActionStatus.SUCCEEDED
                if request.action_type == "INFORM":
                    assert kernel.state_snapshot == before_world
            traces = research.action_traces
            assert [t.request.action_type for t in traces] == [
                "MOVE",
                "ASK",
                "INFORM",
                "WAIT",
                "WAIT",
            ]
            assert [event.event_type for event in research.events] == [
                "BridgeCollapsed",
                "ActorMoved",
                "ActorAsked",
                "ActorInformed",
            ]
            assert len(research.system_event_outcomes) == 1
            initial, indirect = (
                app.knowledge_snapshot().for_actor("thomas").query("east-bridge", "condition")
            )
            assert (initial.value, indirect.value) == ("intact", "collapsed")
            assert initial.source_kind == "INITIAL" and indirect.source_kind == "INFORMED"
            assert indirect.supersedes_id is None and indirect.learned_at == 7
            source_event = next(e for e in research.events if e.event_id == indirect.source_ref)
            assert source_event.source_ref == traces[2].request.action_request_id
            assert source_event.payload["claim_record_id"] == direct(research).knowledge_record_id
            assert direct(research).source_ref == research.events[0].event_id
            assert source_event.payload["reply_to_event_id"] == research.events[2].event_id
            for trace in traces:
                assert trace.result is not None
                assert trace.request.based_on_observation_id in {
                    o.observation_id for o in research.observations
                }
            assert "collapsed" in canonical_json(research.observations[3].content)
            incoming = obj(section(research.observations[4], "social")["social"])["interactions"]
            assert isinstance(incoming, list) and obj(incoming[0])["answered"] is True
            for actor in ("marta", "edwin", "stranger"):
                assert all(
                    record.predicate != "condition" for record in research.knowledge_history(actor)
                )
                assert section(app.game_for(actor).observe(), "social") == {
                    "social": {"interactions": []}
                }
            inputs = ReplayInput(
                scenario_schedule(), tuple(t.request for t in traces), kernel.simulation_time
            )
            harness = ReplayHarness(lambda: create_alderwick_kernel(social=True))
            report = harness.run(inputs)
            assert report == harness.run(inputs)
            assert report.action_results == research.action_results
            assert report.system_event_outcomes == research.system_event_outcomes
            assert report.events == research.events
            assert report.final_state == research.world_snapshot
            assert report.final_state_digest == research.state_digest
            assert report.final_simulation_time == research.simulation_time == 9
            assert report.rng_draw_count == research.rng_snapshot.draw_count == 0
            for _ in range(3):
                assert rebuild(report.events).history() == app.knowledge_snapshot().history()
            return research.observations, traces, report, rebuild(report.events).history()
        finally:
            kernel.close()

    assert run() == run()


@pytest.mark.parametrize(
    "fault", ["unknown", "foreign-run", "other-actor", "target-record", "future", "unobserved"]
)
def test_live_claim_authority_rejects_unowned_or_unobserved_claims(
    live: tuple[SimulationKernel, SimulationApplication, ResearchView],
    fault: str,
) -> None:
    kernel, app, research = live
    game = app.game_for("hugh")
    old = game.observe()
    if fault != "future":
        meet(kernel, app)
    observation = old if fault == "unobserved" else game.observe()
    claim = "unknown"
    if fault in ("future", "unobserved"):
        claim = "run-alderwick:event:00000001:alderwick-direct-v1:00000001"
    elif fault == "foreign-run":
        claim = "other-run:initial:social:hugh-return"
    elif fault in ("other-actor", "target-record"):
        actor = "stranger" if fault == "other-actor" else "thomas"
        claim = research.knowledge_history(actor)[0].knowledge_record_id
    request = replace(
        action(observation, "INFORM", {"target_actor_id": "thomas", "claim_record_id": claim}),
        submitted_at=kernel.simulation_time,
    )
    before = execution(kernel), app.knowledge_snapshot().history()
    receipt = game.submit(request)
    assert receipt.reason_code == "INVALID_CLAIM_REFERENCE"
    assert (execution(kernel), app.knowledge_snapshot().history()) == before
    assert research.action_traces[-1].boundary_reason == "INVALID_CLAIM_REFERENCE"
    assert research.action_traces[-1].result is None


def test_later_record_acquired_after_observation_is_not_a_capability(
    live: tuple[SimulationKernel, SimulationApplication, ResearchView],
) -> None:
    kernel, app, research = live
    # A due-now Event does not run when observe is called.
    kernel.advance_to(2)
    game = app.game_for("hugh")
    old = game.observe()
    kernel.advance_to(3)
    claim = direct(research)
    forged = replace(
        action(
            old,
            "INFORM",
            {"target_actor_id": "thomas", "claim_record_id": claim.knowledge_record_id},
        ),
        submitted_at=3,
    )
    assert game.submit(forged).reason_code == "INVALID_CLAIM_REFERENCE"
    # A later indirect acquisition also cannot authorize an earlier Observation.
    meet(kernel, app)
    thomas = app.game_for("thomas")
    previous = thomas.observe()
    inform(app, claim, "thomas")
    learned = next(k for k in research.knowledge_history("thomas") if k.source_kind == "INFORMED")
    absent = replace(
        action(
            previous,
            "INFORM",
            {"target_actor_id": "hugh", "claim_record_id": learned.knowledge_record_id},
        ),
        submitted_at=kernel.simulation_time,
    )
    assert thomas.submit(absent).reason_code == "INVALID_CLAIM_REFERENCE"


def test_hidden_or_modified_observation_records_cannot_grant_claim_authority() -> None:
    kernel = create_alderwick_kernel(social=True)
    kernel.boot()
    try:
        initial = social_initial_knowledge(kernel.manifest.run_id)
        for forged in (False, True):
            pipeline = ObservationPipeline()

            def hide(context: PerceptionContext, change: bool = forged) -> JsonObject:
                records = context.known["records"]
                assert isinstance(records, list)
                if not change:
                    return {"records": []}
                obj(records[0])["value"] = "modified-claim"
                return {"records": records}

            pipeline.register(
                priority=0, module_id="knowledge", contributor_id="records", contributor=hide
            )
            app, _ = create_application(
                kernel, initial_knowledge=initial, pipeline=pipeline, social=True
            )
            game = app.game_for("hugh")
            observation = game.observe()
            request = action(
                observation,
                "INFORM",
                {"target_actor_id": "thomas", "claim_record_id": initial[-3].knowledge_record_id},
            )
            assert game.submit(request).reason_code == "INVALID_CLAIM_REFERENCE"
    finally:
        kernel.close()


def test_request_and_ask_only_deliver_to_target_without_answer_or_fulfillment(
    live: tuple[SimulationKernel, SimulationApplication, ResearchView],
) -> None:
    kernel, app, research = live
    meet(kernel, app)
    before = app.knowledge_snapshot().history(), kernel.state_snapshot
    ask = send(
        app.game_for("thomas"),
        "ASK",
        {"target_actor_id": "hugh", "subject_ref": "east-bridge", "predicate": "condition"},
    )
    requested = send(
        app.game_for("thomas"),
        "REQUEST",
        {
            "target_actor_id": "hugh",
            "request_kind": "arbitrary-v1",
            "request_payload": {"set_values": {"secret": [1]}},
        },
    )
    assert (app.knowledge_snapshot().history(), kernel.state_snapshot) == before
    received = obj(section(app.game_for("hugh").observe(), "social")["social"])["interactions"]
    assert isinstance(received, list) and len(received) == 2
    assert obj(received[0])["answered"] is False
    assert obj(received[1])["request_payload"] == requested.payload["request_payload"]
    assert research.events[-2].source_ref == ask.action_request_id
    assert research.events[-1].source_ref == requested.action_request_id
    for actor in ("marta", "edwin", "stranger", "thomas"):
        assert section(app.game_for(actor).observe(), "social") == {"social": {"interactions": []}}
    obj(received[1])["request_payload"] = "tampered"
    assert research.events[-1].payload["request_payload"] == requested.payload["request_payload"]


@pytest.mark.parametrize(
    "fault", ["missing", "not-ask", "wrong-topic", "wrong-target", "unobserved"]
)
def test_reply_reference_is_scoped_to_actual_question(
    live: tuple[SimulationKernel, SimulationApplication, ResearchView],
    fault: str,
) -> None:
    kernel, app, research = live
    meet(kernel, app)
    game = app.game_for("hugh")
    previous = game.observe()
    send(
        app.game_for("thomas"),
        "ASK",
        {
            "target_actor_id": "hugh",
            "subject_ref": "other" if fault == "wrong-topic" else "east-bridge",
            "predicate": "condition",
        },
    )
    reply = research.events[-1].event_id
    if fault == "missing":
        reply = "missing"
    elif fault == "not-ask":
        reply = research.events[0].event_id
    observation = previous if fault == "unobserved" else game.observe()
    request = replace(
        action(
            observation,
            "INFORM",
            {
                "target_actor_id": "marta" if fault == "wrong-target" else "thomas",
                "claim_record_id": direct(research).knowledge_record_id,
                "reply_to_event_id": reply,
            },
        ),
        submitted_at=kernel.simulation_time,
    )
    before = execution(kernel)
    assert game.submit(request).reason_code == "INVALID_REPLY_REFERENCE"
    assert execution(kernel) == before


def test_multihop_provenance_and_stale_claims_are_preserved_without_truth_lookup(
    live: tuple[SimulationKernel, SimulationApplication, ResearchView],
) -> None:
    kernel, app, research = live
    meet(kernel, app)
    inform(app, direct(research), "thomas")
    intermediate = next(
        k for k in research.knowledge_history("thomas") if k.source_kind == "INFORMED"
    )
    thomas = app.game_for("thomas")
    assert thomas.submit(intent(thomas, "village-square-to-inn")).status == ActionStatus.SUCCEEDED
    inform(app, intermediate, "marta")
    stale = next(
        k
        for k in research.knowledge_history("thomas")
        if k.predicate == "condition" and k.value == "intact"
    )
    inform(app, stale, "marta")
    first, second = app.knowledge_snapshot().for_actor("marta").query("east-bridge", "condition")
    assert (first.value, second.value) == ("collapsed", "intact")
    assert first.source_kind == second.source_kind == "INFORMED"
    assert first.supersedes_id is second.supersedes_id is None
    event = next(e for e in research.events if e.event_id == first.source_ref)
    assert event.payload["claim_record_id"] == intermediate.knowledge_record_id
    assert rebuild(research.events).history() == app.knowledge_snapshot().history()
    assert research.knowledge_history("edwin") == ()
    assert obj(obj(research.world_snapshot["alderwick"])["east_bridge"])["condition"] == "collapsed"


def test_delivery_failure_recovers_from_committed_event_without_duplicate_acquisition() -> None:
    bus = EventBus()
    calls: list[str] = []

    def broken(event: EventEnvelope) -> None:
        calls.append(event.event_id)
        raise RuntimeError("PRIVATE-SECRET")

    bus.subscribe(
        event_type="ActorInformed",
        priority=0,
        module_id="test",
        subscriber_id="broken",
        subscriber=broken,
    )
    kernel = create_alderwick_kernel(social=True, event_bus=bus)
    kernel.boot()
    try:
        kernel.schedule(scenario_schedule().events[0])
        app, research = create_alderwick_application(kernel, social=True)
        meet(kernel, app)
        with pytest.raises(GameSubmissionError, match=r"^ENGINE_ERROR$"):
            inform(app, direct(research), "thomas")
        trace = research.action_traces[-1]
        assert trace.error_type == "EventDeliveryError"
        assert trace.result is not None and trace.result.status == ActionStatus.SUCCEEDED
        learned = next(
            k for k in research.knowledge_history("thomas") if k.source_kind == "INFORMED"
        )
        assert learned.source_ref == calls[0]
        for _ in range(3):
            records = section(app.game_for("thomas").observe(), "knowledge")["records"]
            assert isinstance(records, list) and learned.to_json() in records
            assert rebuild(kernel.events).history() == app.knowledge_snapshot().history()
        kernel.advance_to(10)
        assert calls == [learned.source_ref]
        assert (
            len([k for k in research.knowledge_history("thomas") if k.source_kind == "INFORMED"])
            == 1
        )
    finally:
        kernel.close()


@pytest.mark.parametrize(
    "fault",
    ["version", "source-kind", "extra", "missing-claim", "other-owner", "self", "future-prefix"],
)
def test_corrupt_social_projection_evidence_fails_closed(
    live: tuple[SimulationKernel, SimulationApplication, ResearchView],
    fault: str,
) -> None:
    kernel, app, research = live
    meet(kernel, app)
    inform(app, direct(research), "thomas")
    events = list(kernel.events)
    event = events[-1]
    if fault == "version":
        event = replace(event, schema_version=2)
    elif fault == "source-kind":
        event = replace(event, source_kind=events[0].source_kind)
    elif fault == "extra":
        event.payload["value"] = "forged"
    elif fault == "missing-claim":
        event.payload["claim_record_id"] = "missing"
    elif fault == "other-owner":
        event.payload["claim_record_id"] = research.knowledge_history("thomas")[
            0
        ].knowledge_record_id
    elif fault == "self":
        event.payload["target_actor_id"] = "hugh"
    else:
        # Put the transmission before the direct acquisition at the same tick.
        events = [
            replace(event, event_sequence=1, simulation_time=3),
            replace(events[0], event_sequence=2),
        ]
    if fault != "future-prefix":
        events[-1] = event
    with pytest.raises(ValueError):
        rebuild(tuple(events))
    assert rebuild(kernel.events).history() == app.knowledge_snapshot().history()


def test_npc_abstains_on_conflicting_answers_and_has_no_implicit_truth_access(
    live: tuple[SimulationKernel, SimulationApplication, ResearchView],
) -> None:
    kernel, app, research = live
    meet(kernel, app)
    inform(app, direct(research), "thomas")
    send(
        app.game_for("hugh"),
        "ASK",
        {"target_actor_id": "thomas", "subject_ref": "east-bridge", "predicate": "condition"},
    )
    controller = SocialNpcController()
    assert controller.decide(app.game_for("thomas").observe()).action_type == "WAIT"
    assert (
        controller.decide(Observation("run", "actor", 1, 0, {"sections": []})).action_type == "WAIT"
    )
