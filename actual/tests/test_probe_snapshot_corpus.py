"""CP-R1: current-source synthetic prefixes, with all external access blocked."""

import http.client
import socket
from pathlib import Path
from typing import Any, cast

import pytest
from probe_policy_fixtures import fixture_case, history_fixture
from test_probe_continuation import packets

from journeymap.adapters import life_http
from journeymap.adapters.probe_execution import object_, rows
from journeymap.adapters.probe_lineage import check_snapshot, reduce_candidate_trace
from journeymap.core.canonical import JsonObject, JsonValue, clone_json_object, state_digest
from journeymap.experiments import continued_probe
from journeymap.experiments import life_reasoning_probe as probe
from journeymap.experiments import paid_probe as paid


@pytest.fixture(autouse=True)
def no_external_access(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError("CP-R1: real network/credential access forbidden")

    for name in ("socket", "getaddrinfo", "create_connection"):
        monkeypatch.setattr(socket, name, forbidden)
    monkeypatch.setattr(http.client, "HTTPSConnection", forbidden)
    for module in (life_http, paid):
        monkeypatch.setattr(module, "real_credential", forbidden)
        monkeypatch.setattr(module, "real_connection", forbidden)


@pytest.fixture(scope="module")
def current_report(tmp_path_factory: pytest.TempPathFactory) -> JsonObject:
    folder = tmp_path_factory.mktemp("cp-r1-current-source-mocks")
    corpus = probe.prepare()
    spec = history_fixture(corpus, folder)
    path = folder / "probe-runs.sqlite3"
    anchor, approval, _ = fixture_case(path, corpus, synthetic=True, history=spec)
    return continued_probe.execute(path, anchor, approval, transport=packets(fail_at=0))


def snapshot_prefix(report: JsonObject, count: int) -> JsonObject:
    snapshot = clone_json_object(object_(report["authority"]))
    if count == 0:
        snapshot["events"] = cast(list[JsonValue], rows(snapshot["events"])[:1])
        snapshot["runs"] = []
        snapshot["consumed"] = cast(
            list[JsonValue], [r for r in rows(snapshot["consumed"]) if r["event_seq"] == 1]
        )
    else:
        run = rows(snapshot["runs"])[0]
        entries = rows(run["entries"])[:count]
        reduced = reduce_candidate_trace(object_(run["binding"]), object_(run["corpus"]), entries)
        run.update(
            entries=cast(list[JsonValue], entries),
            n=count,
            tail=state_digest(entries[-1]),
            status=reduced["status"],
        )
        snapshot["events"] = cast(list[JsonValue], rows(snapshot["events"])[: 2 + count])
    return snapshot


def mutate_corpus(snapshot: JsonObject, mutation: str, *, rehash: bool) -> None:
    corpus = object_(rows(snapshot["runs"])[0]["corpus"])
    if mutation == "missing-fixtures":
        del corpus["fixtures"]
    elif mutation == "repetitions":
        corpus["repetitions"] = 999
    elif mutation == "zero-price":
        corpus["price_nano_usd_per_token"] = {"input": 0, "output": 0, "cached": 0}
    else:
        raise AssertionError("unknown CP-R1 fixture mutation")
    if rehash:
        corpus["corpus_digest"] = state_digest(
            {k: v for k, v in corpus.items() if k != "corpus_digest"}
        )


@pytest.mark.parametrize(
    "count",
    range(7),
    ids=["import-only", "claimed", "reserved", "attempted", "response", "settled", "stopped"],
)
def test_canonical_current_source_prefix_is_accepted(
    current_report: JsonObject, count: int
) -> None:
    snapshot = snapshot_prefix(current_report, count)
    original = clone_json_object(snapshot)
    anchor, _, reduced = check_snapshot(snapshot, Path(probe.__file__).resolve().parents[2])
    assert snapshot == original
    if count == 0:
        assert anchor.sequence == 1 and reduced is None
    else:
        assert anchor.sequence == count + 2 and reduced is not None
        assert reduced["status"] == ("UNCERTAIN" if count == 6 else "OPEN")
        assert object_(rows(snapshot["runs"])[0]["corpus"]) == probe.prepare()


@pytest.mark.parametrize("mutation", ["missing-fixtures", "repetitions", "zero-price"])
@pytest.mark.parametrize(
    "rehash", [False, True], ids=["stored-digest", "content-digest-recomputed"]
)
def test_malformed_current_source_corpus_is_rejected_before_claim_replay(
    current_report: JsonObject, mutation: str, rehash: bool
) -> None:
    snapshot = snapshot_prefix(current_report, 3)
    original = clone_json_object(snapshot)
    mutate_corpus(snapshot, mutation, rehash=rehash)
    assert snapshot["events"] == original["events"]
    run, old = rows(snapshot["runs"])[0], rows(original["runs"])[0]
    assert run["binding"] == old["binding"] and run["entries"] == old["entries"]
    corpus = object_(run["corpus"])
    assert corpus["source_identity"] == probe.prepare()["source_identity"]
    digest_matches = corpus["corpus_digest"] == state_digest(
        {k: v for k, v in corpus.items() if k != "corpus_digest"}
    )
    assert digest_matches == rehash
    with pytest.raises(ValueError, match=r"^continuation source/corpus mismatch$"):
        check_snapshot(snapshot, Path(probe.__file__).resolve().parents[2])


def test_complete_report_corpus_validation_remains_strict(current_report: JsonObject) -> None:
    report = clone_json_object(current_report)
    mutate_corpus(object_(report["authority"]), "missing-fixtures", rehash=False)
    with pytest.raises(ValueError, match=r"^continuation source/corpus mismatch$"):
        continued_probe.audit_report(report, Path(probe.__file__).resolve().parents[2])
