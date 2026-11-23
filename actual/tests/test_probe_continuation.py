"""Policy fixtures only: no live key, network, operator bootstrap, or real approval files."""

import http.client
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from probe_policy_fixtures import fixture_case, history_fixture

from journeymap.adapters import life_http
from journeymap.adapters.life_responses import synthetic_response
from journeymap.adapters.llm import strict_json
from journeymap.adapters.probe_continuation import (
    PRIOR_KNOWN,
    PRIOR_UNRESOLVED,
    Anchor,
    ContinuationApproval,
    FollowupLedger,
    FollowupPlan,
    candidate_binding,
    exposure,
    inactive_proposal,
)
from journeymap.adapters.probe_execution import ProbeApproval, ProbePlan, binding, object_, rows
from journeymap.adapters.probe_history import (
    HistorySpec,
    file_digest,
    verify_bundle,
    verify_history,
)
from journeymap.adapters.probe_lineage import (
    ContinuationJournal,
    check_snapshot,
    read_snapshot,
)
from journeymap.core.canonical import (
    JsonObject,
    JsonValue,
    canonical_json,
    clone_json_object,
    state_digest,
)
from journeymap.experiments import continued_probe
from journeymap.experiments import life_reasoning_probe as probe
from journeymap.experiments import paid_probe as paid


@pytest.fixture(autouse=True)
def no_external_access(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError("policy fixture: real external access forbidden")

    for name in ("socket", "getaddrinfo", "create_connection"):
        monkeypatch.setattr(socket, name, forbidden)
    monkeypatch.setattr(http.client, "HTTPSConnection", forbidden)
    monkeypatch.setattr(life_http, "real_credential", forbidden)
    monkeypatch.setattr(life_http, "real_connection", forbidden)
    monkeypatch.setattr(paid, "real_credential", forbidden)
    monkeypatch.setattr(paid, "real_connection", forbidden)


@pytest.fixture(scope="module")
def corpus() -> JsonObject:
    return probe.prepare()


@pytest.fixture(scope="module")
def history(tmp_path_factory: pytest.TempPathFactory, corpus: JsonObject) -> HistorySpec:
    return history_fixture(corpus, tmp_path_factory.mktemp("policy-base-history"))


def packets(
    inputs: int = 1000, outputs: int = 100, *, fail_at: int | None = None, cached: int = 0
) -> paid.RecordingProbeTransport:
    replies = []
    for index in range(72):
        if index == fail_at:
            replies.append(paid.WireReply(None, None, 1, "TRANSPORT_UNCERTAIN"))
        else:
            doc = object_(strict_json(synthetic_response(probe.WAIT)))
            doc["id"] = f"policy-only-fixture-{index}"
            doc["usage"] = {
                "input_tokens": inputs,
                "output_tokens": outputs,
                "total_tokens": inputs + outputs,
                "input_tokens_details": {"cached_tokens": cached},
                "output_tokens_details": {"reasoning_tokens": outputs},
            }
            replies.append(paid.WireReply(200, canonical_json(doc), 1))
    return paid.RecordingProbeTransport(tuple(replies))


def key_spy(calls: list[str]) -> paid.HTTPProbeTransport:
    def key() -> str:
        calls.append("key")
        raise AssertionError("must be denied before credential")

    return paid.HTTPProbeTransport(credential=key)


def manifest(path: Path) -> JsonObject:
    snapshot = read_snapshot(path)
    return object_(
        object_(object_(rows(snapshot["events"])[0]["event"])["data"])["history_manifest"]
    )


@pytest.mark.parametrize("missing", ["approval", "checkpoint"])
def test_policy_and_current_approval_required_before_credentials(
    tmp_path: Path, corpus: JsonObject, history: HistorySpec, missing: str
) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    anchor, a, _ = fixture_case(path, corpus, synthetic=False, history=history)
    before = file_digest(path)
    calls: list[str] = []
    with pytest.raises(PermissionError):
        continued_probe.execute(
            path,
            None if missing == "checkpoint" else anchor,
            None if missing == "approval" else a,
            transport=key_spy(calls),
        )
    assert calls == [] and file_digest(path) == before


@pytest.mark.parametrize("injected", [False, True])
def test_legacy_v2_cannot_start_http_in_an_empty_registry(
    tmp_path: Path, corpus: JsonObject, injected: bool
) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    rid = str(uuid4())
    a = ProbeApproval(
        str(uuid4()),
        "human-user",
        "approve-one-paid-probe",
        ("read-openai-api-key", "paid-responses"),
        binding(rid, path, corpus),
    )
    calls: list[str] = []
    with pytest.raises(PermissionError, match="continuation authority"):
        paid.execute(rid, path, a, transport=key_spy(calls) if injected else None)
    assert not path.exists() and calls == []


def test_synthetic_approval_is_not_an_http_approval(
    tmp_path: Path, corpus: JsonObject, history: HistorySpec
) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    anchor, a, _ = fixture_case(path, corpus, synthetic=True, history=history)
    calls: list[str] = []
    with pytest.raises(PermissionError):
        continued_probe.execute(path, anchor, a, transport=key_spy(calls))
    assert calls == [] and read_snapshot(path)["runs"] == []
    assert not list(tmp_path.glob("*.attempt-*"))


@pytest.mark.parametrize(
    "wrong",
    [
        "missing",
        "empty",
        "path",
        "device",
        "identity",
        "checkpoint",
        "history",
        "wal",
        "shm",
        "journal",
    ],
)
def test_authority_missing_stale_or_wrong_never_falls_back(
    tmp_path: Path, corpus: JsonObject, history: HistorySpec, wrong: str
) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    anchor, a, _ = fixture_case(path, corpus, synthetic=False, history=history)
    if wrong == "missing":
        path.unlink()
    elif wrong == "empty":
        path.write_bytes(b"")
    elif wrong == "path":
        path = tmp_path / "different.sqlite3"
    elif wrong == "device":
        anchor = replace(anchor, device="unapproved-device")
    elif wrong == "identity":
        anchor = replace(anchor, authority_id=str(uuid4()))
    elif wrong == "checkpoint":
        anchor = replace(anchor, sequence=anchor.sequence + 1)
    elif wrong == "history":
        anchor = replace(anchor, history_digest="0" * 64)
    else:
        Path(str(path) + "-" + wrong).write_bytes(b"uninspected-sidecar")
    calls: list[str] = []
    with pytest.raises((PermissionError, ValueError, sqlite3.Error)):
        continued_probe.execute(path, anchor, a, transport=key_spy(calls))
    assert calls == []
    if wrong in {"missing", "path"}:
        assert not path.exists()


@pytest.mark.parametrize(
    "wrong",
    ["expired", "future", "scope", "source", "plan", "budget", "target", "paid-id", "risk-id"],
)
def test_exact_combined_approval_is_required(
    tmp_path: Path, corpus: JsonObject, history: HistorySpec, wrong: str
) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    anchor, a, _ = fixture_case(path, corpus, synthetic=False, history=history)
    bound = clone_json_object(a.bound)
    if wrong == "expired":
        a = replace(
            a, valid_from="2000-01-01T00:00:00+00:00", expires_at="2000-01-02T00:00:00+00:00"
        )
    elif wrong == "future":
        a = replace(
            a, valid_from="2999-01-01T00:00:00+00:00", expires_at="2999-01-02T00:00:00+00:00"
        )
    elif wrong == "scope":
        a = replace(a, scope=("paid-responses",))
    elif wrong == "source":
        bound["source_digest"] = "wrong"
    elif wrong == "plan":
        bound["plan"] = ProbePlan().data
    elif wrong == "budget":
        object_(bound["plan"])["cost_limit_nano_usd"] = 24_260_000_001
    elif wrong == "target":
        object_(bound["prior_uncertain_target"])["request_id"] = "different"
    elif wrong == "paid-id":
        a = replace(a, approval_id=str(uuid4()))
    else:
        a = replace(a, risk_acceptance_id=str(uuid4()))
    a = replace(a, bound=bound)
    calls: list[str] = []
    with pytest.raises((PermissionError, ValueError)):
        continued_probe.execute(path, anchor, a, transport=key_spy(calls))
    assert calls == [] and read_snapshot(path)["runs"] == []


@pytest.mark.parametrize("role", ["run", "paid", "risk"])
def test_imported_consumed_ids_cannot_be_reused_in_any_role(
    tmp_path: Path, corpus: JsonObject, history: HistorySpec, role: str
) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    anchor, a, _ = fixture_case(path, corpus, synthetic=True, history=history)
    old = rows(manifest(path)["consumed_ids"])[0]["id"]
    rid, aid, risk = str(a.bound["run_id"]), a.approval_id, a.risk_acceptance_id
    if role == "run":
        rid = str(old)
    elif role == "paid":
        aid = str(old)
    else:
        risk = str(old)
    b = candidate_binding(rid, aid, risk, corpus, anchor, manifest(path))
    a = replace(a, approval_id=aid, risk_acceptance_id=risk, bound=b)
    with pytest.raises(PermissionError, match="already consumed"):
        continued_probe.execute(path, anchor, a, transport=packets(fail_at=0))
    assert read_snapshot(path)["runs"] == []


@pytest.mark.parametrize(
    "wrong",
    [
        "extra-run",
        "orphan-entry",
        "state",
        "approval",
        "ordinal",
        "report",
        "audit-string",
        "runtime",
        "sidecar",
    ],
)
def test_complete_original_history_is_verified_not_a_selected_summary(
    tmp_path: Path, corpus: JsonObject, wrong: str
) -> None:
    spec = history_fixture(corpus, tmp_path, cached=False)
    if wrong == "runtime":
        spec = replace(spec, expected_runtime_identity="wrong")
    elif wrong == "sidecar":
        Path(str(spec.registry) + "-wal").write_bytes(b"pending")
    elif wrong == "report":
        report = json.loads(spec.reports[0].read_text(encoding="utf-8"))
        report["accounting"]["ledger"]["known_cost_nano_usd"] = 0
        spec.reports[0].write_text(canonical_json(report), encoding="utf-8")
    elif wrong == "audit-string":
        bundle, _ = verify_history(spec)
        bundle["audit"] = True
        with pytest.raises(ValueError):
            verify_bundle(bundle, spec.frozen_source)
        return
    else:
        with sqlite3.connect(spec.registry) as db:
            if wrong == "extra-run":
                db.execute(
                    "INSERT INTO probe_runs SELECT ?,?,binding,'FINISHED',n,tail "
                    "FROM probe_runs LIMIT 1",
                    (str(uuid4()), str(uuid4())),
                )
            elif wrong == "orphan-entry":
                db.execute("INSERT INTO probe_entries VALUES (?,1,'{}','fake')", (str(uuid4()),))
            elif wrong == "state":
                db.execute("UPDATE probe_runs SET status='FINISHED'")
            elif wrong == "approval":
                db.execute("UPDATE probe_runs SET approval_id=?", (str(uuid4()),))
            else:
                db.execute("UPDATE probe_entries SET ordinal=100 WHERE ordinal=1")
        spec = replace(spec, expected_registry_sha256=file_digest(spec.registry))
    with pytest.raises((PermissionError, ValueError, sqlite3.Error)):
        verify_history(spec)


def test_original_history_changed_after_approval_is_denied(
    tmp_path: Path, corpus: JsonObject
) -> None:
    history_root = tmp_path / "history"
    history_root.mkdir()
    spec = history_fixture(corpus, history_root, cached=False)
    path = tmp_path / "probe-runs.sqlite3"
    anchor, a, _ = fixture_case(path, corpus, synthetic=False, history=spec)
    with sqlite3.connect(spec.registry) as db:
        db.execute("UPDATE probe_runs SET n=n+1")
    calls: list[str] = []
    with pytest.raises(PermissionError, match="original registry"):
        continued_probe.execute(path, anchor, a, transport=key_spy(calls))
    assert calls == [] and read_snapshot(path)["runs"] == []


@pytest.fixture(scope="module")
def full(
    tmp_path_factory: pytest.TempPathFactory, corpus: JsonObject, history: HistorySpec
) -> JsonObject:
    path = tmp_path_factory.mktemp("policy-max72") / "probe-runs.sqlite3"
    anchor, a, _ = fixture_case(path, corpus, synthetic=True, history=history)
    wire = packets(400000, 8192)
    report = continued_probe.execute(path, anchor, a, transport=wire)
    assert len(wire.bodies) == 72
    return report


def test_actual_candidate_runner_accepts_maximum_72_without_releasing_prior_exposure(
    full: JsonObject, history: HistorySpec
) -> None:
    account, lineage = object_(full["accounting"]), object_(full["lineage_accounting"])
    assert account["status"] == "FINISHED" and account["attempted_sends"] == 72
    assert account["total_cost_nano_usd"] == 24_254_208_000
    ledger = object_(account["ledger"])
    assert ledger["input_tokens"] == 28_800_000 and ledger["output_tokens"] == 589_824
    assert lineage["prior_known_cost_nano_usd"] == PRIOR_KNOWN
    assert lineage["prior_unresolved_nano_usd"] == PRIOR_UNRESOLVED
    assert lineage["conditional_exposure_nano_usd"] == 24_711_527_850
    assert lineage["lineage_total_cost_nano_usd"] is None
    assert FollowupLedger(attempts=72).blocked() == "REQUEST_LIMIT"
    assert continued_probe.audit_report(full, history.frozen_source)["data_only_equal"] is True


@pytest.mark.parametrize(
    "field,threshold,reason",
    [
        ("input_tokens", 28_400_000, "INPUT_TOKEN_LIMIT"),
        ("output_tokens", 581_632, "OUTPUT_TOKEN_LIMIT"),
        ("known_cost_nano_usd", 23_923_136_000, "COST_LIMIT"),
    ],
)
def test_candidate_exact_admission_boundaries(field: str, threshold: int, reason: str) -> None:
    ledger = FollowupLedger()
    setattr(ledger, field, threshold)
    assert ledger.blocked() is None
    setattr(ledger, field, threshold + 1)
    assert ledger.blocked() == reason


def test_lineage_limit_includes_prior_cost_and_unresolved_exposure() -> None:
    ledger = FollowupLedger(known_cost_nano_usd=23_923_136_000)
    assert ledger.blocked() is None
    ledger.prior_known_cost_nano_usd += 1
    assert ledger.blocked() == "LINEAGE_COST_LIMIT"
    ledger.prior_known_cost_nano_usd -= 1
    ledger.prior_unresolved_nano_usd += 1
    assert ledger.blocked() == "LINEAGE_COST_LIMIT"
    data = FollowupLedger(known_cost_nano_usd=24_260_000_000).data
    assert (
        exposure(data)["conditional_exposure_nano_usd"]
        == FollowupPlan().lineage_cost_limit_nano_usd
    )
    data["known_cost_nano_usd"] = 24_260_000_001
    with pytest.raises(ValueError):
        exposure(data)
    assert ProbePlan().cost_limit_nano_usd == 1_000_000_000
    assert ProbePlan().total_input_tokens == 500_000


@pytest.mark.parametrize("failed_at", [0, 16, 71])
def test_new_uncertainty_retains_both_reservations_and_no_next_batch(
    tmp_path: Path, corpus: JsonObject, history: HistorySpec, failed_at: int
) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    anchor, a, _ = fixture_case(path, corpus, synthetic=True, history=history)
    wire = packets(fail_at=failed_at)
    report = continued_probe.execute(path, anchor, a, transport=wire)
    assert len(wire.bodies) == failed_at + 1
    assert object_(report["accounting"])["status"] == "UNCERTAIN"
    assert object_(report["accounting"])["total_cost_nano_usd"] is None
    lineage = object_(report["lineage_accounting"])
    assert lineage["unresolved_exposure_sum_nano_usd"] == 2 * PRIOR_UNRESOLVED
    assert lineage["lineage_total_cost_nano_usd"] is None
    assert rows(object_(report["evaluation"])["rows"])[failed_at]["engine"] is None
    with pytest.raises(PermissionError):
        continued_probe.execute(path, anchor, a, transport=packets())
    assert continued_probe.audit_report(report, history.frozen_source)["data_only_equal"] is True


@pytest.mark.parametrize(
    "stage", ["RUN_CLAIMED", "RESERVED", "ATTEMPTED", "RESPONSE", "SETTLED", "RUN_STOPPED"]
)
@pytest.mark.parametrize("after", [False, True])
def test_storage_prefix_never_reuses_ids_or_compensates_claim(
    tmp_path: Path,
    corpus: JsonObject,
    history: HistorySpec,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    after: bool,
) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    anchor, a, _ = fixture_case(path, corpus, synthetic=True, history=history)
    original = ContinuationJournal._append

    def fault(self: ContinuationJournal, kind: str, data: JsonObject) -> None:
        if kind == stage:
            if after:
                original(self, kind, data)
            raise OSError("synthetic-storage-fault")
        original(self, kind, data)

    monkeypatch.setattr(ContinuationJournal, "_append", fault)
    wire = packets(fail_at=0)
    with pytest.raises((RuntimeError, OSError)):
        continued_probe.execute(path, anchor, a, transport=wire)
    assert len(wire.bodies) <= 1
    monkeypatch.setattr(ContinuationJournal, "_append", original)
    fresh = packets()
    with pytest.raises((PermissionError, FileExistsError)):
        continued_probe.execute(path, anchor, a, transport=fresh)
    assert fresh.bodies == []
    assert len(list(tmp_path.glob("*.attempt-*"))) == 1


def test_fence_flush_failure_is_fail_closed(
    tmp_path: Path, corpus: JsonObject, history: HistorySpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    anchor, a, _ = fixture_case(path, corpus, synthetic=True, history=history)

    def fail(fd: int) -> None:
        raise OSError("synthetic-fsync-fault")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fail)
        with pytest.raises(OSError):
            continued_probe.execute(path, anchor, a, transport=packets())
    assert read_snapshot(path)["runs"] == []
    with pytest.raises(FileExistsError):
        continued_probe.execute(path, anchor, a, transport=packets())
    rid, aid, risk = str(uuid4()), str(uuid4()), str(uuid4())
    bound = candidate_binding(rid, aid, risk, corpus, anchor, manifest(path))
    fresh = replace(a, approval_id=aid, risk_acceptance_id=risk, bound=bound)
    wire = packets()
    with pytest.raises(FileExistsError):
        continued_probe.execute(path, anchor, fresh, transport=wire)
    assert wire.bodies == []


def reseal_authority(snapshot: JsonObject) -> None:
    previous = "0" * 64
    for i, wrapper in enumerate(rows(snapshot["events"]), 1):
        event = object_(wrapper["event"])
        event["sequence"], event["previous"] = i, previous
        previous = state_digest(event)
        wrapper["digest"] = previous


@pytest.mark.parametrize(
    "change",
    [
        "prior-zero",
        "reservation-missing",
        "double-count",
        "capacity-as-cost",
        "consumed-id",
        "entry-link",
        "state",
        "extra-claim",
        "bool-sequence",
    ],
)
def test_resealed_authority_or_accounting_forgery_is_rejected(
    full: JsonObject, history: HistorySpec, change: str
) -> None:
    snapshot = clone_json_object(object_(full["authority"]))
    events = rows(snapshot["events"])
    first_commit = object_(object_(events[2]["event"])["data"])
    if change == "prior-zero":
        object_(first_commit["exposure"])["prior_known_cost_nano_usd"] = 0
    elif change == "reservation-missing":
        object_(first_commit["exposure"])["prior_unresolved_nano_usd"] = 0
    elif change == "double-count":
        object_(first_commit["exposure"])["conditional_exposure_nano_usd"] = 794_183_850
    elif change == "capacity-as-cost":
        object_(first_commit["exposure"])["conditional_exposure_nano_usd"] = 24_717_319_850
    elif change == "consumed-id":
        pass
    elif change == "entry-link":
        first_commit["entry_digest"] = "0" * 64
    elif change == "state":
        rows(snapshot["runs"])[0]["status"] = "OPEN"
    elif change == "extra-claim":
        cast(list[JsonValue], snapshot["events"]).append(clone_json_object(events[1]))
    else:
        object_(events[0]["event"])["sequence"] = True
    if change == "consumed-id":
        cast(list[JsonValue], snapshot["consumed"]).pop()
    reseal_authority(snapshot)
    if change == "bool-sequence":
        object_(events[0]["event"])["sequence"] = True
        events[0]["digest"] = state_digest(object_(events[0]["event"]))
    with pytest.raises((ValueError, PermissionError)):
        check_snapshot(snapshot, history.frozen_source)


def test_old_snapshot_cannot_satisfy_a_trusted_newer_checkpoint(
    tmp_path: Path, corpus: JsonObject, history: HistorySpec
) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    anchor, a, _ = fixture_case(path, corpus, synthetic=True, history=history)
    before = path.read_bytes()
    result = continued_probe.execute(path, anchor, a, transport=packets(fail_at=0))
    latest = Anchor.parse(result["latest_recorded_checkpoint"])
    path.write_bytes(
        before
    )  # A mock stale copy; trusted latest anchor is deliberately NOT rolled back.
    wire = packets()
    with pytest.raises(PermissionError, match="stale checkpoint"):
        continued_probe.execute(path, latest, a, transport=wire)
    assert wire.bodies == []


def test_proposal_has_no_human_approval_schema_or_issued_ids() -> None:
    p = inactive_proposal()
    assert p["authorized"] is False and p["operational_history_imported"] is False
    assert "approval_id" not in p and "run_id" not in p
    with pytest.raises((PermissionError, ValueError)):
        ContinuationApproval.parse(p)


@pytest.mark.parametrize("change", ["body", "order"])
def test_changed_wire_schedule_is_rejected_before_key_lookup(
    tmp_path: Path,
    corpus: JsonObject,
    history: HistorySpec,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    anchor, a, _ = fixture_case(path, corpus, synthetic=False, history=history)
    changed = clone_json_object(corpus)
    requests = rows(changed["requests"])
    if change == "body":
        object_(requests[0]["body"])["reasoning"] = {"effort": "low"}
        requests[0]["body_digest"] = state_digest(object_(requests[0]["body"]))
    else:
        requests[0], requests[1] = requests[1], requests[0]
        changed["requests"] = cast(list[JsonValue], requests)
    changed["corpus_digest"] = state_digest(
        {k: v for k, v in changed.items() if k != "corpus_digest"}
    )
    monkeypatch.setattr(probe, "prepare", lambda: changed)
    calls: list[str] = []
    with pytest.raises((PermissionError, ValueError)):
        continued_probe.execute(path, anchor, a, transport=key_spy(calls))
    assert calls == []


def test_cached_usage_and_late_complete_do_not_confirm_prior_cost(
    tmp_path: Path, corpus: JsonObject, history: HistorySpec
) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    anchor, a, _ = fixture_case(path, corpus, synthetic=True, history=history)
    first = packets(cached=800).replies[0]
    assert isinstance(first, paid.WireReply)
    wire = paid.RecordingProbeTransport((replace(first, elapsed_ms=120001),))
    report = continued_probe.execute(path, anchor, a, transport=wire)
    account = object_(report["accounting"])
    assert account["stop_reason"] == "TIMEOUT" and account["status"] == "FINISHED"
    assert account["total_cost_nano_usd"] == 660_000
    assert object_(report["lineage_accounting"])["lineage_total_cost_nano_usd"] is None
    assert rows(object_(report["evaluation"])["rows"])[0]["engine"] is None
    assert len(wire.bodies) == 1


def test_even_success_does_not_allow_a_second_batch_with_new_ids(full: JsonObject) -> None:
    snapshot = object_(full["authority"])
    path = Path(str(object_(snapshot["meta"])["path"]))
    latest = Anchor.parse(full["latest_recorded_checkpoint"])
    claim = object_(object_(rows(snapshot["events"])[1]["event"])["data"])
    a = ContinuationApproval.parse(claim["approval"])
    rid, aid, risk = str(uuid4()), str(uuid4()), str(uuid4())
    bound = candidate_binding(rid, aid, risk, object_(full["corpus"]), latest, manifest(path))
    a = replace(a, approval_id=aid, risk_acceptance_id=risk, bound=bound)
    wire = packets()
    with pytest.raises(PermissionError, match="one successor"):
        continued_probe.execute(path, latest, a, transport=wire)
    assert wire.bodies == []


_PROCESS = r"""
import http.client,json,pathlib,socket,sys,time
def forbidden(*a,**k):raise AssertionError('real external access forbidden')
socket.socket=socket.getaddrinfo=socket.create_connection=forbidden
http.client.HTTPSConnection=forbidden
p=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'));sys.path.insert(0,p['source'])
from journeymap.adapters import life_http
from journeymap.adapters.probe_continuation import Anchor,ContinuationApproval
from journeymap.adapters.life_responses import synthetic_response
from journeymap.experiments import paid_probe,continued_probe,life_reasoning_probe
life_http.real_credential=life_http.real_connection=forbidden
paid_probe.real_credential=paid_probe.real_connection=forbidden
role=sys.argv[2];folder=pathlib.Path(p['folder'])
def key():
 (folder/(role+'-fake-key-count')).write_text('1')
 if role=='winner':
  (folder/'ready').write_text('ready');deadline=time.monotonic()+45
  while not (folder/'release').exists():
   if time.monotonic()>deadline:raise AssertionError('fixture coordinator timeout')
   time.sleep(.01)
 return 'OFFLINE-PROCESS-FIXTURE-KEY'
class Response:
 status=503
 def read1(self,n):return synthetic_response(life_reasoning_probe.WAIT).encode()
 def isclosed(self):return True
class Connection:
 def request(self,*a,**k):(folder/(role+'-fake-send-count')).write_text('1')
 def getresponse(self):return Response()
 def close(self):pass
try:
 result=continued_probe.execute(
  pathlib.Path(p['authority']),Anchor.parse(p['anchor']),
  ContinuationApproval.parse(p['approval']),
  transport=paid_probe.HTTPProbeTransport(
   credential=key,connection=lambda h,t:Connection(),clock=lambda:0))
 print(json.dumps({'accepted':True,'attempts':result['accounting']['attempted_sends']}))
except (PermissionError,FileExistsError):
 print(json.dumps({'accepted':False}))
"""


def test_two_processes_have_one_claim_and_loser_has_no_key_or_http(
    tmp_path: Path, corpus: JsonObject, history: HistorySpec
) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    anchor, a, _ = fixture_case(path, corpus, synthetic=False, history=history)
    fixture = tmp_path / "FIXTURE_INPUT_ONLY.json"
    fixture.write_text(
        canonical_json(
            {
                "not_a_real_approval": True,
                "authority": str(path),
                "source": str(Path(probe.__file__).resolve().parents[2]),
                "folder": str(tmp_path),
                "anchor": anchor.data,
                "approval": a.data,
            }
        ),
        encoding="utf-8",
    )
    winner = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", _PROCESS, str(fixture), "winner"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        deadline = time.monotonic() + 40
        while not (tmp_path / "ready").exists():
            assert winner.poll() is None, winner.communicate()
            assert time.monotonic() < deadline
            time.sleep(0.01)
        loser = subprocess.run(
            [sys.executable, "-I", "-B", "-c", _PROCESS, str(fixture), "loser"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=True,
        )
        assert json.loads(loser.stdout)["accepted"] is False
        assert not (tmp_path / "loser-fake-key-count").exists()
        assert not (tmp_path / "loser-fake-send-count").exists()
    finally:
        (tmp_path / "release").write_text("release")
        stdout, stderr = winner.communicate(timeout=45)
    assert winner.returncode == 0, stderr
    assert json.loads(stdout)["accepted"] is True
    snapshot = read_snapshot(path)
    assert len(rows(snapshot["runs"])) == 1 and len(rows(snapshot["consumed"])) == 5


def test_same_authority_copied_to_another_path_is_not_an_approved_authority(
    tmp_path: Path, corpus: JsonObject, history: HistorySpec
) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    anchor, a, _ = fixture_case(path, corpus, synthetic=False, history=history)
    copied = tmp_path / "copied.sqlite3"
    shutil.copy2(path, copied)  # Mock DB only; no operating history is copied.
    calls: list[str] = []
    with pytest.raises(PermissionError, match="path"):
        continued_probe.execute(copied, anchor, a, transport=key_spy(calls))
    assert calls == []
