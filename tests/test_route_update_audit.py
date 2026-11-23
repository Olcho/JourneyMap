"""E04 independent replay, response classes, durable failures and adversarial exports."""

import shutil
from pathlib import Path
from typing import cast

import pytest
import test_route_update as support
from test_route_update import WAIT, exported, forbidden

from journeymap.adapters.route_update import FakeProvider, Script, obj, rows
from journeymap.core.canonical import JsonObject, canonical_json
from journeymap.experiments import route_update as runner
from journeymap.experiments.pickup_cue_live_audit import read_json
from journeymap.experiments.pilot_journal import AttemptJournal, digest, inspect_journal
from journeymap.experiments.route_update_audit import audit_directory

demo = support.demo
offline = support.offline


@pytest.mark.parametrize(
    "output,stop,response,appropriate,eventual",
    [
        ("REFUSAL", "RESPONSE_FAILURE", "REFUSAL", None, False),
        ("INCOMPLETE", "RESPONSE_FAILURE", "INCOMPLETE", None, False),
        ("bad-json", "RESPONSE_FAILURE", "MALFORMED", None, False),
        ("INFRASTRUCTURE", "INFRASTRUCTURE", "INFRASTRUCTURE", None, None),
        ("INTEGRITY", "INTEGRITY", "INTEGRITY", None, None),
        (WAIT, "DECISION_LIMIT", "VALID", False, False),
        (runner.move("A-to-G"), "DECISION_LIMIT", "VALID", False, False),
    ],
)
def test_response_failures_have_distinct_denominators(
    tmp_path: Path,
    output: str,
    stop: str,
    response: str,
    appropriate: bool | None,
    eventual: bool | None,
) -> None:
    outputs = (output,) * (6 if response == "VALID" else 1)
    fixture = runner.Fixture(Script((runner.move("S-to-A"),)), (Script(outputs),) * 3)
    directory = tmp_path / "failure"
    report = runner.execute((fixture, runner.demo_fixtures()[1]), directory)
    assert report["status"] == "INCLUDED"
    branch = rows(report["branches"])[0]
    assert branch["status"] == stop and branch["first_response_status"] == response
    metrics = obj(branch["metrics"])
    assert metrics["first_appropriate"] is appropriate and metrics["eventual_arrival"] is eventual
    assert metrics["completion_ticks"] is None and metrics["extra_ticks"] is None
    summary = obj(report["summary"])
    if response == "INTEGRITY":
        assert summary["initial_attempts"] == 1
        assert summary["generated_branches"] == 1
        assert rows(report["prefixes"])[1]["status"] == "NOT_ATTEMPTED"
        assert [b["status"] for b in rows(report["branches"])[1:]] == ["NOT_ATTEMPTED"] * 2
        assert len(rows(summary["incomplete_pairs"])) == 1
    else:
        assert summary["initial_attempts"] == 2 and summary["generated_branches"] == 6
        assert summary["judgeable_first_action_denominator"] == (6 if response == "VALID" else 3)
        assert summary["eventual_arrivals"] == 3
        assert report["fake_provider_decision_intents"] == (26 if response == "VALID" else 11)
        if response == "INFRASTRUCTURE":
            assert summary["verified_eventual_denominator"] == 3
            assert len(rows(summary["incomplete_pairs"])) == 1


def reseal_journal(path: Path, records: list[JsonObject]) -> None:
    previous = "0" * 64
    lines: list[str] = []
    for index, row in enumerate(records, 1):
        row["sequence"], row["previous_sha256"] = index, previous
        previous = digest(row)
        lines.append(canonical_json({**row, "sha256": previous}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    "field",
    [
        "metrics",
        "receipt",
        "event",
        "position",
        "knowledge",
        "experience",
        "semantic-input",
        "wire-prompt",
        "wire-schema",
        "wire-settings",
        "authority",
        "checkpoint",
    ],
)
def test_resealed_journal_tampering_is_reconstructed_not_trusted(
    tmp_path: Path,
    demo: tuple[Path, JsonObject],
    field: str,
) -> None:
    directory = tmp_path / "tamper"
    shutil.copytree(demo[0], directory)
    records = inspect_journal(directory / "attempts.jsonl").rows
    if field.startswith("wire") or field == "semantic-input":
        record = next(
            r for r in records if r["kind"] == "INTENT" and obj(r["data"])["branch_id"] is not None
        )
        prepared = obj(obj(record["data"])["prepared"])
        if field == "semantic-input":
            obj(obj(prepared["semantic_input"])["observation"])["knowledge"] = []
        else:
            body = obj(obj(prepared["wire"])["body"])
            if field == "wire-prompt":
                body["input"] = "changed"
            elif field == "wire-schema":
                obj(obj(body["text"])["format"])["schema"] = {}
            else:
                body["model"] = "another-model"
    else:
        record = next(
            r for r in records if r["kind"] == "RESULT" and obj(r["data"])["condition"] is not None
        )
        result = obj(record["data"])
        if field == "metrics":
            obj(result["metrics"])["first_appropriate"] = False
        elif field == "receipt":
            obj(rows(result["steps"])[0]["receipt"])["resolved_at"] = 999
        elif field == "event":
            obj(rows(obj(result["engine_report"])["events"])[-1]["payload"])["destination"] = "G"
        elif field == "position":
            movement = obj(obj(obj(result["engine_report"])["final_state"])["movement"])
            obj(obj(movement["positions"])["traveller"])["location_id"] = "G"
        elif field == "knowledge":
            result["knowledge"] = []
        elif field == "experience":
            result["event_traces"] = []
        elif field == "authority":
            obj(rows(result["steps"])[0]["request"])["run_id"] = "foreign-run"
        else:
            obj(result["checkpoint"])["tick"] = 3
    reseal_journal(directory / "attempts.jsonl", records)
    assert inspect_journal(directory / "attempts.jsonl").status == "COMPLETE"
    report = audit_directory(directory)
    assert report["status"] == "EXCLUDED"
    assert report["reason"] == "E04_PLAN_JOURNAL_EXPORT_OR_RECONSTRUCTION_INTEGRITY"
    assert any(r["status"] == "INTEGRITY" for r in rows(report["branches"]))


@pytest.mark.parametrize(
    "field", ["metrics", "plan", "extra-file", "deleted-export", "torn-journal", "bad-chain"]
)
def test_export_plan_and_durable_prefix_failures(
    tmp_path: Path,
    demo: tuple[Path, JsonObject],
    field: str,
) -> None:
    directory = tmp_path / "tamper"
    shutil.copytree(demo[0], directory)
    expected = cast(str, read_json(directory / "plan.json")["plan_sha256"])
    if field == "metrics":
        path = directory / "p001-unchanged.json"
        export = read_json(path)
        obj(obj(export["result"])["metrics"])["first_appropriate"] = False
        export["record_sha256"] = digest({k: v for k, v in export.items() if k != "record_sha256"})
        path.write_text(canonical_json(export), encoding="utf-8")
    elif field == "plan":
        path = directory / "plan.json"
        plan = read_json(path)
        plan["initial_map"] = []
        plan["plan_sha256"] = digest({k: v for k, v in plan.items() if k != "plan_sha256"})
        path.write_text(canonical_json(plan), encoding="utf-8")
    elif field == "extra-file":
        (directory / "unexpected.json").write_text("{}", encoding="utf-8")
    elif field == "deleted-export":
        (directory / "p001-unchanged.json").unlink()
    else:
        path = directory / "attempts.jsonl"
        if field == "torn-journal":
            path.write_bytes(path.read_bytes()[:-5])
        else:
            path.write_bytes(path.read_bytes().replace(b'"kind":"RESULT"', b'"kind":"ALTERED"', 1))
    report = audit_directory(directory, expected_plan_sha256=expected)
    assert report["status"] == ("INCOMPLETE" if field == "torn-journal" else "EXCLUDED")


def test_first_score_survives_later_recording_failure_with_unknown_final(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = AttemptJournal.append
    branch_results = 0

    def fail_second_result(self: AttemptJournal, kind: str, data: JsonObject) -> None:
        nonlocal branch_results
        if kind == "RESULT" and data["condition"] == "unchanged":
            branch_results += 1
            if branch_results == 2:
                raise OSError("offline injected recording failure")
        original(self, kind, data)

    monkeypatch.setattr(AttemptJournal, "append", fail_second_result)
    report = runner.execute(runner.demo_fixtures(), tmp_path / "recording")
    assert report["status"] == "INCOMPLETE"
    assert report["reason"] == "E04_RECORDING_FAILURE_VALID_PREFIX_ONLY"
    first = rows(report["branches"])[0]
    assert first["status"] == "INTEGRITY_RECORDING"
    assessment = obj(first["metrics"])
    assert assessment["first_appropriate"] is True
    assert assessment["eventual_arrival"] is None and assessment["completion_ticks"] is None
    assert [r["generated"] for r in rows(report["branches"])] == [True, False, False]
    assert rows(report["prefixes"])[1]["status"] == "NOT_ATTEMPTED"
    monkeypatch.setattr(FakeProvider, "generate", forbidden)
    assert audit_directory(tmp_path / "recording") == report


def test_received_policy_does_not_infer_from_direct_witness(demo: tuple[Path, JsonObject]) -> None:
    from journeymap.adapters.route_update_policy import first_policy

    first = obj(rows(exported(demo[0], 1, "unchanged")["steps"])[0]["semantic_input"])
    observation = obj(first["observation"])
    rows_knowledge = cast(list[JsonObject], observation["knowledge"])
    rows_knowledge.append(
        {
            "source_kind": "DIRECT_OBSERVATION",
            "learned_at": 3,
            "claim": {"kind": "route-status", "route_ids": ["C-to-G", "G-to-C"], "passable": False},
        }
    )
    assert first_policy(first, "RECEIVED-UPDATE")["selected_route"] == "A-to-C"
    observation["time"] = 5
    with pytest.raises(ValueError):
        first_policy(first, "OLD-MAP")
