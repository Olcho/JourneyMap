"""Additional M5 boundary and adversarial recovery audit."""

from dataclasses import fields
from pathlib import Path

import pytest
from test_alderwick import execution, obj, section
from test_architecture import imported_names
from test_social import factory
from test_social_information import action, direct, inform, meet, rebuild, send

from journeymap.application.observations import ObservationPipeline
from journeymap.bootstrap import (
    create_alderwick_application,
    create_alderwick_kernel,
    create_application,
)
from journeymap.core.canonical import JsonObject, canonical_json
from journeymap.core.controller import GameSubmissionError
from journeymap.core.events import EventEnvelope, EventSourceKind
from journeymap.core.handlers import ActionStatus, ActionValidationError
from journeymap.core.observations import PerceptionContext
from journeymap.core.replay import ReplayHarness, ReplayInput
from journeymap.core.scheduler import ScenarioSchedule, ScheduledEventSpec
from journeymap.modules.knowledge import KnowledgeLedger, KnowledgeRecord
from journeymap.modules.knowledge.projection import KnowledgeProjection
from journeymap.modules.social.knowledge import project_informed_knowledge
from journeymap.scenarios.alderwick.fixture import scenario_schedule
from journeymap.scenarios.alderwick.knowledge import project_bridge_knowledge


def test_same_tick_record_after_observation_requires_actual_visible_membership() -> None:
    kernel = create_alderwick_kernel(social=True)
    kernel.boot()
    try:
        kernel.advance_to(3)
        kernel.schedule(scenario_schedule(3).events[0])
        app, research = create_alderwick_application(kernel, social=True)
        game = app.game_for("hugh")
        old = game.observe()
        assert direct_records(research.knowledge_history("hugh")) == ()
        kernel.advance_to(3)
        claim = direct(research)
        assert claim.learned_at == old.simulation_time == 3
        request = action(
            old,
            "INFORM",
            {"target_actor_id": "thomas", "claim_record_id": claim.knowledge_record_id},
        )
        before = execution(kernel)
        assert game.submit(request).reason_code == "INVALID_CLAIM_REFERENCE"
        assert execution(kernel) == before
        # Membership now passes; canonical range validation is the next boundary.
        request = action(game.observe(), "INFORM", request.payload)
        assert game.submit(request).reason_code == "OUT_OF_RANGE"
        assert all(k.source_kind != "INFORMED" for k in research.knowledge_history("thomas"))
    finally:
        kernel.close()


def direct_records(records: tuple[KnowledgeRecord, ...]) -> tuple[KnowledgeRecord, ...]:
    return tuple(record for record in records if record.source_kind == "DIRECT_OBSERVATION")


def test_live_failure_and_request_stream_replay_exclude_boundary_denials() -> None:
    kernel = factory()
    kernel.boot()
    try:
        known = KnowledgeRecord(
            "claim",
            "run-default",
            "hugh",
            "bridge",
            "condition",
            "intact",
            "INITIAL",
            "author:1",
            0,
        )
        app, research = create_application(kernel, initial_knowledge=(known,), social=True)
        game = app.game_for("hugh")
        spec = ScheduledEventSpec(2, 0, "ChangeTarget", 1, {"kind": "move"})
        kernel.schedule(spec)
        assert (
            game.submit(
                action(
                    game.observe(),
                    "INFORM",
                    {"target_actor_id": "thomas", "claim_record_id": "missing"},
                )
            ).reason_code
            == "INVALID_CLAIM_REFERENCE"
        )
        send(
            game,
            "REQUEST",
            {"target_actor_id": "thomas", "request_kind": "test", "request_payload": {}},
        )
        assert (
            game.submit(
                action(
                    game.observe(),
                    "ASK",
                    {
                        "target_actor_id": "absent",
                        "subject_ref": "bridge",
                        "predicate": "condition",
                    },
                )
            ).reason_code
            == "UNKNOWN_TARGET"
        )
        result = game.submit(
            action(
                game.observe(), "INFORM", {"target_actor_id": "thomas", "claim_record_id": "claim"}
            )
        )
        assert result.status == ActionStatus.FAILED and result.reason_code == "OUT_OF_RANGE"
        assert research.knowledge_history("thomas") == ()
        inputs = ReplayInput(
            ScenarioSchedule("empty", "1", (spec,)),
            tuple(t.request for t in research.action_traces if t.result is not None),
            kernel.simulation_time,
        )
        report = ReplayHarness(factory).run(inputs)
        assert report == ReplayHarness(factory).run(inputs)
        assert report.action_results == research.action_results
        assert [r.status for r in report.action_results] == [
            ActionStatus.SUCCEEDED,
            ActionStatus.REJECTED,
            ActionStatus.FAILED,
        ]
        assert report.events == research.events
        assert report.final_state == research.world_snapshot
        assert report.final_state_digest == research.state_digest
        assert report.final_simulation_time == research.simulation_time == 2
        rebuilt = KnowledgeProjection(
            KnowledgeLedger("run-default", 0, (known,)),
            report.events,
            event_rules=(project_informed_knowledge,),
        )
        assert rebuilt.history() == (known,)
        assert len(research.action_traces) == 4 and len(report.action_results) == 3
    finally:
        kernel.close()


def test_social_scope_is_filtered_before_even_an_echo_contributor() -> None:
    kernel = create_alderwick_kernel(social=True)
    kernel.boot()
    try:
        kernel.schedule(scenario_schedule().events[0])
        app, _ = create_alderwick_application(kernel, social=True)
        meet(kernel, app)
        send(
            app.game_for("thomas"),
            "REQUEST",
            {
                "target_actor_id": "hugh",
                "request_kind": "private",
                "request_payload": {"secret": "ONLY-HUGH"},
            },
        )
        received: list[PerceptionContext] = []

        def echo(context: PerceptionContext) -> JsonObject:
            received.append(context)
            return {"perceived": context.perceived, "known": context.known}

        pipeline = ObservationPipeline()
        pipeline.register(priority=0, module_id="test", contributor_id="echo", contributor=echo)
        other, _ = create_application(kernel, pipeline=pipeline, social=True)
        for actor in ("marta", "stranger", "edwin"):
            observed = other.game_for(actor).observe()
            assert "ONLY-HUGH" not in canonical_json(observed.content)
            assert received[-1].perceived["social"] == {"interactions": []}
            assert set(received[-1].perceived) == {"self", "movement", "social"}
        other.game_for("hugh").observe()
        assert "ONLY-HUGH" in canonical_json(received[-1].perceived)
        assert set(received[-1].perceived) == {"self", "movement", "social"}
        assert {field.name for field in fields(received[-1])} == {
            "run_id",
            "actor_id",
            "simulation_time",
            "perceived",
            "known",
        }
        received[-1].perceived.clear()
        assert "ONLY-HUGH" in canonical_json(other.game_for("hugh").observe().content)
    finally:
        kernel.close()


@pytest.mark.parametrize("error_type", [ValueError, ActionValidationError])
def test_projection_read_failure_and_claim_validation_failure_are_traced_without_commit(
    error_type: type[ValueError],
) -> None:
    kernel = create_alderwick_kernel(social=True)
    kernel.boot()
    failing = False

    def projector(events: tuple[EventEnvelope, ...]) -> tuple[KnowledgeRecord, ...]:
        if failing:
            raise error_type("SECRET-PROJECTION")
        return project_bridge_knowledge(events)

    try:
        kernel.schedule(scenario_schedule().events[0])
        app, research = create_application(kernel, knowledge_projector=projector, social=True)
        meet(kernel, app)
        game = app.game_for("hugh")
        observed = game.observe()
        request = action(
            observed,
            "INFORM",
            {"target_actor_id": "thomas", "claim_record_id": direct(research).knowledge_record_id},
        )
        before = execution(kernel)
        failing = True
        with pytest.raises(GameSubmissionError, match=r"^KNOWLEDGE_UNAVAILABLE$"):
            game.submit(request)
        assert research.action_traces[-1].error_type == error_type.__name__
        assert research.action_traces[-1].result is None
        with pytest.raises(GameSubmissionError, match=r"^OBSERVATION_UNAVAILABLE$"):
            game.observe()
        assert research.observations[-1] == observed and execution(kernel) == before
        failing = False
        assert game.observe().observation_sequence == observed.observation_sequence + 1
        assert game.submit(request).status == ActionStatus.SUCCEEDED
    finally:
        kernel.close()


def test_prefix_views_and_detachment_include_initial_and_prior_indirect_records() -> None:
    kernel = create_alderwick_kernel(social=True)
    kernel.boot()
    try:
        kernel.schedule(scenario_schedule().events[0])
        app, research = create_alderwick_application(kernel, social=True)
        meet(kernel, app)
        prefix = kernel.events
        inform(app, direct(research), "thomas")
        history = rebuild(kernel.events).history()
        assert all(record.source_kind != "INFORMED" for record in rebuild(prefix).history())
        snapshot = rebuild(kernel.events)
        for record in snapshot.history():
            if isinstance(record.value, dict):
                record.value.clear()
        for event in kernel.events:
            event.payload.clear()
        assert snapshot.history() == history == rebuild(kernel.events).history()
        incoming = obj(section(app.game_for("thomas").observe(), "social")["social"])[
            "interactions"
        ]
        assert isinstance(incoming, list)
        assert all("claim_record_id" not in obj(item) for item in incoming)
    finally:
        kernel.close()


def test_event_rule_output_provenance_and_input_isolation() -> None:
    event = EventEnvelope(
        "e1", "run", 1, 1, "Something", 1, EventSourceKind.ACTION, "a1", "t1", "t1", "a1", {}
    )

    def bad(
        current: EventEnvelope, previous: tuple[EventEnvelope, ...], knowledge: KnowledgeLedger
    ) -> tuple[KnowledgeRecord, ...]:
        return (KnowledgeRecord("k1", "run", "a", "x", "p", "v", "INFORMED", "future", 1),)

    with pytest.raises(ValueError, match="current committed"):
        KnowledgeProjection(KnowledgeLedger("run", 0), (event,), event_rules=(bad,))

    def mutate(
        current: EventEnvelope, previous: tuple[EventEnvelope, ...], knowledge: KnowledgeLedger
    ) -> tuple[KnowledgeRecord, ...]:
        current.payload["hidden"] = True
        knowledge.run_id = "wrong"
        return ()

    def check(
        current: EventEnvelope, previous: tuple[EventEnvelope, ...], knowledge: KnowledgeLedger
    ) -> tuple[KnowledgeRecord, ...]:
        assert current.payload == {} and knowledge.run_id == "run"
        return ()

    assert (
        KnowledgeProjection(
            KnowledgeLedger("run", 0), (event,), event_rules=(mutate, check)
        ).history()
        == ()
    )


def test_social_npc_and_replay_preserve_import_and_schema_boundaries() -> None:
    root = Path(__file__).parents[1] / "src" / "journeymap"
    assert imported_names(root / "adapters/social_npc.py") <= {
        "journeymap.core.canonical",
        "journeymap.core.handlers",
        "journeymap.core.observations",
    }
    assert imported_names(root / "modules/social/handlers.py").isdisjoint(
        {
            "journeymap.modules.knowledge",
            "journeymap.core.kernel",
            "journeymap.application.session",
        }
    )
    for path in (root / "core").glob("*.py"):
        assert not any("social" in name for name in imported_names(path))
    assert {field.name for field in fields(ReplayInput)} == {"schedule", "actions", "advance_to"}


def test_social_composition_is_explicit_and_disabled_game_cannot_bypass_authority() -> None:
    kernel = create_alderwick_kernel(social=True)
    kernel.boot()
    try:
        app, research = create_application(kernel)
        game = app.game_for("hugh")
        before = execution(kernel)
        assert (
            game.submit(
                action(
                    game.observe(),
                    "INFORM",
                    {"target_actor_id": "thomas", "claim_record_id": "forged"},
                )
            ).reason_code
            == "SOCIAL_UNAVAILABLE"
        )
        assert execution(kernel) == before and research.action_traces[-1].result is None
    finally:
        kernel.close()
