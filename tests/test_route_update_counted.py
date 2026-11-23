"""E04 counted protocol correctness: no network, keys or behavioral evidence."""

import os
import shutil
from pathlib import Path
from typing import cast

import pytest

from journeymap.adapters.llm import strict_json
from journeymap.adapters.route_update import obj, rows
from journeymap.adapters.route_update_count import (
    CountHTTPTransport,
    RecordingCountTransport,
    count_wire,
    token_count,
)
from journeymap.adapters.route_update_live import CONFIG, HTTPTransport, RecordingTransport
from journeymap.core.canonical import JsonObject, canonical_json
from journeymap.experiments import route_update_live_plan as legacy
from journeymap.experiments.pilot_journal import AttemptJournal, digest, inspect_journal
from journeymap.experiments.route_update import demo_fixtures, move
from journeymap.experiments.route_update_counted import execute
from journeymap.experiments.route_update_counted_cost import CountedLedger
from journeymap.experiments.route_update_counted_plan import (
    authorize_network,
    make_plan,
    preflight,
    validate_plan,
)
from journeymap.experiments.route_update_counted_service import review_smoke, validate_review
from journeymap.experiments.route_update_counted_session import CountedSession
from journeymap.experiments.route_update_live import LiveFixture
from journeymap.experiments.route_update_live_audit import audit_directory
from journeymap.experiments.route_update_live_cost import CostStop
from journeymap.scenarios.route_update import CONDITIONS

WAIT = '{"action_type":"WAIT","payload":{"duration":1}}'


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    original = os.environ.get

    def guarded(name: str, default: str | None = None) -> str | None:
        if name == "OPENAI_API_KEY":
            raise AssertionError("key lookup forbidden")
        return original(name, default)

    def forbidden(*a: object, **kw: object) -> None:
        raise AssertionError("HTTP construction forbidden")

    monkeypatch.setattr(os.environ, "get", guarded)
    for cls in (HTTPTransport, CountHTTPTransport):
        monkeypatch.setattr(cls, "__init__", forbidden)


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


def run(
    tmp_path: Path,
    *,
    stage: str = "smoke",
    counts: tuple[int | str, ...] | None = None,
    fixture: LiveFixture | None = None,
    scope: str = "count-and-generate",
) -> tuple[Path, JsonObject]:
    directory = tmp_path / "run"
    plan = make_plan(stage, "recording-mock", directory, scope=scope)
    data = (
        (fixture or fixtures()[0],)
        if stage == "smoke"
        else (fixture,) * 6
        if fixture
        else fixtures()
    )
    return directory, execute(plan, directory, fixtures=data, count_outcomes=counts)


@pytest.mark.parametrize(
    "raw",
    [
        "{}",
        "[]",
        "null",
        '{"object":"wrong","input_tokens":1}',
        '{"object":"response.input_tokens","input_tokens":-1}',
        '{"object":"response.input_tokens","input_tokens":true}',
        '{"object":"response.input_tokens","input_tokens":1.0}',
        '{"object":"response.input_tokens","input_tokens":"1"}',
        '{"object":"response.input_tokens","input_tokens":1,"extra":0}',
        '{"object":"response.input_tokens","input_tokens":1,"input_tokens":2}',
    ],
)
def test_invalid_count(raw: str) -> None:
    with pytest.raises((ValueError, TypeError)):
        token_count(raw)


@pytest.mark.parametrize("count", [0, 1, 20000])
def test_count_shape_bounds(count: int) -> None:
    assert (
        token_count(canonical_json({"object": "response.input_tokens", "input_tokens": count}))
        == count
    )


@pytest.mark.parametrize(
    "outcome,stop",
    [
        (20001, "COUNT_INPUT_LIMIT_NOT_GENERATED"),
        (-1, "COUNT_INVALID_RESPONSE"),
        ('{"object":"response.input_tokens","input_tokens":true}', "COUNT_INVALID_RESPONSE"),
        ("bad", "COUNT_INVALID_RESPONSE"),
        ("TIMEOUT", "COUNT_INFRASTRUCTURE"),
        ("HTTP_ERROR", "COUNT_INFRASTRUCTURE"),
        ("TRANSPORT_ERROR", "COUNT_INFRASTRUCTURE"),
        (pytest.param("x" * 2000001, "COUNT_INFRASTRUCTURE", id="oversize-count")),
    ],
)
def test_no_generation_after_count_failure(tmp_path: Path, outcome: int | str, stop: str) -> None:
    directory, report = run(tmp_path, counts=(outcome,))
    assert report["status"] == "INCLUDED" and report["stop"] == stop
    assert report["count_send_attempts"] == 1 and report["send_attempts"] == 0
    assert not list(directory.glob("request-*.utf8"))
    assert obj(report["ledger"])["reserved_usd"] == "0.00000"
    assert obj(report["ledger"])["count_reserved_usd"] == "0.00010"
    assert audit_directory(directory) == report
    with pytest.raises(ValueError):
        execute(
            obj(strict_json((directory / "plan.json").read_text())),
            directory,
            fixtures=(fixtures()[0],),
        )


def test_order_count_projection_no_second_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    count_send, gen_send = RecordingCountTransport.send, RecordingTransport.send
    count_payloads: list[bytes] = []
    directory = tmp_path / "run"

    def count(self: RecordingCountTransport, payload: bytes) -> bytes:
        records = inspect_journal(directory / "attempts.jsonl").rows
        assert records[-1]["kind"] == "COUNT_SEND"
        assert (directory / "count-request-001.utf8").read_bytes() == payload
        count_payloads.append(payload)
        return count_send(self, payload)

    def gen(self: RecordingTransport, payload: bytes) -> bytes:
        records = inspect_journal(directory / "attempts.jsonl").rows
        assert [r["kind"] for r in records][-5:] == [
            "COUNT_SEND",
            "COUNT_RESPONSE",
            "COUNT_RESULT",
            "INTENT",
            "SEND",
        ]
        counted = obj(strict_json(count_payloads[-1].decode()))
        generated = obj(strict_json(payload.decode()))
        assert set(counted) == {"model", "input", "text", "reasoning"}
        assert all(counted[k] == generated[k] for k in counted)
        before = obj(obj(records[2]["data"])["prepared"])
        after = obj(obj(records[-2]["data"])["prepared"])
        assert before == after
        return gen_send(self, payload)

    monkeypatch.setattr(RecordingCountTransport, "send", count)
    monkeypatch.setattr(RecordingTransport, "send", gen)
    _, r = run(tmp_path)
    assert r["status"] == "INCLUDED" and r["network_send_attempts"] == 2
    assert r["send_attempts"] == r["count_send_attempts"] == 1
    with pytest.raises(ValueError, match="consumed"):
        t = RecordingCountTransport()
        count_send(t, b"{}")
        count_send(t, b"{}")


def test_balanced_policies_and_no_prompt_reinjection(tmp_path: Path) -> None:
    _, report = run(tmp_path, stage="pilot")
    assert (
        report["status"] == "INCLUDED"
        and report["send_attempts"] == report["count_send_attempts"] == 48
    )
    policies = rows(obj(report["summary"])["code_baselines"])
    assert len(policies) == 18 and all(p["policy_computed"] and p["model_called"] for p in policies)
    for branch in rows(report["branches"]):
        record = obj(branch["first_move_policies"])
        for name in ("OLD-MAP", "RECEIVED-UPDATE"):
            policy = obj(record[name])
            inputs = obj(policy["input"])
            position = obj(inputs["observation"])["position"]
            expected = (
                "B-to-D"
                if position == "B"
                else "A-to-D"
                if name == "RECEIVED-UPDATE" and branch["condition"] == CONDITIONS[2]
                else "A-to-C"
            )
            assert policy["selected_route"] == expected
            assert policy["optimal_first_routes"] == [expected]
            assert policy["tie_break"]
            assert set(inputs) == {"observation", "event_memory"}
        for step in rows(branch["steps"]):
            text = (
                cast(str, obj(step["wire"])["canonical_utf8"])
                if "wire" in step
                else canonical_json(step["semantic_input"])
            )
            assert "OLD-MAP" not in text and "RECEIVED-UPDATE" not in text
    assert obj(report["summary"])["actual_balance_complete"] is True


def test_unattempted_policy_records(tmp_path: Path) -> None:
    _, report = run(tmp_path, stage="pilot", counts=(1000, "TIMEOUT"))
    assert (
        report["status"] == "INCLUDED"
        and report["send_attempts"] == 1
        and report["count_send_attempts"] == 2
    )
    policies = rows(obj(report["summary"])["code_baselines"])
    assert len(policies) == 3 and all(
        p["policy_computed"] and not p["model_called"] for p in policies
    )
    assert all(p["model_first_judgment"] is None for p in policies)


def test_mismatch_keeps_usage_and_reservation(tmp_path: Path) -> None:
    _, report = run(tmp_path, counts=(1001,))
    assert report["status"] == "INCLUDED" and report["stop"] == "COUNT_USAGE_MISMATCH"
    assert report["network_send_attempts"] == 2
    ledger = obj(report["ledger"])
    entry = rows(ledger["entries"])[0]
    assert entry["usage_count_mismatch"] == {"counted": 1001, "returned": 1000}
    assert entry["usage"] and entry["confirmed_estimate_usd"]
    assert ledger["reserved_usd"] == "0.07048"


def test_explicit_count_transcript_exhaustion_no_fallback(tmp_path: Path) -> None:
    _, report = run(tmp_path, stage="pilot", counts=(1000,))
    assert report["stop"] == "COUNT_INFRASTRUCTURE" and report["send_attempts"] == 1
    assert report["count_send_attempts"] == 2


def test_count_only_mock_no_generation_or_pilot_metrics(tmp_path: Path) -> None:
    directory, r = run(tmp_path, scope="count-only")
    assert r["status"] == "INCLUDED" and r["stop"] == "COUNT_ONLY_COMPLETE"
    assert r["send_attempts"] == 0 and r["count_send_attempts"] == 1
    plan = obj(strict_json((directory / "plan.json").read_text()))
    assert plan["generation_cap"] == 0 and plan["network_cap"] == 1
    with pytest.raises(ValueError, match="count-only"):
        authorize_network(plan, None, "generate")
    with pytest.raises(ValueError):
        make_plan("pilot", "recording-mock", tmp_path / "other", scope="count-only")


@pytest.mark.parametrize("stage", ["smoke", "pilot"])
@pytest.mark.parametrize("operation", ["count", "generate"])
def test_unapproved_network_and_manual_ready_forbidden(
    tmp_path: Path, stage: str, operation: str
) -> None:
    plan = make_plan(stage, "live", tmp_path / "new")
    assert (
        plan["state"] == "DRAFT" and obj(plan["readiness"])["service_confirmation"] == "UNVERIFIED"
    )
    assert obj(plan["budget_review"])["approved_usd"] is None
    assert obj(plan["count_price_review"])["status"] == "UNKNOWN"
    assert plan["network_cap"] == (2 if stage == "smoke" else 228)
    with pytest.raises(ValueError, match="authorization"):
        authorize_network(plan, None, operation)
    with pytest.raises(ValueError):
        preflight(plan, tmp_path / "new", {"status": "HUMAN_APPROVED", "scope": "count-only"})
    plan["state"] = "READY"
    plan["plan_sha256"] = digest({k: v for k, v in plan.items() if k != "plan_sha256"})
    with pytest.raises(ValueError, match="mismatch"):
        validate_plan(plan)


def test_service_flag_and_mock_do_not_confirm_service(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        validate_review({"status": "RECORDED_LIVE_SMOKE"})
    directory, _ = run(tmp_path)
    with pytest.raises(ValueError):
        review_smoke(directory)
    with pytest.raises(ValueError):
        make_plan(
            "pilot", "live", tmp_path / "new", price_review={"status": "EXTERNALLY_REVIEWED_BOUND"}
        )


@pytest.mark.parametrize("mode", ["live", "recording-mock"])
def test_count_evidence_binding_and_consumption(mode: str) -> None:
    session = CountedSession("x", CONFIG, "recording-mock")
    try:
        prepared = session.prepare(initial=True)
        wire = obj(prepared["wire"])
        ledger = CountedLedger("1.00", 1, 1, 2, "0.001")
        with pytest.raises(CostStop, match="MISSING"):
            ledger.reserve(wire, mode)
        ledger.begin_count(count_wire(wire, obj(prepared["semantic_input"]), CONFIG), mode)
        assert (
            ledger.receive_count('{"object":"response.input_tokens","input_tokens":20000}', None)
            is None
        )
        changed = obj(strict_json(canonical_json(wire)))
        obj(changed["body"])["model"] = "different"
        with pytest.raises(CostStop, match="BINDING"):
            ledger.reserve(changed, mode)
        assert ledger.entries == []
        ledger.reserve(wire, mode)
        with pytest.raises(CostStop, match="BINDING"):
            ledger.reserve(wire, mode)
    finally:
        session.close()


@pytest.mark.parametrize("which", ["count", "network", "budget"])
def test_separate_count_limits(which: str) -> None:
    ledger = CountedLedger(
        "0.00001" if which == "budget" else "1",
        1,
        0 if which == "count" else 1,
        0 if which == "network" else 2,
        "0.001",
    )
    with pytest.raises(CostStop):
        ledger.begin_count({}, "recording-mock")
    assert ledger.counts == [] and ledger.entries == []


@pytest.mark.parametrize("version", ["legacy", "counted"])
def test_readonly_foreign_path_no_host_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    maker, validator = (
        (legacy.make_plan, legacy.validate_plan)
        if version == "legacy"
        else (make_plan, validate_plan)
    )
    plan = maker("smoke", "recording-mock", tmp_path / "run")
    plan = maker(
        "smoke",
        "recording-mock",
        r"C:\Users\original\smoke-final",
        saved_source=obj(plan["source"]),
        saved_runtime=obj(plan["runtime"]),
    )
    frozen = canonical_json(plan)

    def forbidden(*a: object, **kw: object) -> Path:
        raise AssertionError("audit path resolution prohibited")

    monkeypatch.setattr(Path, "resolve", forbidden)
    validator(plan)
    assert canonical_json(plan) == frozen
    plan["output_path"] = r"C:\tampered"
    with pytest.raises(ValueError, match="digest"):
        validator(plan)


@pytest.mark.parametrize("version", ["legacy", "counted"])
def test_native_output_mismatch(tmp_path: Path, version: str) -> None:
    maker, gate = (
        (legacy.make_plan, legacy.preflight) if version == "legacy" else (make_plan, preflight)
    )
    p = maker("smoke", "recording-mock", tmp_path / "run")
    with pytest.raises(ValueError, match="exact"):
        gate(p, tmp_path / "other")


def test_copy_audit_and_resealed_wrong_policy(tmp_path: Path) -> None:
    directory, r = run(tmp_path, stage="pilot", counts=(1000, "TIMEOUT"))
    copied = tmp_path / "copied"
    shutil.copytree(directory, copied)
    assert audit_directory(copied) == r
    records = inspect_journal(copied / "attempts.jsonl").rows
    for row in records:
        data = obj(row["data"])
        if row["kind"] == "ALL_FIRST_INPUTS_VALIDATED":
            obj(obj(obj(data[CONDITIONS[0]])["first_move_policies"])["OLD-MAP"])[
                "selected_route"
            ] = "A-to-D"
        if row["kind"] == "BRANCH_END":
            obj(obj(obj(data["result"])["first_move_policies"])["OLD-MAP"])["selected_route"] = (
                "A-to-D"
            )
            data["record_sha256"] = digest({k: v for k, v in data.items() if k != "record_sha256"})
            (copied / ("p001-" + CONDITIONS[0] + ".json")).write_text(canonical_json(data))
    path = copied / "attempts.jsonl"
    path.unlink()
    journal = AttemptJournal(path)
    for row in records:
        journal.append(cast(str, row["kind"]), obj(row["data"]))
    journal.close()
    assert audit_directory(copied)["status"] == "EXCLUDED"
    assert audit_directory(directory) == r


@pytest.mark.parametrize(
    "kind", ["COUNT_INTENT", "COUNT_SEND", "COUNT_RESPONSE", "COUNT_RESULT", "INTENT"]
)
def test_recording_failure_no_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    original = AttemptJournal.append

    def failed(self: AttemptJournal, k: str, data: JsonObject) -> None:
        if k == kind:
            raise OSError("injected durable failure")
        original(self, k, data)

    monkeypatch.setattr(AttemptJournal, "append", failed)
    _, report = run(tmp_path)
    assert report["status"] == "INCOMPLETE" and report["send_attempts"] == 0
    assert report["count_send_attempts"] == (0 if kind in ("COUNT_INTENT", "COUNT_SEND") else 1)


def test_wire_changed_after_count_stops_before_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = CountedLedger.receive_count

    def changed(self: CountedLedger, raw: str | None, failure: JsonObject | None) -> str | None:
        result = original(self, raw, failure)
        obj(self.counts[-1]["wire"])["generation_wire_sha256"] = "changed-after-count"
        return result

    monkeypatch.setattr(CountedLedger, "receive_count", changed)
    _, report = run(tmp_path)
    assert report["status"] == "INCLUDED" and report["stop"] == "COUNT_GENERATION_BINDING_ERROR"
    assert report["send_attempts"] == 0 and report["count_send_attempts"] == 1


@pytest.mark.parametrize(
    "outcome,stop",
    [
        ("REFUSAL", "COMPLETE"),
        ("malformed", "COMPLETE"),
        ("INCOMPLETE", "COMPLETE"),
        ("TOKEN_CAP", "OUTPUT_BUDGET_DIAGNOSTIC_STOP"),
        ("TIMEOUT", "INFRASTRUCTURE"),
        ("HTTP_ERROR", "INFRASTRUCTURE"),
        ("IDENTITY", "INTEGRITY"),
        ("USAGE", "USAGE_COST_ERROR"),
        ("TIER", "USAGE_COST_ERROR"),
    ],
)
def test_generation_failures_terminate_without_retry(
    tmp_path: Path, outcome: str, stop: str
) -> None:
    _, r = run(tmp_path, stage="pilot", fixture=LiveFixture(move("S-to-A"), ((outcome,),) * 3))
    assert r["status"] == "INCLUDED" and r["stop"] == stop
    assert r["count_send_attempts"] == r["send_attempts"] == (24 if stop == "COMPLETE" else 2)
    assert all(p["policy_computed"] for p in rows(obj(r["summary"])["code_baselines"]))


def test_maximum_separate_caps(tmp_path: Path) -> None:
    _, r = run(tmp_path, stage="pilot", fixture=LiveFixture(move("S-to-A"), ((WAIT,) * 6,) * 3))
    assert r["status"] == "INCLUDED" and r["send_attempts"] == r["count_send_attempts"] == 114
    assert r["network_send_attempts"] == 228
    assert obj(r["ledger"])["combined_reserved_usd"] == "8.04612"
    assert all(b["stop"] == "DECISION_LIMIT" for b in rows(r["branches"]))


@pytest.mark.parametrize(
    "file", ["count-request-001.utf8", "count-response-001.utf8", "request-001.utf8"]
)
def test_raw_count_or_generation_tamper_excluded(tmp_path: Path, file: str) -> None:
    directory, _ = run(tmp_path)
    p = directory / file
    p.write_bytes(p.read_bytes() + b" ")
    assert audit_directory(directory)["status"] == "EXCLUDED"


def test_live_count_only_approval_does_not_authorize_generation(tmp_path: Path) -> None:
    # Synthetic evidence and approval exist only in this unit fixture, not as live artifacts.
    text = "SYNTHETIC UNIT FIXTURE; not an actual official fee confirmation"
    price: JsonObject = {
        "status": "EXTERNALLY_REVIEWED_BOUND",
        "source": "https://developers.openai.com/synthetic-unit-fixture",
        "checked_date_kst": "2026-10-09",
        "upper_bound_usd": "0.001",
        "evidence_text": text,
        "evidence_sha256": digest({"text": text}),
    }
    p = make_plan(
        "smoke",
        "live",
        tmp_path / "unused",
        scope="count-only",
        price_review=price,
        saved_source={"unit_fixture": True},
        saved_runtime={"unit_fixture": True},
    )
    a: JsonObject = {
        "status": "HUMAN_APPROVED",
        "mode": "live",
        "plan_sha256": p["plan_sha256"],
        "stage": p["stage"],
        "scope": p["scope"],
        "output_path": p["output_path"],
        "count_cap": 1,
        "generation_cap": 0,
        "network_cap": 1,
        "approved_usd": obj(p["budget_review"])["stage_reservation_cap_usd"],
        "endpoints": [p["count_endpoint"]],
    }
    authorize_network(p, a, "count")
    with pytest.raises(ValueError, match="count-only"):
        authorize_network(p, a, "generate")
    a["count_cap"] = 2
    with pytest.raises(ValueError, match="exact"):
        authorize_network(p, a, "count")
    pilot = make_plan(
        "pilot",
        "live",
        tmp_path / "unused2",
        price_review=price,
        saved_source={"unit_fixture": True},
        saved_runtime={"unit_fixture": True},
    )
    assert (
        pilot["state"] == "DRAFT"
        and obj(pilot["readiness"])["service_confirmation"] == "UNVERIFIED"
    )
