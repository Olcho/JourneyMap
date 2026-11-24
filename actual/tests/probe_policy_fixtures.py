"""Synthetic authority/history only. Human-shaped objects here are NOT real approvals."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from journeymap.adapters.life_admission import source_identity
from journeymap.adapters.life_responses import synthetic_response
from journeymap.adapters.llm import strict_json
from journeymap.adapters.probe_continuation import Anchor, ContinuationApproval, candidate_binding
from journeymap.adapters.probe_execution import ProbeApproval, binding, object_, rows
from journeymap.adapters.probe_history import HistorySpec, file_digest
from journeymap.adapters.probe_lineage import (
    bootstrap_authority,
    bootstrap_binding,
    device_id,
    journal_from_run,
    read_snapshot,
    reduce_candidate_trace,
)
from journeymap.core.canonical import JsonObject, canonical_json
from journeymap.experiments import continued_probe
from journeymap.experiments import life_reasoning_probe as probe
from journeymap.experiments import paid_probe as paid

_HISTORIES: dict[str, HistorySpec] = {}


def history_fixture(corpus: JsonObject, parent: Path, *, cached: bool = True) -> HistorySpec:
    identity = source_identity()
    if cached and identity in _HISTORIES:
        return _HISTORIES[identity]
    folder = parent / "synthetic-prior-history"
    folder.mkdir(parents=True, exist_ok=False)
    path = folder / "probe-runs.sqlite3"
    run_id, approval_id = str(uuid4()), str(uuid4())
    a = ProbeApproval(
        approval_id,
        "offline-fixture",
        "synthetic-probe-only",
        ("recording-only",),
        binding(run_id, path, corpus),
    )
    replies = []
    for index in range(16):
        doc = object_(strict_json(synthetic_response(probe.WAIT)))
        output = 6000 if index < 4 else 403 if index == 4 else 0
        inputs, cached_tokens = (18913, 5248) if index == 0 else (0, 0)
        doc["id"] = f"synthetic-history-{run_id}-{index}"
        doc["usage"] = {
            "input_tokens": inputs,
            "output_tokens": output,
            "total_tokens": inputs + output,
            "input_tokens_details": {"cached_tokens": cached_tokens},
            "output_tokens_details": {"reasoning_tokens": output},
        }
        replies.append(paid.WireReply(200, canonical_json(doc), 1))
    replies.append(paid.WireReply(None, None, 8140, "TRANSPORT_UNCERTAIN"))
    report = paid.execute(run_id, path, a, transport=paid.RecordingProbeTransport(tuple(replies)))
    assert object_(object_(report["accounting"])["ledger"])["known_cost_nano_usd"] == 120_455_850
    report_path = folder / "report.json"
    report_path.write_text(canonical_json(report) + "\n", encoding="utf-8")
    spec = HistorySpec(
        path, (report_path,), Path(probe.__file__).resolve().parents[2], file_digest(path), identity
    )
    if cached:
        _HISTORIES[identity] = spec
    return spec


def fixture_case(
    path: Path,
    corpus: JsonObject,
    *,
    synthetic: bool,
    run_id: str | None = None,
    approval_id: str | None = None,
    risk_id: str | None = None,
    history: HistorySpec | None = None,
) -> tuple[Anchor, ContinuationApproval, HistorySpec]:
    spec = history or history_fixture(corpus, path.parent)
    authority_id = str(uuid4())
    now = datetime.now(UTC)
    start, end = (now - timedelta(minutes=1)).isoformat(), (now + timedelta(hours=1)).isoformat()
    permit: JsonObject = {
        "schema": "probe-history-import-approval-1",
        "approved_by": "offline-fixture" if synthetic else "human-user",
        "decision": "synthetic-history-import-only"
        if synthetic
        else "approve-probe-history-import",
        "valid_from": start,
        "expires_at": end,
        "bound": bootstrap_binding(path, authority_id, device_id(), spec),
    }
    anchor = bootstrap_authority(path, spec, authority_id, device_id(), permit, synthetic=synthetic)
    rid, aid, risk = run_id or str(uuid4()), approval_id or str(uuid4()), risk_id or str(uuid4())
    history_manifest = object_(rows(read_snapshot(path)["events"])[0]["event"])["data"]
    manifest = object_(object_(history_manifest)["history_manifest"])
    bound = candidate_binding(rid, aid, risk, corpus, anchor, manifest)
    a = ContinuationApproval(
        aid,
        risk,
        "offline-fixture" if synthetic else "human-user",
        "synthetic-continuation-only" if synthetic else "approve-one-probe-continuation",
        ("recording-only", "synthetic-risk-only")
        if synthetic
        else ("accept-one-prior-uncertainty", "read-openai-api-key", "paid-responses"),
        start,
        end,
        bound,
    )
    return anchor, a, spec


def execute_http_fixture(
    run_id: str, path: Path, a: ProbeApproval, *, transport: paid.HTTPProbeTransport | None = None
) -> JsonObject:
    corpus = probe.prepare()
    a.validate(binding(run_id, path, corpus), synthetic=False)
    anchor, approval, _ = fixture_case(
        path, corpus, synthetic=False, run_id=run_id, approval_id=a.approval_id
    )
    return continued_probe.execute(path, anchor, approval, transport=transport)


def read_fixture_journal(path: Path, run_id: str) -> JsonObject:
    matches = [r for r in rows(read_snapshot(path)["runs"]) if r["run_id"] == run_id]
    assert len(matches) == 1
    return journal_from_run(matches[0])


def audit_fixture_report(report: JsonObject) -> JsonObject:
    runtime = Path(
        str(
            object_(object_(object_(report["authority"])["meta"])["history_bundle"])["runtime_path"]
        )
    )
    return continued_probe.audit_report(report, runtime)


def reduce_fixture_journal(
    bound: JsonObject, corpus: JsonObject, entries: list[JsonObject]
) -> JsonObject:
    return reduce_candidate_trace(bound, corpus, entries)
