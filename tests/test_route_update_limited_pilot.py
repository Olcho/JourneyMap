"""V4 caps, strict pending boundaries and pinned v3 compatibility; no API calls."""

import copy
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import cast

import pytest

from journeymap.adapters.route_update import obj, rows
from journeymap.adapters.route_update_count import CountHTTPTransport, count_wire
from journeymap.adapters.route_update_live import CONFIG, HTTPTransport
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.experiments import route_update_counted as runner
from journeymap.experiments import route_update_counted_plan as planning
from journeymap.experiments import route_update_live_plan as legacy
from journeymap.experiments.pickup_cue_live_plan import source_identity
from journeymap.experiments.pilot_journal import digest
from journeymap.experiments.provenance import runtime_identity
from journeymap.experiments.route_update import demo_fixtures
from journeymap.experiments.route_update_counted_cost import CountedLedger, ledger_for_plan
from journeymap.experiments.route_update_counted_session import CountedSession
from journeymap.experiments.route_update_limited_pilot import (
    AUDIT,
    COST,
    POLICY,
    authorization_template,
)
from journeymap.experiments.route_update_live import LiveFixture
from journeymap.experiments.route_update_live_audit import audit_directory
from journeymap.experiments.route_update_live_cost import CostStop
from journeymap.experiments.route_update_pilot_compatibility import (
    create_compatibility,
    semantic_probe,
    validate_compatibility,
)

ROOT = Path(__file__).resolve().parents[1]
WAIT = '{"action_type":"WAIT","payload":{"duration":1}}'


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    old = os.environ.get

    def get(k: str, d: str | None = None) -> str | None:
        if k == "OPENAI_API_KEY":
            raise AssertionError("key lookup forbidden")
        return old(k, d)

    def forbidden(*a: object, **kw: object) -> None:
        raise AssertionError("HTTP/network forbidden")

    monkeypatch.setattr(os.environ, "get", get)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(HTTPTransport, "__init__", forbidden)
    monkeypatch.setattr(CountHTTPTransport, "__init__", forbidden)


@pytest.fixture(scope="module")
def actual_evidence() -> tuple[JsonObject, JsonObject]:
    fixture_path = os.environ.get("JOURNEYMAP_E04_PILOT_FIXTURE")
    path = (
        Path(fixture_path)
        if fixture_path
        else ROOT / "tests/fixtures/experiment04-pilot-baseline.json"
    )
    if not fixture_path and not path.is_file():
        pytest.skip("E04 compatibility requires the preserved private pilot witness fixture")
    f = obj(json.loads(path.read_text(encoding="utf-8")))
    current: JsonObject = {
        p.relative_to(ROOT).as_posix(): p.read_bytes().decode()
        for p in (ROOT / "src/journeymap").rglob("*.py")
    }
    current["pyproject.toml"] = (ROOT / "pyproject.toml").read_bytes().decode()
    review = obj(json.loads(cast(str, f["witness_utf8"])))
    record = create_compatibility(
        obj(f["baseline_sources"]),
        cast(str, f["witness_utf8"]),
        obj(f["baseline_semantics"]),
        current,
        source_identity(),
        runtime_identity(),
    )
    return review, record


def live_plan(tmp_path: Path, evidence: tuple[JsonObject, JsonObject]) -> JsonObject:
    review, record = evidence
    return planning.make_plan(
        "pilot",
        "live",
        tmp_path / "run",
        cost_policy=POLICY,
        service_review=review,
        compatibility=record,
    )


def fixtures() -> tuple[LiveFixture, ...]:
    demos = demo_fixtures()
    return tuple(
        LiveFixture(
            demos[i % 2].initial.outputs[0],
            (
                demos[i % 2].branches[0].outputs,
                demos[i % 2].branches[1].outputs,
                demos[i % 2].branches[2].outputs,
            ),
        )
        for i in range(6)
    )


def test_mock_null_caps_and_v2_research_equivalence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p4 = planning.make_plan("pilot", "recording-mock", tmp_path / "v4", cost_policy=POLICY)
    r4 = runner.execute(p4, tmp_path / "v4", fixtures=fixtures())
    r2 = runner.execute(
        planning.make_plan("pilot", "recording-mock", tmp_path / "v2"),
        tmp_path / "v2",
        fixtures=fixtures(),
    )
    assert r4["status"] == "INCLUDED" and r4["audit_version"] == AUDIT
    # Parent export digest changes with protocol version, outside model/research input.
    compared4, compared2 = copy.deepcopy(r4), copy.deepcopy(r2)
    for report in (compared4, compared2):
        for branch in rows(report["branches"]):
            obj(branch["parent"]).pop("record_sha256")
    for k in ("prefixes", "branches", "summary", "send_attempts", "count_send_attempts"):
        assert compared4[k] == compared2[k]
    ledger = obj(r4["ledger"])
    for k, v in COST.items():
        assert ledger[k] == v
    assert ledger["count_charge_estimate_usd"] is None
    assert "combined_reserved_usd" not in ledger and "count_reserved_usd" not in ledger
    shutil.copytree(tmp_path / "v4", tmp_path / "relocated")

    def forbidden(*a: object, **kw: object) -> None:
        raise AssertionError("readonly audit must not resolve source paths or use Git")

    monkeypatch.setattr(Path, "resolve", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    assert audit_directory(tmp_path / "relocated") == r4


@pytest.mark.parametrize(
    "count,stop",
    [
        (20001, "COUNT_INPUT_LIMIT_NOT_GENERATED"),
        ("bad", "COUNT_INVALID_RESPONSE"),
        ("TIMEOUT", "COUNT_INFRASTRUCTURE"),
        (1001, "COUNT_USAGE_MISMATCH"),
    ],
)
def test_stops_before_additional_calls(tmp_path: Path, count: int | str, stop: str) -> None:
    p = planning.make_plan("pilot", "recording-mock", tmp_path / "run", cost_policy=POLICY)
    r = runner.execute(p, tmp_path / "run", fixtures=fixtures(), count_outcomes=(count,))
    assert r["stop"] == stop and r["count_send_attempts"] == 1
    assert r["send_attempts"] == (1 if count == 1001 else 0)
    assert obj(r["ledger"])["count_charge_estimate_usd"] is None


def test_114_caps_reservations_retained_and_wire_drift(tmp_path: Path) -> None:
    p = planning.make_plan("pilot", "recording-mock", tmp_path / "unused", cost_policy=POLICY)
    ledger = ledger_for_plan(p)
    s = CountedSession("caps", CONFIG, "recording-mock")
    try:
        prepared = s.prepare(initial=True)
        wire = obj(prepared["wire"])
        cw = count_wire(wire, obj(prepared["semantic_input"]), CONFIG)
        for _ in range(114):
            ledger.begin_count(cw, "recording-mock")
            ledger.counts[-1]["sent"] = "ATTEMPTED_CHARGE_UNKNOWN"
            ledger.receive_count('{"object":"response.input_tokens","input_tokens":1000}', None)
            ledger.reserve(wire, "recording-mock")
            ledger.entries[-1]["sent"] = "ATTEMPTED_CHARGE_UNKNOWN"
        assert len(ledger.counts) + len(ledger.entries) == 228
        with pytest.raises(CostStop):
            ledger.begin_count(cw, "recording-mock")
        ledger.counts[-1]["generation_reserved"] = False
        with pytest.raises(CostStop, match="CALL_LIMIT"):
            ledger.reserve(wire, "recording-mock")
        snap = ledger.snapshot()
        assert (
            snap["generation_reserved_usd"]
            == snap["generation_unconfirmed_reservation_usd"]
            == "8.03472"
        )
        assert snap["count_send_attempts"] == 114 and snap["count_charge_estimate_usd"] is None
        bad = copy.deepcopy(wire)
        bad["sha256"] = "drift"
        with pytest.raises(CostStop, match="BINDING"):
            ledger.reserve(bad, "recording-mock")
    finally:
        s.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("count_cap", True),
        ("generation_cap", 115),
        ("network_cap", 229),
        ("prefix_count", 5),
        ("stage", "smoke"),
        ("scope", "count-only"),
        ("cost_policy", "SMOKE_COUNT_FEE_UNKNOWN_V1"),
        ("total_cost_bound_guaranteed", 0),
        ("combined_cost_upper_bound_usd", "10.00"),
        ("generation_reservation_cap_usd", "8.10520"),
    ],
)
def test_resealed_scope_tamper(tmp_path: Path, field: str, value: JsonValue) -> None:
    p = planning.make_plan("pilot", "recording-mock", tmp_path / "unused", cost_policy=POLICY)
    p[field] = value
    p["plan_sha256"] = digest({k: v for k, v in p.items() if k != "plan_sha256"})
    with pytest.raises(ValueError):
        planning.validate_plan(p)


def test_policy_ledger_dispatch_and_modes(tmp_path: Path) -> None:
    for stage, scope in (("smoke", "count-and-generate"), ("pilot", "count-only")):
        with pytest.raises(ValueError):
            planning.make_plan(
                stage, "recording-mock", tmp_path / "bad", scope=scope, cost_policy=POLICY
            )
    for cap, g, c, n, fee in (
        ("0.07048", 1, 1, 2, None),
        ("8.03472", 114, True, 228, None),
        ("8.03472", 114, 114, 228, "0"),
    ):
        with pytest.raises(ValueError):
            CountedLedger(cap, g, c, n, fee, cost_policy=POLICY)
    with pytest.raises(ValueError):
        planning.make_plan("pilot", "live", tmp_path / "bad", cost_policy=POLICY)
    assert planning.make_plan("pilot", "live", tmp_path / "v2")["state"] == "DRAFT"


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "PENDING_USER_APPROVAL"),
        ("accept_count_fee_unknown", False),
        ("accept_count_fee_unknown", 1),
        ("plan_sha256", "wrong"),
        ("output_path", "wrong"),
        ("network_cap", 229),
        ("prefix_count", True),
        ("fallback", 0),
    ],
)
def test_approval_before_key_http_output_claim(
    tmp_path: Path, actual_evidence: tuple[JsonObject, JsonObject], field: str, value: JsonValue
) -> None:
    p = live_plan(tmp_path, actual_evidence)
    a = authorization_template(p)
    a.update(status="HUMAN_APPROVED", accept_count_fee_unknown=True)
    a[field] = value
    with pytest.raises(ValueError):
        runner.execute(p, tmp_path / "run", authorization=a)
    assert not (tmp_path / "run").exists()
    assert not (ROOT / "trials/e04-plan-claims" / (cast(str, p["plan_sha256"]) + ".json")).exists()


def test_service_price_approval_separation_and_old_smoke_rejected(
    tmp_path: Path, actual_evidence: tuple[JsonObject, JsonObject]
) -> None:
    p = live_plan(tmp_path, actual_evidence)
    assert p["state"] == "READY_FOR_REVIEW"
    ready = obj(p["readiness"])
    assert ready["service_evidence"] == "RECORDED_ACTUAL_V3_SMOKE"
    assert (
        ready["count_price"] == "UNKNOWN" and ready["send_authorization"] == "PENDING_USER_APPROVAL"
    )
    smoke_approval = obj(obj(actual_evidence[0]["witness"])["authorization"])
    for a in (None, smoke_approval, authorization_template(p)):
        with pytest.raises(ValueError):
            runner.execute(p, tmp_path / "run", authorization=a)
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize(
    "kind", ["remove-change", "protected", "semantics", "witness", "baseline", "runtime"]
)
def test_resealed_compatibility_tamper(
    tmp_path: Path, actual_evidence: tuple[JsonObject, JsonObject], kind: str
) -> None:
    review, original = actual_evidence
    record = copy.deepcopy(original)
    if kind == "remove-change":
        record["changes"] = cast(list[JsonValue], record["changes"])[:-1]
    elif kind == "protected":
        record["protected_files"] = []
    elif kind == "semantics":
        obj(record["baseline_semantics"])["actual_result_sha256"] = "changed"
        obj(record["current_semantics"])["actual_result_sha256"] = "changed"
    elif kind == "witness":
        record["witness_utf8"] = cast(str, record["witness_utf8"]) + " "
    elif kind == "baseline":
        obj(record["baseline_sources"])["pyproject.toml"] = "changed"
    else:
        record["runtime"] = {"implementation": "CPython", "version": [3, 12, 13]}
    record["record_sha256"] = digest({k: v for k, v in record.items() if k != "record_sha256"})
    with pytest.raises(ValueError):
        validate_compatibility(record, review, source_identity(), runtime_identity())


def test_current_source_runtime_and_mock_evidence_rejected(
    tmp_path: Path, actual_evidence: tuple[JsonObject, JsonObject], monkeypatch: pytest.MonkeyPatch
) -> None:
    p = live_plan(tmp_path, actual_evidence)
    a = authorization_template(p)
    a.update(status="HUMAN_APPROVED", accept_count_fee_unknown=True)
    source = dict(obj(p["source"]))
    source["working_source_sha256"] = "changed"
    monkeypatch.setattr(legacy, "source_identity", lambda: source)
    with pytest.raises(ValueError):
        runner.execute(p, tmp_path / "run", authorization=a)
    assert not (tmp_path / "run").exists()
    with pytest.raises(ValueError):
        planning.make_plan(
            "pilot",
            "recording-mock",
            tmp_path / "bad",
            cost_policy=POLICY,
            service_review=actual_evidence[0],
            compatibility=actual_evidence[1],
        )


def test_semantics_proof_actual_and_a_b_followups(
    actual_evidence: tuple[JsonObject, JsonObject],
) -> None:
    review, record = actual_evidence
    proof = semantic_probe(review)
    assert canonical_json(proof) == canonical_json(record["baseline_semantics"])
    cases = rows(proof["followup_cases"])
    assert len(cases) == 9 and all("first_move_policies" in obj(c["result"]) for c in cases)
    assert any(obj(c["result"])["stop"] == "PREFIX_UNSUCCESSFUL" for c in cases)


@pytest.mark.parametrize("kind", ["COUNT_RESPONSE", "INTENT", "SEND"])
def test_recording_failure_retains_policy_reservations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    from journeymap.experiments.pilot_journal import AttemptJournal

    original = AttemptJournal.append

    def fail(self: AttemptJournal, k: str, data: JsonObject) -> None:
        if k == kind:
            raise OSError("synthetic recording failure")
        original(self, k, data)

    monkeypatch.setattr(AttemptJournal, "append", fail)
    p = planning.make_plan("pilot", "recording-mock", tmp_path / "run", cost_policy=POLICY)
    r = runner.execute(p, tmp_path / "run", fixtures=fixtures())
    assert r["status"] == "INCOMPLETE" and obj(r["ledger"])["count_charge_estimate_usd"] is None
    assert obj(r["ledger"])["count_send_attempts"] == 1
    if kind == "SEND":
        assert obj(r["ledger"])["generation_reserved_usd"] == "0.07048"


def test_current_runtime_changed_blocks_before_output(
    tmp_path: Path, actual_evidence: tuple[JsonObject, JsonObject], monkeypatch: pytest.MonkeyPatch
) -> None:
    p = live_plan(tmp_path, actual_evidence)
    a = authorization_template(p)
    a.update(status="HUMAN_APPROVED", accept_count_fee_unknown=True)
    monkeypatch.setattr(
        legacy, "runtime_identity", lambda: {"implementation": "CPython", "version": [3, 12, 13]}
    )
    with pytest.raises(ValueError):
        runner.execute(p, tmp_path / "run", authorization=a)
    assert not (tmp_path / "run").exists()


def test_noneligible_wait_prefix_not_replaced(tmp_path: Path) -> None:
    p = planning.make_plan("pilot", "recording-mock", tmp_path / "run", cost_policy=POLICY)
    fixture = LiveFixture(WAIT, ((), (), ()))
    r = runner.execute(p, tmp_path / "run", fixtures=(fixture,) * 6)
    assert len(rows(r["prefixes"])) == 6 and r["branches"] == []
    assert r["send_attempts"] == r["count_send_attempts"] == 6
    assert all(row["stop"] == "PREFIX_UNSUCCESSFUL" for row in rows(r["prefixes"]))


def test_config_and_mock_witness_tamper(
    tmp_path: Path, actual_evidence: tuple[JsonObject, JsonObject]
) -> None:
    p = planning.make_plan("pilot", "recording-mock", tmp_path / "run", cost_policy=POLICY)
    obj(p["config"])["model"] = "other"
    p["plan_sha256"] = digest({k: v for k, v in p.items() if k != "plan_sha256"})
    with pytest.raises(ValueError):
        planning.validate_plan(p)
    review = copy.deepcopy(actual_evidence[0])
    obj(obj(review["witness"])["plan"])["mode"] = "recording-mock"
    review["witness_sha256"] = digest(obj(review["witness"]))
    with pytest.raises(ValueError):
        validate_compatibility(actual_evidence[1], review, source_identity(), runtime_identity())
