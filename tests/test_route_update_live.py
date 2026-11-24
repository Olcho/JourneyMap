"""E04 live preparation correctness only: every transport is inert or forbidden."""

import os
import shutil
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from journeymap.adapters.llm import strict_json
from journeymap.adapters.route_update import PROFILE, SCHEMA, obj, rows
from journeymap.adapters.route_update_live import (
    CONFIG,
    HTTPTransport,
    RecordingTransport,
    decode,
    wire,
)
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.experiments import route_update_live_plan as planning
from journeymap.experiments.pilot_journal import AttemptJournal, digest, inspect_journal
from journeymap.experiments.route_update import demo_fixtures, move
from journeymap.experiments.route_update_live import LiveFixture, execute
from journeymap.experiments.route_update_live_audit import audit_directory
from journeymap.experiments.route_update_live_cost import RESERVATION, CostStop, Ledger
from journeymap.experiments.route_update_live_plan import make_plan, preflight, validate_plan
from journeymap.experiments.route_update_live_session import LiveSession
from journeymap.scenarios.route_update import CONDITIONS

WAIT = '{"action_type":"WAIT","payload":{"duration":1}}'


@pytest.fixture(autouse=True)
def no_http_or_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    original = os.environ.get

    def guarded(name: str, default: str | None = None) -> str | None:
        if name == "OPENAI_API_KEY":
            raise AssertionError("credential lookup forbidden")
        return original(name, default)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("HTTP construction forbidden")

    monkeypatch.setattr(os.environ, "get", guarded)
    monkeypatch.setattr(HTTPTransport, "__init__", forbidden)


def demos() -> tuple[LiveFixture, ...]:
    base = demo_fixtures()
    return tuple(
        LiveFixture(
            base[i % 2].initial.outputs[0],
            (
                base[i % 2].branches[0].outputs,
                base[i % 2].branches[1].outputs,
                base[i % 2].branches[2].outputs,
            ),
        )
        for i in range(6)
    )


def run(
    tmp_path: Path,
    fixture: LiveFixture | None = None,
    *,
    stage: str = "pilot",
    mock_input_tokens: int = 1000,
) -> tuple[Path, JsonObject]:
    directory = tmp_path / "new-run"
    plan = make_plan(stage, "recording-mock", directory)
    fixtures = (fixture,) * (1 if stage == "smoke" else 6) if fixture else demos()
    return directory, execute(
        plan, directory, fixtures=fixtures, mock_input_tokens=mock_input_tokens
    )


def test_balanced_first_inputs_guard_before_any_followup_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = RecordingTransport.send
    sends: list[bytes] = []
    directory = tmp_path / "new-run"

    def spy(self: RecordingTransport, payload: bytes) -> bytes:
        journal = inspect_journal(directory / "attempts.jsonl")
        body = obj(strict_json(payload.decode()))
        inputs = obj(strict_json(cast(str, body["input"]).split("\nINPUT_JSON\n")[1]))
        if cast(int, obj(inputs["observation"])["time"]) >= 3:
            guards = [
                obj(row["data"])
                for row in journal.rows
                if row["kind"] == "ALL_FIRST_INPUTS_VALIDATED"
            ]
            assert guards
            guard = guards[-1]
            for condition in CONDITIONS:
                snapshot = obj(guard[condition])
                assert len(rows(snapshot["observations"])) == 2
                assert len(rows(snapshot["event_traces"])) == 1
                pending = obj(snapshot["pending"])
                assert len(rows(obj(pending["semantic_input"])["event_memory"])) == 1
                assert "ledger" not in cast(str, obj(pending["wire"])["canonical_utf8"])
            assert (
                obj(obj(guard[CONDITIONS[0]])["pending"])["wire"]
                == obj(obj(guard[CONDITIONS[1]])["pending"])["wire"]
            )
        assert obj(journal.rows[-1]["data"])["request_number"] == len(sends) + 1
        recorded = directory / f"request-{len(sends) + 1:03d}.utf8"
        assert recorded.read_bytes() == payload
        sends.append(payload)
        return original(self, payload)

    monkeypatch.setattr(RecordingTransport, "send", spy)
    _, report = run(tmp_path)
    assert report["status"] == "INCLUDED"
    assert report["send_attempts"] == 48
    assert report["initial_send_attempts"] == 6
    summary = obj(report["summary"])
    orders = rows(summary["orders"])
    assert summary["actual_balance_complete"] is True
    assert orders[4]["actual_order"] == [CONDITIONS[2], CONDITIONS[0], CONDITIONS[1]]
    for position in range(3):
        assert all(
            sum(cast(list[JsonValue], a["planned_order"])[position] == c for a in orders) == 2
            for c in CONDITIONS
        )
    assert obj(summary["A_matched_pairs"])["notice_only_true"] == 3
    assert Decimal(cast(str, obj(report["ledger"])["reserved_usd"])) == RESERVATION * 48
    for branch in rows(report["branches"]):
        assert branch["prefix_calls"] == 0 and branch["prefix_replays"] == 1
        assert len(rows(obj(obj(branch["prefix"])["semantic_input"])["event_memory"])) == 0
        assert obj(branch["prefix"])["provider_called"] is False


@pytest.mark.parametrize(
    "outcome,stop,response_status",
    [
        ("REFUSAL", "COMPLETE", "REFUSAL"),
        ("malformed", "COMPLETE", "MALFORMED"),
        ("INCOMPLETE", "COMPLETE", "INCOMPLETE"),
        ("TOKEN_CAP", "OUTPUT_BUDGET_DIAGNOSTIC_STOP", "INCOMPLETE"),
        ("TIMEOUT", "INFRASTRUCTURE", "INFRASTRUCTURE"),
        ("HTTP_ERROR", "INFRASTRUCTURE", "INFRASTRUCTURE"),
        ("TRANSPORT_ERROR", "INFRASTRUCTURE", "INFRASTRUCTURE"),
        ("IDENTITY", "INTEGRITY", "INTEGRITY"),
        ("USAGE", "USAGE_COST_ERROR", "COST_UNVERIFIED"),
        ("TIER", "USAGE_COST_ERROR", "COST_UNVERIFIED"),
    ],
)
def test_response_and_global_stops(
    tmp_path: Path, outcome: str, stop: str, response_status: str
) -> None:
    fixture = LiveFixture(move("S-to-A"), ((outcome,), (outcome,), (outcome,)))
    _, report = run(tmp_path, fixture)
    assert report["status"] == "INCLUDED"
    assert report["stop"] == stop
    branches = rows(report["branches"])
    assert obj(rows(branches[0]["steps"])[0])["response_status"] == response_status
    assert obj(branches[0]["metrics"])["first_appropriate"] is None
    if stop == "COMPLETE":
        assert report["send_attempts"] == 24 and len(branches) == 18
    else:
        assert report["send_attempts"] == 2 and len(branches) == 1
        assert obj(report["summary"])["initial_not_attempted"] == 5
        assert obj(obj(report["summary"])["A_matched_pairs"])["null_pair"] == 1
        if stop in ("INFRASTRUCTURE", "INTEGRITY", "USAGE_COST_ERROR"):
            assert obj(report["ledger"])["unconfirmed_reservation_usd"] == "0.07048"


@pytest.mark.parametrize("initial", [WAIT, "REFUSAL", "bad", "INCOMPLETE"])
def test_initial_failure_never_resamples(tmp_path: Path, initial: str) -> None:
    _, report = run(tmp_path, LiveFixture(initial, ((), (), ())))
    assert report["status"] == "INCLUDED" and report["send_attempts"] == 6
    assert report["branches"] == []
    assert obj(report["summary"])["A_interpretation"] == "PRIMARY_COMPARISON_UNAVAILABLE"
    assert all(c["first_appropriate"] is None for c in rows(obj(report["summary"])["branch_cases"]))


def test_B_only_no_A_primary_comparison(tmp_path: Path) -> None:
    _, report = run(tmp_path, demos()[1])
    summary = obj(report["summary"])
    assert summary["A_interpretation"] == "PRIMARY_COMPARISON_UNAVAILABLE"
    B = obj(obj(summary["strata"])["B"])
    assert B["prefix_denominator"] == 6
    assert all(
        c["B_first_D_direction"] == 6 for c in obj(B["conditions"]).values() if isinstance(c, dict)
    )


def test_smoke_is_one_initial_call_no_pilot_metrics(tmp_path: Path) -> None:
    _, report = run(tmp_path, demos()[0], stage="smoke")
    assert report["send_attempts"] == 1 and report["branches"] == []
    assert "strata" not in obj(report["summary"])
    assert obj(report["summary"])["branches_planned"] == 0


def test_maximum_114_calls_and_six_decisions(tmp_path: Path) -> None:
    _, report = run(tmp_path, LiveFixture(move("S-to-A"), ((WAIT,) * 6,) * 3))
    assert report["status"] == "INCLUDED" and report["send_attempts"] == 114
    assert obj(report["ledger"])["reserved_usd"] == "8.03472"
    assert all(
        b["stop"] == "DECISION_LIMIT" and obj(b["metrics"])["decisions"] == 6
        for b in rows(report["branches"])
    )


def test_time_budget_no_submission_and_first_failure_retained(tmp_path: Path) -> None:
    wait = '{"action_type":"WAIT","payload":{"duration":25}}'
    _, report = run(tmp_path, LiveFixture(move("S-to-A"), ((wait,),) * 3))
    for b in rows(report["branches"]):
        assert b["stop"] == "TIME_BUDGET_NOT_SUBMITTED"
        assert obj(b["metrics"])["submitted_actions"] == 0
        assert obj(b["metrics"])["first_appropriate"] is None


def test_recovery_does_not_overwrite_first_and_arrival_stops(tmp_path: Path) -> None:
    _, report = run(tmp_path)
    hidden = rows(report["branches"])[1]
    metrics = obj(hidden["metrics"])
    assert metrics["first_appropriate"] is False
    assert metrics["eventual_arrival"] is True and metrics["recovered_arrival"] is True
    assert metrics["extra_ticks"] == 2 and metrics["decisions"] == 4
    assert len(rows(hidden["steps"])) == 4


@pytest.mark.parametrize(
    "change",
    [
        "source",
        "profile_version",
        "schema_sha256",
        "mode",
        "call_cap",
        "allocation",
        "config",
        "state",
    ],
)
def test_manual_plan_edits_fail_before_transport(tmp_path: Path, change: str) -> None:
    output = tmp_path / "new"
    plan = make_plan("pilot", "recording-mock", output)
    plan[change] = "edited"
    plan["plan_sha256"] = digest({k: v for k, v in plan.items() if k != "plan_sha256"})
    with pytest.raises((ValueError, TypeError)):
        execute(plan, output, fixtures=demos())
    assert not output.exists()


def test_live_draft_and_authorization_do_not_make_READY(tmp_path: Path) -> None:
    output = tmp_path / "new"
    plan = make_plan("pilot", "live", output)
    assert plan["state"] == "DRAFT" and obj(plan["authorization"])["approved_usd"] is None
    with pytest.raises(ValueError, match="authorization missing"):
        preflight(plan, output)
    # Synthetic gate test only; not saved and never supplied to an actual transport.
    synthetic: JsonObject = {
        "plan_sha256": plan["plan_sha256"],
        "stage": "pilot",
        "call_cap": 114,
        "output_path": plan["output_path"],
        "mode": "live",
        "status": "HUMAN_APPROVED",
        "approved_usd": "8.03472",
    }
    with pytest.raises(ValueError, match="TOKEN_COUNTER_UNVERIFIED"):
        preflight(plan, output, synthetic)
    assert not output.exists()


@pytest.mark.parametrize(
    "field", ["plan_sha256", "stage", "call_cap", "output_path", "mode", "status", "approved_usd"]
)
def test_authorization_exact_scope(tmp_path: Path, field: str) -> None:
    output = tmp_path / "new"
    plan = make_plan("smoke", "live", output)
    synthetic: JsonObject = {
        "plan_sha256": plan["plan_sha256"],
        "stage": "smoke",
        "call_cap": 1,
        "output_path": plan["output_path"],
        "mode": "live",
        "status": "HUMAN_APPROVED",
        "approved_usd": "0.07048",
    }
    synthetic[field] = "wrong"
    with pytest.raises(ValueError, match="exact stage"):
        preflight(plan, output, synthetic)


def test_existing_output_and_started_claim_never_resume(tmp_path: Path) -> None:
    directory, _ = run(tmp_path, demos()[0], stage="smoke")
    plan = obj(strict_json((directory / "plan.json").read_text()))
    with pytest.raises(ValueError, match="new exact"):
        execute(plan, directory, fixtures=(demos()[0],))
    # Simulated accidental deletion of a new test output cannot remove the retained claim.
    shutil.rmtree(directory)
    with pytest.raises(FileExistsError):
        execute(plan, directory, fixtures=(demos()[0],))
    assert not directory.exists()


@pytest.mark.parametrize("field", list(CONFIG))
def test_wire_rejects_candidate_config_drift(field: str) -> None:
    config = dict(CONFIG)
    config[field] = "wrong"
    with pytest.raises(ValueError):
        wire({}, config)


def test_wire_profile_schema_and_allowlist_exact() -> None:
    session = LiveSession("wire", CONFIG, "recording-mock")
    try:
        prepared = session.prepare(initial=True)
        body = obj(obj(prepared["wire"])["body"])
        assert body["input"] == PROFILE.render(obj(prepared["semantic_input"]))
        assert obj(obj(body["text"])["format"])["schema"] == SCHEMA
        assert set(body) == {
            "model",
            "input",
            "store",
            "reasoning",
            "max_output_tokens",
            "service_tier",
            "text",
        }
        assert body["store"] is False
        assert obj(body["reasoning"])["effort"] == "low"
    finally:
        session.close()


@pytest.mark.parametrize("mode", ["live", "recording-mock"])
def test_mock_live_identity_not_interchangeable(mode: str) -> None:
    session = LiveSession("mode", CONFIG, mode)
    try:
        prepared = session.prepare(initial=True)
        transport = RecordingTransport(move("S-to-A"))
        raw = obj(
            strict_json(
                transport.send(cast(str, obj(prepared["wire"])["canonical_utf8"]).encode()).decode()
            )
        )
        response = decode(raw, "recording-mock" if mode == "live" else "live")
        assert session.finish_live(response, None, initial=True)["stop"] == "INTEGRITY"
    finally:
        session.close()


def test_ledger_before_send_and_unknown_reservation() -> None:
    ledger = Ledger("0.10", 2)
    ledger.reserve({"sha256": "abc"}, "recording-mock")
    assert ledger.snapshot()["unconfirmed_reservation_usd"] == "0.07048"
    with pytest.raises(CostStop, match="BUDGET_LIMIT_NOT_SENT"):
        ledger.reserve({"sha256": "def"}, "recording-mock")
    assert len(ledger.entries) == 1


@pytest.mark.parametrize("count", [-1, 20001, True])
def test_input_bound_before_send(tmp_path: Path, count: int) -> None:
    _, report = run(tmp_path, demos()[0], stage="smoke", mock_input_tokens=count)
    assert report["status"] == "INCLUDED" and report["send_attempts"] == 0
    assert report["stop"] == "INPUT_LIMIT_NOT_SENT"
    assert obj(report["ledger"])["entries"] == []


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {"input_tokens": True, "output_tokens": 0, "total_tokens": 1},
        {"input_tokens": 20001, "output_tokens": 0, "total_tokens": 20001},
        {"input_tokens": 1, "output_tokens": 2049, "total_tokens": 2050},
        {"input_tokens": 1, "output_tokens": 1, "total_tokens": 3},
    ],
)
def test_unsettled_invalid_usage_keeps_max_reservation(usage: JsonValue) -> None:
    ledger = Ledger("1", 1)
    ledger.reserve({"sha256": "abc"}, "recording-mock")
    with pytest.raises((ValueError, TypeError)):
        ledger.settle({"usage": usage, "service_tier": "default"})
    assert ledger.snapshot()["unconfirmed_reservation_usd"] == "0.07048"


def test_token_counter_cannot_be_forged_or_byte_divided() -> None:
    ledger = Ledger("10", 114)
    with pytest.raises(CostStop, match="TOKEN_COUNTER_UNVERIFIED"):
        ledger.reserve({"sha256": "abc", "bytes": 1}, "live")
    assert ledger.entries == []


def test_audit_has_no_provider_source_runtime_or_key_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, report = run(tmp_path, demos()[0], stage="smoke")

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("audit dependency forbidden")

    monkeypatch.setattr(planning, "source_identity", forbidden)
    monkeypatch.setattr(planning, "runtime_identity", forbidden)
    monkeypatch.setattr(RecordingTransport, "send", forbidden)
    assert audit_directory(directory) == report


@pytest.mark.parametrize("file", ["request-001.utf8", "response-001.utf8", "smoke.json"])
def test_raw_file_tamper_excluded(tmp_path: Path, file: str) -> None:
    directory, _ = run(tmp_path, demos()[0], stage="smoke")
    path = directory / file
    path.write_bytes(path.read_bytes() + b"tampered")
    assert audit_directory(directory)["status"] == "EXCLUDED"


def test_recording_failure_after_valid_first_preserves_nullable_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    append = AttemptJournal.append
    valid_results = 0

    def fail(self: AttemptJournal, kind: str, data: JsonObject) -> None:
        nonlocal valid_results
        if kind == "RESULT":
            valid_results += 1
            if valid_results == 3:
                raise OSError("injected recording failure")
        append(self, kind, data)

    monkeypatch.setattr(AttemptJournal, "append", fail)
    _, report = run(tmp_path)
    assert report["status"] == "INCOMPLETE" and report["send_attempts"] == 3
    pending = obj(report["pending_unknown"])
    assert obj(pending["metrics"])["first_appropriate"] is True
    assert obj(pending["metrics"])["eventual_arrival"] is None
    assert pending["stop"] == "UNKNOWN"


def test_no_send_if_intent_persistence_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    append = AttemptJournal.append

    def fail(self: AttemptJournal, kind: str, data: JsonObject) -> None:
        if kind == "INTENT":
            raise OSError("injected before send")
        append(self, kind, data)

    monkeypatch.setattr(AttemptJournal, "append", fail)
    _, report = run(tmp_path)
    assert report["status"] == "INCOMPLETE" and report["send_attempts"] == 0


def test_plan_digest_exact_not_manual_DRAFT_relabel(tmp_path: Path) -> None:
    plan = make_plan("pilot", "live", tmp_path / "new")
    plan["state"] = "READY"
    plan["readiness"] = "VERIFIED"
    plan["plan_sha256"] = digest({k: v for k, v in plan.items() if k != "plan_sha256"})
    with pytest.raises(ValueError):
        validate_plan(plan)


def test_initial_failure_preserves_later_allocation_ordinals(tmp_path: Path) -> None:
    directory = tmp_path / "new-run"
    plan = make_plan("pilot", "recording-mock", directory)
    fixtures = (LiveFixture("REFUSAL", ((), (), ())), *demos()[1:])
    report = execute(plan, directory, fixtures=fixtures)
    assert report["status"] == "INCLUDED"
    orders = rows(obj(report["summary"])["orders"])
    assert orders[0]["actual_order"] == []
    assert orders[1]["actual_order"] == [CONDITIONS[0], CONDITIONS[2], CONDITIONS[1]]
    assert orders[4]["actual_order"] == [CONDITIONS[2], CONDITIONS[0], CONDITIONS[1]]
    assert obj(report["summary"])["actual_balance_complete"] is False


def test_equal_wire_allows_independent_different_responses(tmp_path: Path) -> None:
    fixture = LiveFixture(
        move("S-to-A"),
        (
            (move("A-to-C"), move("C-to-G")),
            (move("A-to-D"), move("D-to-G")),
            (move("A-to-D"), move("D-to-G")),
        ),
    )
    _, report = run(tmp_path, fixture)
    branches = rows(report["branches"])
    first, hidden = (rows(b["steps"])[0] for b in branches[:2])
    assert first["wire"] == hidden["wire"]
    assert first["raw_response"] != hidden["raw_response"]
    assert obj(obj(report["summary"])["A_matched_pairs"])["both_true"] == 6


def test_token_cap_after_valid_first_keeps_score_final_unknown(tmp_path: Path) -> None:
    fixture = LiveFixture(move("S-to-A"), ((move("A-to-C"), "TOKEN_CAP"), (), ()))
    _, report = run(tmp_path, fixture)
    assert report["status"] == "INCLUDED" and report["send_attempts"] == 3
    metrics = obj(rows(report["branches"])[0]["metrics"])
    assert metrics["first_appropriate"] is True and metrics["eventual_arrival"] is None
    assert metrics["recovered_arrival"] is None


@pytest.mark.parametrize("field", ["ledger", "snapshot", "order"])
def test_resealed_journal_tamper_still_excluded(tmp_path: Path, field: str) -> None:
    directory, _ = run(tmp_path, demos()[0], stage="smoke")
    records = inspect_journal(directory / "attempts.jsonl").rows
    for row in records:
        data = obj(row["data"])
        if row["kind"] == "RESULT" and field == "ledger":
            obj(data["ledger"])["reserved_usd"] = "0"
        if row["kind"] == "RESULT" and field == "snapshot":
            obj(data["snapshot"])["stop"] = "PREFIX_UNSUCCESSFUL"
        if row["kind"] == "PLAN" and field == "order":
            data["order"] = ["researcher-inserted"]
    path = directory / "attempts.jsonl"
    path.unlink()  # Test fixture only; production never overwrites a journal.
    journal = AttemptJournal(path)
    for row in records:
        journal.append(cast(str, row["kind"]), obj(row["data"]))
    journal.close()
    assert audit_directory(directory)["status"] == "EXCLUDED"


def test_budget_call_cap_failure_before_next_reservation() -> None:
    ledger = Ledger("10", 1)
    ledger.reserve({"sha256": "first"}, "recording-mock")
    ledger.settle(
        {
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 100,
                "total_tokens": 1100,
                "input_tokens_details": {"cached_tokens": 500},
                "output_tokens_details": {"reasoning_tokens": 90},
            },
            "service_tier": "default",
        }
    )
    assert ledger.snapshot()["reserved_usd"] == "0.07048"
    assert ledger.snapshot()["confirmed_usage_estimate_usd"] == "0.0035"
    assert obj(ledger.entries[0]["usage"])["output_tokens_details"] == {"reasoning_tokens": 90}
    with pytest.raises(CostStop, match="CALL_LIMIT_NOT_SENT"):
        ledger.reserve({"sha256": "second"}, "recording-mock")
    assert len(ledger.entries) == 1
