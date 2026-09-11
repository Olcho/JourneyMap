"""UTF-8 content budget, section identity/order and long-history fail-closed rules."""

from dataclasses import fields
from itertools import permutations

import pytest
from test_game_and_research import execution, make_kernel

from journeymap.application.observations import DEFAULT_OBSERVATION_BUDGET_BYTES, ObservationHistory, ObservationPipeline
from journeymap.bootstrap import create_alderwick_application, create_alderwick_kernel, create_application
from journeymap.core.canonical import JsonObject, canonical_json, state_digest
from journeymap.core.controller import GameSubmissionError
from journeymap.core.handlers import ActionRequest, ActionStatus
from journeymap.core.observations import Observation, PerceptionContext, validate_observation_v1
from journeymap.modules.knowledge import KnowledgeRecord


def context() -> PerceptionContext:
    return PerceptionContext("run", "a", 0, {}, {})


def test_observation_v1_golden_content_digest_and_section_order() -> None:
    expected: JsonObject = {"sections": [
        {"module_id": "a", "contributor_id": "first", "content": {"한": "글"}},
        {"module_id": "b", "contributor_id": "second", "content": {}},
    ]}
    records = []
    for order in permutations(((1, "b", "second"), (0, "a", "first"))):
        pipeline = ObservationPipeline()
        for priority, module, contributor in order:
            def output(c: PerceptionContext, identity: str = module) -> JsonObject:
                return {"한": "글"} if identity == "a" else {}
            pipeline.register(priority=priority, module_id=module, contributor_id=contributor, contributor=output)
        record = ObservationHistory("run").generate(context(), pipeline)
        validate_observation_v1(record)
        assert record.content == expected and record.content_digest == state_digest(expected)
        assert (record.observation_id, record.run_id, record.actor_id, record.observation_sequence, record.simulation_time, record.schema_version) == (
            "run:observation:00000001", "run", "a", 1, 0, 1,
        )
        assert {f.name for f in fields(record) if not f.name.startswith("_")} == {
            "observation_id", "run_id", "actor_id", "observation_sequence", "simulation_time", "schema_version", "content_digest",
        }
        records.append(record)
    assert records[0] == records[1]


@pytest.mark.parametrize("extra", ["a", "한", ["item"]])
def test_exact_utf8_boundary_overflow_and_atomic_history(extra: object) -> None:
    output: JsonObject = {"text": "한글"}
    content: JsonObject = {"sections": [{"module_id": "m", "contributor_id": "c", "content": output}]}
    size = len(canonical_json(content).encode("utf-8"))
    assert size > len(canonical_json(content))
    pipeline = ObservationPipeline(max_content_bytes=size)
    pipeline.register(priority=0, module_id="m", contributor_id="c", contributor=lambda c: output)
    history = ObservationHistory("run")
    first = history.generate(context(), pipeline)
    if isinstance(extra, str):
        output["text"] = "한글" + extra
    else:
        output["items"] = ["item"]
    with pytest.raises(ValueError, match="budget"):
        history.generate(context(), pipeline)
    assert history.records == (first,)
    output.clear()
    output["text"] = "한글"
    assert history.generate(context(), pipeline).observation_sequence == 2


def test_default_65536_boundary_and_one_byte_over_preserve_live_world() -> None:
    assert DEFAULT_OBSERVATION_BUDGET_BYTES == 65_536
    output: JsonObject = {"text": ""}
    content: JsonObject = {"sections": [{"module_id": "m", "contributor_id": "c", "content": output}]}
    overhead = len(canonical_json(content).encode("utf-8"))
    output["text"] = "x" * (65_536 - overhead)
    pipeline = ObservationPipeline()
    pipeline.register(priority=0, module_id="m", contributor_id="c", contributor=lambda c: output)
    kernel = make_kernel()
    kernel.boot()
    try:
        app, research = create_application(kernel, pipeline=pipeline)
        before = execution(kernel)
        observation = app.game_for("a").observe()
        assert len(canonical_json(observation.content).encode("utf-8")) == 65_536
        output["text"] = str(output["text"]) + "x"
        for _ in range(2):
            with pytest.raises(GameSubmissionError, match="^OBSERVATION_UNAVAILABLE$"):
                app.game_for("a").observe()
            assert execution(kernel) == before
            assert research.observations == (observation,)
        output.clear()
        assert app.game_for("a").observe().observation_sequence == 2
    finally:
        kernel.close()


@pytest.mark.parametrize("budget", [True, 0, -1, 1.0])
def test_budget_requires_a_positive_integer(budget: int) -> None:
    with pytest.raises(ValueError):
        ObservationPipeline(max_content_bytes=budget)


def test_duplicate_visible_identity_cannot_hide_behind_different_priority() -> None:
    pipeline = ObservationPipeline()
    pipeline.register(priority=0, module_id="m", contributor_id="c", contributor=lambda c: {})
    for priority in (0, 1):
        with pytest.raises(ValueError, match="duplicate"):
            pipeline.register(priority=priority, module_id="m", contributor_id="c", contributor=lambda c: {})


@pytest.mark.parametrize("content", [
    {}, {"sections": {}}, {"sections": [], "world": {}},
    {"sections": [{"content": {}}]},
    {"sections": [{"module_id": "m", "contributor_id": "c", "content": []}]},
    {"sections": [{"module_id": "m", "contributor_id": "c", "content": {}, "debug": {}}]},
    {"sections": [{"module_id": "m", "contributor_id": "c", "content": {}}] * 2},
])
def test_reader_rejects_ambiguous_or_malformed_section_envelope(content: JsonObject) -> None:
    with pytest.raises(ValueError):
        validate_observation_v1(Observation("r", "a", 1, 0, content))


@pytest.mark.parametrize("count", [40, 250])
def test_knowledge_long_history_order_and_overflow_never_delete_records(count: int) -> None:
    outputs = []
    for reverse in (False, True):
        kernel = make_kernel()
        kernel.boot()
        try:
            records = tuple(KnowledgeRecord(
                f"k{i:04d}", "run-m3", "a", "bridge", "claim", "x" * 100,
                "INITIAL", "allowed", 0,
            ) for i in range(count))
            app, research = create_application(kernel, initial_knowledge=reversed(records) if reverse else records)
            before = execution(kernel)
            game = app.game_for("a")
            if count == 250:
                for _ in range(2):
                    with pytest.raises(GameSubmissionError, match="^OBSERVATION_UNAVAILABLE$"):
                        game.observe()
                assert research.observations == ()
                # Another actor still receives the first successful run sequence.
                assert app.game_for("b").observe().observation_sequence == 1
            else:
                observation = game.observe()
                outputs.append(observation)
                assert all(record.knowledge_record_id in canonical_json(observation.content) for record in records)
            assert execution(kernel) == before
            assert len(research.knowledge_history("a")) == count
        finally:
            kernel.close()
    if outputs:
        assert outputs[0] == outputs[1]


def test_social_history_growth_is_deterministic_and_full_research_is_preserved() -> None:
    outcomes = []
    for _ in range(2):
        kernel = create_alderwick_kernel(social=True)
        kernel.boot()
        try:
            app, research = create_alderwick_application(kernel, social=True)
            # Edwin joins Thomas via an ordinary MOVE; no fixture/world writes.
            sender = app.game_for("edwin")
            initial = sender.observe()
            sender.submit(ActionRequest("move", initial.run_id, "edwin", initial.observation_id, 0, "MOVE", 1, {"route_id": "bakery-to-village-square"}))
            observed = sender.observe()
            target = app.game_for("thomas")
            last_ok = target.observe()
            for i in range(40):
                receipt = sender.submit(ActionRequest(
                    f"request-{i}", observed.run_id, "edwin", observed.observation_id,
                    kernel.simulation_time, "REQUEST", 1,
                    {"target_actor_id": "thomas", "request_kind": "help", "request_payload": {"text": "x" * 2000}},
                ))
                assert receipt.status is ActionStatus.SUCCEEDED
                if i == 5:
                    last_ok = target.observe()
            before = execution(kernel)
            history_before = research.observations
            for _ in range(2):
                with pytest.raises(GameSubmissionError, match="^OBSERVATION_UNAVAILABLE$"):
                    target.observe()
            assert research.observations == history_before
            assert execution(kernel) == before and len(kernel.events) == 41
            assert target is not None and last_ok.content
            assert sender.observe().observation_sequence == len(history_before) + 1
            outcomes.append((kernel.events, research.knowledge_history("thomas"), history_before))
        finally:
            kernel.close()
    assert outcomes[0] == outcomes[1]
