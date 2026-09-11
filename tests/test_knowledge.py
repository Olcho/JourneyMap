"""M3 epistemic ownership, sources, unknowns and correction lineage."""

from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest

from journeymap.core.canonical import CanonicalValueError, JsonValue
from journeymap.modules.knowledge import ActorKnowledgeView, KnowledgeLedger, KnowledgeRecord


def record() -> KnowledgeRecord:
    return KnowledgeRecord(
        "k1", "run", "a", "unverified-place", "open", {"claim": True}, "INITIAL", "scenario:1", 0
    )


def test_explicit_sources_contradictions_unknowns_and_stable_history() -> None:
    first = record()
    second = replace(first, knowledge_record_id="k2", value=False, source_ref="report:other")
    correction = replace(
        second, knowledge_record_id="k3", learned_at=1, supersedes_id="k2", value=True
    )
    other = replace(first, knowledge_record_id="kb", actor_id="b")
    records = (correction, other, second, first)
    ledger = KnowledgeLedger("run", 1, records)
    assert ledger.history() == KnowledgeLedger("run", 1, reversed(records)).history()
    own = ledger.for_actor("a")
    assert own.query("unverified-place", "open") == (first, second, correction)
    assert own.query("world-fact-without-record", "open") == ()
    assert ledger.for_actor("unknown-actor").history() == ()
    assert ledger.for_actor("b").history() == (other,)
    assert first.source_kind == "INITIAL"
    assert second.source_ref == "report:other"
    assert correction.supersedes_id == "k2"
    # There is no truth lookup or automatic resolution to overwrite either claim.
    assert own.history()[0].value == {"claim": True}
    assert own.history()[1].value is False
    assert KnowledgeLedger("run", 0).history() == ()
    assert {name for name in dir(own) if not name.startswith("_")} == {"query", "history"}
    with pytest.raises(TypeError):
        cast(Callable[..., object], own.query)("x", "y", actor_id="b")


def test_record_input_queries_and_serialization_are_detached() -> None:
    value: JsonValue = {"nested": [1]}
    source = replace(record(), value=value)
    ledger = KnowledgeLedger("run", 0, (source,))
    assert isinstance(value, dict)
    value.clear()
    assert isinstance(source.value, dict)
    source.value.clear()
    own = ledger.for_actor("a")
    delivered = own.history()[0]
    assert isinstance(delivered.value, dict)
    delivered.value.clear()
    serialized = own.history()[0].to_json()
    serialized["value"] = None
    assert ledger.history()[0].value == {"nested": [1]}
    assert own.history()[0].value == {"nested": [1]}
    with pytest.raises(ValueError, match="single actor"):
        ActorKnowledgeView((record(), replace(record(), actor_id="b")))


@pytest.mark.parametrize(
    "records",
    [
        (record(), record()),
        (replace(record(), run_id="other"),),
        (replace(record(), learned_at=2),),
        (replace(record(), supersedes_id="missing"),),
        (replace(record(), supersedes_id="k1"),),
        (record(), replace(record(), knowledge_record_id="k2", actor_id="b", supersedes_id="k1")),
        (
            record(),
            replace(record(), knowledge_record_id="k2", predicate="different", supersedes_id="k1"),
        ),
        (
            replace(record(), learned_at=1),
            replace(record(), knowledge_record_id="k2", supersedes_id="k1"),
        ),
        (
            replace(record(), supersedes_id="k2"),
            replace(record(), knowledge_record_id="k2", supersedes_id="k1"),
        ),
    ],
)
def test_invalid_initial_ledger_provenance_is_rejected(
    records: tuple[KnowledgeRecord, ...],
) -> None:
    with pytest.raises(ValueError):
        KnowledgeLedger("run", 1, records)


@pytest.mark.parametrize("bad", [float("nan"), {"set"}, {1: 2}, object()])
def test_noncanonical_claim_values_are_rejected(bad: object) -> None:
    with pytest.raises(CanonicalValueError):
        replace(record(), value=cast(JsonValue, bad))


def test_record_identity_source_and_time_validation() -> None:
    for field in (
        "knowledge_record_id",
        "actor_id",
        "subject_ref",
        "predicate",
        "source_kind",
        "source_ref",
    ):
        with pytest.raises(ValueError):
            cast(Callable[..., KnowledgeRecord], replace)(record(), **{field: ""})
    with pytest.raises(ValueError):
        replace(record(), learned_at=True)
    with pytest.raises(ValueError):
        replace(record(), schema_version=0)
