"""M3 detached envelopes and deterministic post-perception extension contracts."""

from dataclasses import FrozenInstanceError, fields
from itertools import permutations
from typing import cast

import pytest

from journeymap.application.observations import ObservationHistory, ObservationPipeline
from journeymap.core.canonical import CanonicalValueError, JsonObject, JsonValue, state_digest
from journeymap.core.observations import Observation, PerceptionContext


def context() -> PerceptionContext:
    return PerceptionContext("run", "a", 7, {"self": {"entity_id": "a"}}, {"records": []})


def test_envelope_identity_digest_and_deep_immutability() -> None:
    pipeline = ObservationPipeline()
    pipeline.register(
        priority=0, module_id="test", contributor_id="echo", contributor=lambda c: c.perceived
    )
    history = ObservationHistory("run")
    record = history.generate(context(), pipeline)
    assert record.observation_id == "run:observation:00000001"
    assert (record.run_id, record.actor_id, record.simulation_time, record.schema_version) == (
        "run",
        "a",
        7,
        1,
    )
    original = record.content
    delivered = record.content["sections"]
    assert isinstance(delivered, list) and isinstance(delivered[0], dict)
    delivered[0].clear()
    assert record.content == original
    assert record.content_digest == state_digest(original)
    with pytest.raises(FrozenInstanceError):
        record.__setattr__("actor_id", "b")
    assert history.get(record.observation_id) == record
    assert history.get("missing") is None
    assert history.records == (record,)
    assert history.generate(context(), pipeline).observation_sequence == 2
    assert ObservationHistory("run").generate(context(), pipeline) == record
    assert Observation("run", "a", 1, 7, {"b": 1, "a": 2}).content_digest == (
        Observation("run", "a", 1, 7, {"a": 2, "b": 1}).content_digest
    )


def test_all_ordering_key_parts_are_stable_across_registration_permutations() -> None:
    keys = [(2, "a", "c"), (1, "b", "a"), (1, "a", "b"), (1, "a", "a")]
    records: list[Observation] = []
    for order in permutations(keys):
        pipeline = ObservationPipeline()
        calls: list[tuple[int, str, str]] = []
        for key in order:

            def contribute(
                c: PerceptionContext,
                k: tuple[int, str, str] = key,
                log: list[tuple[int, str, str]] = calls,
            ) -> JsonObject:
                log.append(k)
                return {"actor": c.actor_id}

            pipeline.register(
                priority=key[0], module_id=key[1], contributor_id=key[2], contributor=contribute
            )
        records.append(ObservationHistory("run").generate(context(), pipeline))
        assert calls == sorted(keys)
    assert all(record == records[0] for record in records)


def test_context_and_output_aliases_cannot_contaminate_other_contributors_or_records() -> None:
    original = context()
    retained_inputs: list[PerceptionContext] = []
    retained_output: JsonObject = {"nested": {"value": 1}}
    pipeline = ObservationPipeline()

    def mutator(c: PerceptionContext) -> JsonObject:
        assert [field.name for field in fields(c)] == [
            "run_id",
            "actor_id",
            "simulation_time",
            "perceived",
            "known",
        ]
        retained_inputs.append(c)
        own = c.perceived["self"]
        assert isinstance(own, dict)
        own["entity_id"] = "injected"
        records = c.known["records"]
        assert isinstance(records, list)
        records.append("injected")
        return retained_output

    def observer(c: PerceptionContext) -> JsonObject:
        assert c.perceived == original.perceived
        assert c.known == {"records": []}
        return c.perceived

    for priority, contributor in enumerate((mutator, observer)):
        pipeline.register(
            priority=priority,
            module_id="test",
            contributor_id=str(priority),
            contributor=contributor,
        )
    history = ObservationHistory("run")
    first = history.generate(original, pipeline)
    first_content = first.content
    nested = retained_output["nested"]
    assert isinstance(nested, dict)
    nested["value"] = "changed after delivery"
    retained_inputs[0].perceived["bad"] = True
    second = history.generate(original, pipeline)
    assert first.content == first_content
    assert second.content != first_content
    assert original == context()
    assert history.records[0].content == first_content


@pytest.mark.parametrize("bad", [float("nan"), {1: "value"}, {"set"}, (1,), object()])
def test_invalid_output_never_appends_a_partial_record_or_consumes_sequence(bad: object) -> None:
    pipeline = ObservationPipeline()
    output: JsonObject = {"bad": cast(JsonValue, bad)}
    pipeline.register(
        priority=1, module_id="test", contributor_id="bad", contributor=lambda c: output
    )
    history = ObservationHistory("run")
    with pytest.raises(CanonicalValueError):
        history.generate(context(), pipeline)
    assert history.records == ()
    output.clear()
    assert history.generate(context(), pipeline).observation_sequence == 1


def test_non_object_output_exception_and_reentrancy_are_atomic() -> None:
    history = ObservationHistory("run")
    pipeline = ObservationPipeline()
    pipeline.register(
        priority=0,
        module_id="test",
        contributor_id="bad",
        contributor=lambda c: cast(JsonObject, []),
    )
    with pytest.raises(ValueError, match="JSON object"):
        history.generate(context(), pipeline)
    assert history.records == ()
    recursive = ObservationPipeline()

    def reenter(c: PerceptionContext) -> JsonObject:
        history.generate(c, recursive)
        return {}

    recursive.register(priority=0, module_id="test", contributor_id="r", contributor=reenter)
    with pytest.raises(RuntimeError, match="reentered"):
        history.generate(context(), recursive)
    assert history.records == ()
    assert history.generate(context(), ObservationPipeline()).observation_sequence == 1


def test_registration_and_run_scope_validation() -> None:
    pipeline = ObservationPipeline()
    pipeline.register(priority=0, module_id="test", contributor_id="a", contributor=lambda c: {})
    with pytest.raises(ValueError, match="duplicate"):
        pipeline.register(
            priority=0, module_id="test", contributor_id="a", contributor=lambda c: {}
        )
    with pytest.raises(ValueError, match="integer"):
        pipeline.register(
            priority=True, module_id="test", contributor_id="b", contributor=lambda c: {}
        )
    with pytest.raises(ValueError, match="non-empty"):
        pipeline.register(priority=1, module_id="", contributor_id="b", contributor=lambda c: {})
    with pytest.raises(ValueError, match="another run"):
        ObservationHistory("other").generate(context(), pipeline)


@pytest.mark.parametrize(
    ("sequence", "tick", "version"), [(0, 0, 1), (True, 0, 1), (1, -1, 1), (1, 0, False)]
)
def test_invalid_envelope_ordering_values(sequence: int, tick: int, version: int) -> None:
    with pytest.raises(ValueError):
        Observation("run", "a", sequence, tick, {}, version)
