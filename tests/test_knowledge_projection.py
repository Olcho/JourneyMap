"""Runtime knowledge identity, causal evidence, duplicates and reconstruction."""

from dataclasses import replace

import pytest
from test_alderwick import intent, obj

from journeymap.bootstrap import create_alderwick_application, create_alderwick_kernel
from journeymap.core.events import EventEnvelope
from journeymap.modules.knowledge import KnowledgeLedger, KnowledgeRecord
from journeymap.modules.knowledge.projection import KnowledgeProjection
from journeymap.scenarios.alderwick.bridge import bridge_witnesses
from journeymap.scenarios.alderwick.fixture import initial_world, scenario_schedule
from journeymap.scenarios.alderwick.knowledge import project_bridge_knowledge


def committed_events() -> tuple[EventEnvelope, ...]:
    kernel = create_alderwick_kernel()
    kernel.boot()
    try:
        kernel.schedule(scenario_schedule().events[0])
        kernel.advance_to(3)
        app, _ = create_alderwick_application(kernel)
        game = app.game_for("thomas")
        game.submit(intent(game, "village-square-to-east-road"))
        game.submit(intent(game, "east-road-to-village-square"))
        game.submit(intent(game, "village-square-to-east-road"))
        return kernel.events
    finally:
        kernel.close()


def initial() -> KnowledgeLedger:
    return KnowledgeLedger("run-alderwick", 0)


def test_rebuild_is_repeatable_deduplicated_detached_and_preserves_initial_claims() -> None:
    events = committed_events()
    prior = KnowledgeRecord(
        "initial-claim",
        "run-alderwick",
        "hugh",
        "east-bridge",
        "condition",
        "intact",
        "INITIAL",
        "author:claim",
        0,
    )
    ledger = KnowledgeLedger("run-alderwick", 0, (prior,))
    first = KnowledgeProjection(ledger, events, project_bridge_knowledge)
    expected = first.history()
    assert len(expected) == 3
    assert first.for_actor("hugh").history()[0] == prior
    direct = first.for_actor("hugh").history()[1]
    assert direct.supersedes_id is None  # retain conflicting claim; no automatic belief winner
    assert direct.knowledge_record_id == f"{events[0].event_id}:alderwick-direct-v1:00000001"
    assert direct.source_ref == events[0].event_id
    assert len(first.for_actor("thomas").history()) == 1
    assert first.for_actor("thomas").history()[0].source_ref == events[1].event_id
    assert first.for_actor("marta").history() == ()
    for _ in range(3):
        assert KnowledgeProjection(ledger, events, project_bridge_knowledge).history() == expected
    assert ledger.history() == (prior,)
    for event in events:
        event.payload.clear()
    assert first.history() == expected
    assert first.for_actor("hugh").history() == expected[:2]


def test_prefix_projection_does_not_use_future_truth_or_later_positions() -> None:
    kernel = create_alderwick_kernel()
    kernel.boot()
    try:
        kernel.schedule(scenario_schedule(5).events[0])
        app, research = create_alderwick_application(kernel)
        game = app.game_for("thomas")
        game.submit(intent(game, "village-square-to-east-road"))
        # Seeing intact truth is perceived, not a collapsed knowledge acquisition.
        assert research.knowledge_history("thomas") == ()
        prefix = kernel.events
        game.submit(intent(game, "east-road-to-village-square"))
        kernel.advance_to(5)
        assert research.knowledge_history("thomas") == ()
        assert KnowledgeProjection(initial(), prefix, project_bridge_knowledge).history() == ()
        assert kernel.events[-1].payload["witness_actor_ids"] == ["hugh"]
        game.submit(intent(game, "village-square-to-east-road"))
        acquired = research.knowledge_history("thomas")[0]
        assert acquired.learned_at == 7 and acquired.source_ref == kernel.events[-1].event_id
        assert KnowledgeProjection(initial(), kernel.events, project_bridge_knowledge).for_actor(
            "thomas"
        ).history() == (acquired,)
    finally:
        kernel.close()


def test_witness_snapshot_order_uses_occurrence_positions_not_input_insertion_order() -> None:
    state = initial_world()
    positions = obj(obj(state["movement"])["positions"])
    obj(positions["thomas"])["location_id"] = "east-bridge"
    assert bridge_witnesses(state) == ("hugh", "thomas")
    state["entities"] = dict(reversed(tuple(obj(state["entities"]).items())))
    assert bridge_witnesses(state) == ("hugh", "thomas")
    event = committed_events()[0]
    event.payload["witness_actor_ids"] = ["hugh", "thomas"]
    acquired = KnowledgeProjection(initial(), (event,), project_bridge_knowledge)
    assert [k.actor_id for k in acquired.history()] == ["hugh", "thomas"]
    assert [k.knowledge_record_id for k in acquired.history()] == [
        f"{event.event_id}:alderwick-direct-v1:{index:08d}" for index in (1, 2)
    ]
    obj(positions["hugh"])["location_id"] = "inn"
    assert acquired.for_actor("hugh").history()[0].source_ref == event.event_id


@pytest.mark.parametrize("fault", ["duplicate", "gap", "order", "foreign", "time", "id"])
def test_corrupt_or_partial_event_log_is_rejected_instead_of_silently_reprojected(
    fault: str,
) -> None:
    events = committed_events()
    if fault == "duplicate":
        events = (*events, events[-1])
    elif fault == "gap":
        events = events[1:]
    elif fault == "order":
        events = tuple(reversed(events))
    elif fault == "foreign":
        events = (replace(events[0], run_id="other"), *events[1:])
    elif fault == "time":
        events = (events[0], replace(events[1], simulation_time=0), *events[2:])
    else:
        events = (events[0], replace(events[1], event_id=events[0].event_id), *events[2:])
    with pytest.raises(ValueError, match="complete ordered"):
        KnowledgeProjection(initial(), events, project_bridge_knowledge)


@pytest.mark.parametrize("fault", ["missing-source", "wrong-time", "wrong-run", "duplicate-id"])
def test_bad_projector_output_never_returns_partial_snapshot(fault: str) -> None:
    events = committed_events()

    def bad(log: tuple[EventEnvelope, ...]) -> tuple[KnowledgeRecord, ...]:
        records = project_bridge_knowledge(log)
        record = records[0]
        if fault == "missing-source":
            record = replace(record, source_ref="not-committed")
        elif fault == "wrong-time":
            record = replace(record, learned_at=0)
        elif fault == "wrong-run":
            record = replace(record, run_id="other")
        else:
            return (*records, record)
        return (record, *records[1:])

    with pytest.raises(ValueError):
        KnowledgeProjection(initial(), events, bad)
    assert len(KnowledgeProjection(initial(), events, project_bridge_knowledge).history()) == 2


@pytest.mark.parametrize(
    "fault", ["version", "condition", "witness-type", "witness-order", "witness-duplicate"]
)
def test_unsupported_or_malformed_collapse_evidence_fails_closed(fault: str) -> None:
    event = committed_events()[0]
    if fault == "version":
        event = replace(event, schema_version=2)
    elif fault == "condition":
        event.payload["condition"] = "intact"
    elif fault == "witness-type":
        event.payload["witness_actor_ids"] = [False]
    elif fault == "witness-order":
        event.payload["witness_actor_ids"] = ["thomas", "hugh"]
    else:
        event.payload["witness_actor_ids"] = ["hugh", "hugh"]
    with pytest.raises(ValueError):
        KnowledgeProjection(initial(), (event,), project_bridge_knowledge)
