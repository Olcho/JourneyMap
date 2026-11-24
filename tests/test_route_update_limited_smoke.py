"""Smoke 1/1/2 unknown-fee exception correctness; live-format records are SYNTHETIC."""

import os
import shutil
import socket
from pathlib import Path
from typing import cast

import pytest

from journeymap.adapters.llm import strict_json
from journeymap.adapters.route_update import obj, rows
from journeymap.adapters.route_update_count import (
    CountHTTPTransport,
    RecordingCountTransport,
    count_wire,
)
from journeymap.adapters.route_update_live import CONFIG, HTTPTransport, RecordingTransport
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.experiments import route_update_counted as runner
from journeymap.experiments import route_update_counted_plan as planning
from journeymap.experiments import route_update_live_plan as legacy
from journeymap.experiments.pilot_journal import AttemptJournal, digest, inspect_journal
from journeymap.experiments.route_update import move
from journeymap.experiments.route_update_counted_cost import CountedLedger, ledger_for_plan
from journeymap.experiments.route_update_counted_service import review_smoke, validate_review
from journeymap.experiments.route_update_counted_session import CountedSession
from journeymap.experiments.route_update_limited_smoke import (
    AUDIT,
    COST,
    POLICY,
    authorization_template,
)
from journeymap.experiments.route_update_live import LiveFixture
from journeymap.experiments.route_update_live_audit import audit_directory
from journeymap.experiments.route_update_live_cost import CostStop

WAIT = '{"action_type":"WAIT","payload":{"duration":1}}'


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    original = os.environ.get

    def guarded(k: str, default: str | None = None) -> str | None:
        if k == "OPENAI_API_KEY":
            raise AssertionError("key lookup forbidden")
        return original(k, default)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("real HTTP or network forbidden")

    monkeypatch.setattr(os.environ, "get", guarded)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for cls in (HTTPTransport, CountHTTPTransport):
        monkeypatch.setattr(cls, "__init__", forbidden)


def plan(tmp_path: Path, mode: str = "recording-mock") -> JsonObject:
    return planning.make_plan("smoke", mode, tmp_path / "run", cost_policy=POLICY)


def synthetic_authorization(p: JsonObject) -> JsonObject:
    a = authorization_template(p)
    a.update({"status": "HUMAN_APPROVED", "accept_count_fee_unknown": True})
    return a


def live_transports(
    monkeypatch: pytest.MonkeyPatch, *, count: int | str = 1000, generation: str = move("S-to-A")
) -> list[str]:
    attempts: list[str] = []

    class Count(RecordingCountTransport):
        def send(self, payload: bytes) -> bytes:
            attempts.append("count")
            return super().send(payload)

    class Generation(RecordingTransport):
        def send(self, payload: bytes) -> bytes:
            attempts.append("generate")
            return super().send(payload)

    monkeypatch.setattr(runner, "CountHTTPTransport", lambda config: Count(count))
    monkeypatch.setattr(runner, "HTTPTransport", lambda config: Generation(generation))
    return attempts


def test_mock_smoke_null_ledger_and_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = plan(tmp_path)
    r = runner.execute(p, tmp_path / "run", fixtures=(LiveFixture(move("S-to-A"), ((), (), ())),))
    assert (
        r["status"] == "INCLUDED"
        and r["audit_version"] == AUDIT
        and r["network_send_attempts"] == 2
    )
    assert r["branches"] == [] and obj(r["summary"])["branches_planned"] == 0
    ledger = obj(r["ledger"])
    for k, v in COST.items():
        assert ledger[k] == v
    assert ledger["generation_reserved_usd"] == "0.07048"
    assert ledger["generation_confirmed_usage_estimate_usd"] == "0.0035"
    assert ledger["count_charge_estimate_usd"] is None and ledger["count_send_attempts"] == 1
    assert all(
        k not in ledger
        for k in (
            "budget_cap_usd",
            "combined_reserved_usd",
            "count_reserved_usd",
            "confirmed_usage_estimate_usd",
        )
    )
    assert rows(ledger["count_entries"])[0]["count_reservation_usd"] is None
    assert audit_directory(tmp_path / "run") == r
    shutil.copytree(tmp_path / "run", tmp_path / "copy")

    def forbidden(*a: object, **kw: object) -> Path:
        raise AssertionError("readonly path resolve forbidden")

    monkeypatch.setattr(Path, "resolve", forbidden)
    assert audit_directory(tmp_path / "copy") == r
    assert inspect_journal(tmp_path / "copy/attempts.jsonl").status == "COMPLETE"


@pytest.mark.parametrize(
    "count,stop",
    [
        (20001, "COUNT_INPUT_LIMIT_NOT_GENERATED"),
        ("TIMEOUT", "COUNT_INFRASTRUCTURE"),
        ("bad", "COUNT_INVALID_RESPONSE"),
    ],
)
def test_count_failure_no_generation(tmp_path: Path, count: int | str, stop: str) -> None:
    r = runner.execute(
        plan(tmp_path),
        tmp_path / "run",
        fixtures=(LiveFixture(WAIT, ((), (), ())),),
        count_outcomes=(count,),
    )
    assert r["status"] == "INCLUDED" and r["stop"] == stop
    assert r["send_attempts"] == 0 and r["count_send_attempts"] == 1
    ledger = obj(r["ledger"])
    assert ledger["generation_reserved_usd"] == "0.00000"
    assert (
        ledger["count_charge_estimate_usd"] is None
        and ledger["combined_cost_upper_bound_usd"] is None
    )


@pytest.mark.parametrize(
    "outcome,stop",
    [
        ("TIMEOUT", "INFRASTRUCTURE"),
        ("REFUSAL", "COMPLETE"),
        ("INCOMPLETE", "COMPLETE"),
        ("TOKEN_CAP", "OUTPUT_BUDGET_DIAGNOSTIC_STOP"),
        ("IDENTITY", "INTEGRITY"),
    ],
)
def test_generation_failure_single_send(tmp_path: Path, outcome: str, stop: str) -> None:
    p = plan(tmp_path)
    r = runner.execute(p, tmp_path / "run", fixtures=(LiveFixture(outcome, ((), (), ())),))
    assert r["status"] == "INCLUDED" and r["stop"] == stop and r["network_send_attempts"] == 2
    assert obj(r["ledger"])["count_charge_estimate_usd"] is None
    with pytest.raises(ValueError):
        runner.execute(p, tmp_path / "run", fixtures=(LiveFixture(WAIT, ((), (), ())),))


@pytest.mark.parametrize(
    "field,value",
    [
        ("stage", "pilot"),
        ("scope", "count-only"),
        ("prefix_count", 2),
        ("count_cap", 2),
        ("generation_cap", 2),
        ("network_cap", 3),
        ("count_cap", True),
        ("count_endpoint", "https://wrong.invalid"),
        ("combined_cost_upper_bound_usd", "10.00"),
        ("total_cost_bound_guaranteed", 0),
    ],
)
def test_resealed_scope_or_money_tamper(tmp_path: Path, field: str, value: JsonValue) -> None:
    p = plan(tmp_path, "live")
    p[field] = value
    p["plan_sha256"] = digest({k: v for k, v in p.items() if k != "plan_sha256"})
    with pytest.raises(ValueError):
        planning.preflight(p, tmp_path / "run", synthetic_authorization(plan(tmp_path, "live")))
    assert not (tmp_path / "run").exists()


def test_explicit_scope_and_default_v2_preserved(tmp_path: Path) -> None:
    for stage, scope in [("pilot", "count-and-generate"), ("smoke", "count-only")]:
        with pytest.raises(ValueError):
            planning.make_plan(stage, "live", tmp_path / "bad", scope=scope, cost_policy=POLICY)
    p = planning.make_plan("smoke", "live", tmp_path / "v2")
    assert p["protocol"] == "experiment04-responses-execution-2" and p["state"] == "DRAFT"
    assert "cost_policy" not in p
    with pytest.raises(CostStop, match="COUNT_PRICE_UNKNOWN"):
        CountedLedger("0.07048", 1, 1, 2, None)
    with pytest.raises(ValueError):
        planning.make_plan("smoke", "live", tmp_path / "wrong", cost_policy="OTHER")
    p = plan(tmp_path)
    obj(p["config"])["model"] = "other"
    p["plan_sha256"] = digest({k: v for k, v in p.items() if k != "plan_sha256"})
    try:
        with pytest.raises(ValueError):
            planning.validate_plan(p)
    finally:
        obj(p["config"])["model"] = "gpt-6.1-sol"


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "PENDING_USER_APPROVAL"),
        ("accept_count_fee_unknown", False),
        ("accept_count_fee_unknown", 1),
        ("plan_sha256", "wrong"),
        ("output_path", "wrong"),
        ("cost_policy", "OTHER"),
        ("count_cap", 2),
        ("count_cap", True),
        ("combined_cost_upper_bound_usd", "10.00"),
        ("total_cost_bound_guaranteed", 0),
        ("automatic_retry", 0),
    ],
)
def test_bad_approval_before_http(tmp_path: Path, field: str, value: JsonValue) -> None:
    p = plan(tmp_path, "live")
    a = synthetic_authorization(p)
    a[field] = value
    with pytest.raises(ValueError, match="HUMAN_APPROVED"):
        runner.execute(p, tmp_path / "run", authorization=a)
    assert not (tmp_path / "run").exists()


def test_missing_and_old_approval_rejected(tmp_path: Path) -> None:
    p = plan(tmp_path, "live")
    approvals: tuple[JsonObject | None, ...] = (
        None,
        {"status": "HUMAN_APPROVED", "approved_usd": "10.00"},
    )
    for a in approvals:
        with pytest.raises(ValueError):
            runner.execute(p, tmp_path / "run", authorization=a)
    assert authorization_template(p)["status"] == "PENDING_USER_APPROVAL"
    assert authorization_template(p)["accept_count_fee_unknown"] is False


@pytest.mark.parametrize(
    "count,generation,stop",
    [
        (1000, WAIT, "COMPLETE"),
        (1001, WAIT, "COUNT_USAGE_MISMATCH"),
        ("HTTP_ERROR", WAIT, "COUNT_INFRASTRUCTURE"),
    ],
)
def test_live_boundary_is_synthetic_and_no_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int | str, generation: str, stop: str
) -> None:
    p = plan(tmp_path, "live")
    attempts = live_transports(monkeypatch, count=count, generation=generation)
    r = runner.execute(p, tmp_path / "run", authorization=synthetic_authorization(p))
    assert r["status"] == "INCLUDED" and r["stop"] == stop
    assert attempts == (["count"] if count == "HTTP_ERROR" else ["count", "generate"])
    assert r["branches"] == [] and obj(r["ledger"])["count_charge_estimate_usd"] is None
    assert audit_directory(tmp_path / "run") == r
    with pytest.raises(ValueError):
        runner.execute(p, tmp_path / "run", authorization=synthetic_authorization(p))
    assert len(attempts) == r["network_send_attempts"]


def test_live_witness_unknown_not_pilot_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = plan(tmp_path, "live")
    live_transports(monkeypatch)
    r = runner.execute(p, tmp_path / "run", authorization=synthetic_authorization(p))
    assert r["status"] == "INCLUDED"
    witness = review_smoke(tmp_path / "run")
    validate_review(witness)
    assert witness["cost_review"] == COST
    assert "not proof of service facts" in cast(str, witness["authenticity_limit"])
    # Only a synthetic temp fixture. No service witness is emitted as a task artifact.
    pilot = planning.make_plan("pilot", "live", tmp_path / "pilot", service_review=witness)
    assert pilot["state"] == "DRAFT" and obj(pilot["authorization"])["status"] == "UNAUTHORIZED"
    assert obj(pilot["count_price_review"])["status"] == "UNKNOWN" and "cost_policy" not in pilot
    obj(witness["cost_review"])["count_price_status"] = "EXTERNALLY_REVIEWED_BOUND"
    with pytest.raises(ValueError):
        validate_review(witness)
    (tmp_path / "run/authorization.json").unlink()
    assert audit_directory(tmp_path / "run")["status"] == "EXCLUDED"


def test_mock_never_service_witness(tmp_path: Path) -> None:
    runner.execute(plan(tmp_path), tmp_path / "run", fixtures=(LiveFixture(WAIT, ((), (), ())),))
    with pytest.raises(ValueError):
        review_smoke(tmp_path / "run")


def test_unknown_ledger_single_pair_and_wire_binding(tmp_path: Path) -> None:
    p = plan(tmp_path)
    ledger = ledger_for_plan(p)
    s = CountedSession("unit", CONFIG, "recording-mock")
    try:
        prepared = s.prepare(initial=True)
        wire = obj(prepared["wire"])
        ledger.begin_count(
            count_wire(wire, obj(prepared["semantic_input"]), CONFIG), "recording-mock"
        )
        with pytest.raises(CostStop):
            ledger.begin_count({}, "recording-mock")
        assert (
            ledger.receive_count('{"object":"response.input_tokens","input_tokens":1000}', None)
            is None
        )
        changed = obj(strict_json(canonical_json(wire)))
        changed["sha256"] = "changed"
        with pytest.raises(CostStop, match="BINDING"):
            ledger.reserve(changed, "recording-mock")
        ledger.reserve(wire, "recording-mock")
        with pytest.raises(CostStop):
            ledger.reserve(wire, "recording-mock")
        assert ledger.snapshot()["generation_reserved_usd"] == "0.07048"
        with pytest.raises(ValueError):
            CountedLedger("10", 114, 114, 228, None, cost_policy=POLICY)
        with pytest.raises(ValueError):
            CountedLedger("0.07048", 1, 1, 2, "0.00010", cost_policy=POLICY)
    finally:
        s.close()


def test_source_changed_after_approval_blocks_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = plan(tmp_path, "live")
    attempts = live_transports(monkeypatch)
    original = RecordingCountTransport.send

    def drift(self: RecordingCountTransport, payload: bytes) -> bytes:
        response = original(self, payload)
        source = dict(obj(p["source"]))
        source["working_source_sha256"] = "changed-after-count"
        monkeypatch.setattr(legacy, "source_identity", lambda: source)
        # A changed source invalidates full contract reconstruction before generation.
        return response

    monkeypatch.setattr(RecordingCountTransport, "send", drift)
    r = runner.execute(p, tmp_path / "run", authorization=synthetic_authorization(p))
    assert attempts == ["count"] and r["send_attempts"] == 0
    assert (tmp_path / "run/recording-failure.json").exists()


@pytest.mark.parametrize("kind", ["COUNT_SEND", "COUNT_RESPONSE", "INTENT"])
def test_recording_failure_preserves_unknown_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    append = AttemptJournal.append

    def fail(self: AttemptJournal, k: str, data: JsonObject) -> None:
        if k == kind:
            raise OSError("synthetic durable recording failure")
        append(self, k, data)

    monkeypatch.setattr(AttemptJournal, "append", fail)
    r = runner.execute(
        plan(tmp_path), tmp_path / "run", fixtures=(LiveFixture(WAIT, ((), (), ())),)
    )
    assert r["status"] == "INCOMPLETE" and r["send_attempts"] == 0
    assert obj(r["ledger"])["count_charge_estimate_usd"] is None
