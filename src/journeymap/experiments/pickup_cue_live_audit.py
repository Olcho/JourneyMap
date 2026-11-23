"""Provider-free read-only plan/journal audit and independent engine/input reconstruction."""

import json
from pathlib import Path
from typing import cast

from journeymap.adapters.llm import Record
from journeymap.adapters.provider import ProviderFailure, RawModelResponse
from journeymap.bootstrap import create_pickup_cue_kernel
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.core.handlers import ActionRequest
from journeymap.core.replay import ReplayHarness, ReplayInput
from journeymap.experiments.alderwick import json_value
from journeymap.experiments.memory_horizon_audit import obj, require, rows
from journeymap.experiments.pickup_cue_audit import same
from journeymap.experiments.pickup_cue_live_plan import (
    AUDIT_VERSION,
    EXPORT_VERSION,
    LEGACY_PLAN_VERSION,
    validate_plan,
)
from journeymap.experiments.pickup_cue_session import PickupSession
from journeymap.experiments.pilot_journal import digest, inspect_journal
from journeymap.scenarios.alderwick.pickup_cue import pickup_schedule


def read_json(path: Path) -> JsonObject:
    def pairs(items: list[tuple[str, JsonValue]]) -> JsonObject:
        result: JsonObject = {}
        for key, value in items:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result

    # Malformed model text (including escaped surrogate code points) is retained as evidence.
    result = obj(json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs))
    return Record.capture(result).data


def replay_result(result: JsonObject) -> None:
    case = obj(result["case"])
    report = ReplayHarness(
        lambda: create_pickup_cue_kernel(
            run_id=f"{case['case_id']}:run",
            target=cast(str, case["target"]),
            seed=cast(int, case["seed"]),
        )
    ).run(
        ReplayInput(
            pickup_schedule(),
            tuple(ActionRequest(**r) for r in rows(result["action_requests"])),  # type: ignore[arg-type]
            cast(int, obj(result["engine_report"])["final_simulation_time"]),
        )
    )
    same(result["engine_report"], json_value(report), "independent engine replay")


def response_fields(row: JsonObject) -> tuple[RawModelResponse | None, JsonObject | None]:
    require(set(row) == {"allocation_id", "raw_response", "failure"}, "response fields")
    if row["raw_response"] is None:
        failure = obj(row["failure"])
        require(set(failure) == {"kind", "http_status"}, "failure inventory")
        status = failure["http_status"]
        require(status is None or (type(status) is int and 100 <= status <= 599), "HTTP status")
        ProviderFailure(cast(str, failure["kind"]), cast(int | None, status))
        require((failure["kind"] == "HTTP_ERROR") == (status is not None), "HTTP failure binding")
        return None, failure
    require(row["failure"] is None, "response/failure conflict")
    raw = obj(row["raw_response"])
    require(set(raw) == {"text", "metadata", "schema_version"}, "raw response fields")
    metadata = obj(raw["metadata"])
    require(
        set(metadata)
        == {
            "provider",
            "adapter_version",
            "response_id",
            "model",
            "status",
            "usage",
            "refusal",
            "incomplete_details",
        },
        "response metadata fields",
    )
    require(type(metadata["refusal"]) is bool, "refusal flag")
    return RawModelResponse(**raw), None  # type: ignore[arg-type]


def inspect_legacy(
    plan: JsonObject,
    directory: Path,
    expected_plan_sha256: str | None,
) -> JsonObject:
    """Read sealed v1 plan/journal facts only; never certify mode or behavioral evidence."""
    require(
        plan["plan_sha256"] == digest({k: v for k, v in plan.items() if k != "plan_sha256"}),
        "legacy plan digest",
    )
    if expected_plan_sha256 is not None:
        require(plan["plan_sha256"] == expected_plan_sha256, "legacy external plan digest mismatch")
    journal = inspect_journal(directory / "attempts.jsonl")
    allocations = rows(plan["allocations"])
    return {
        "audit_version": AUDIT_VERSION,
        "status": "EXCLUDED",
        "reason": "LEGACY_PLAN_MODE_UNBOUND",
        "legacy_plan_version": LEGACY_PLAN_VERSION,
        "legacy_read_only": True,
        "execution_mode_verified": False,
        "mode": None,
        "journal_status": journal.status,
        "reported_journal_mode": obj(journal.rows[0]["data"]).get("mode") if journal.rows else None,
        "calls_consumed": sum(r["kind"] == "INTENT" for r in journal.rows),
        "behavioral_denominator": 0,
        "task_success_count": 0,
        "integrity_exclusions": len(allocations),
        "allocations": [
            {**a, "category": "INTEGRITY_EXCLUSION", "task_success": None} for a in allocations
        ],
    }


def audit_directory(directory: Path, *, expected_plan_sha256: str | None = None) -> JsonObject:
    """Every frozen allocation is reported, including never-started and unknown outcomes."""
    reports: list[JsonValue] = []
    summary: JsonObject = {
        "audit_version": AUDIT_VERSION,
        "status": "EXCLUDED",
        "mode": None,
        "allocations": reports,
        "calls_consumed": 0,
        "behavioral_denominator": 0,
        "task_success_count": 0,
        "infrastructure": 0,
        "unknown": 0,
        "integrity_exclusions": 0,
        "preparation_submissions": 0,
        "evaluation_submissions": 0,
    }
    session: PickupSession | None = None
    try:
        plan = read_json(directory / "plan.json")
        if plan.get("plan_version") == LEGACY_PLAN_VERSION:
            return inspect_legacy(plan, directory, expected_plan_sha256)
        validate_plan(plan)
        require(plan["state"] == "READY", "draft execution artifact")
        if expected_plan_sha256 is not None:
            require(plan["plan_sha256"] == expected_plan_sha256, "external plan digest mismatch")
        allocations = rows(plan["allocations"])
        summary["mode"] = plan["mode"]
        reports.extend(
            {**a, "category": "NOT_ATTEMPTED", "task_success": None} for a in allocations
        )
        journal = inspect_journal(directory / "attempts.jsonl")
        summary["journal_status"] = journal.status
        require(journal.status in ("COMPLETE", "TORN_TAIL"), "corrupt journal")
        stream = journal.rows
        summary["calls_consumed"] = sum(r["kind"] == "INTENT" for r in stream)
        require(bool(stream), "missing journal plan binding")
        header = obj(stream[0]["data"])
        require(stream[0]["kind"] == "PLAN" and set(header) == {"plan_sha256", "mode"}, "plan row")
        require(header["mode"] in ("live", "recording-mock"), "execution mode")
        require(header["plan_sha256"] == plan["plan_sha256"], "plan/journal binding")
        same(header["mode"], plan["mode"], "journal/planned mode binding")
        mode = plan["mode"]
        index, consumed, cursor = 0, 0, 1
        ended = False
        stop_reason = "COMPLETE"
        files = {"plan.json", "attempts.jsonl"}
        while cursor < len(stream):
            row = stream[cursor]
            if row["kind"] == "END":
                same(row["data"], {"consumed": consumed, "stop": stop_reason}, "end accounting")
                require(
                    index == len(allocations) or stop_reason != "COMPLETE", "premature completion"
                )
                require(
                    cursor == len(stream) - 1 and journal.status == "COMPLETE", "data after end"
                )
                ended = True
                break
            require(
                stop_reason == "COMPLETE" and index < len(allocations), "call after stop/budget"
            )
            allocation = allocations[index]
            report = obj(reports[index])
            same(row["kind"], "ALLOCATION", "allocation boundary")
            same(row["data"], allocation, "allocation order/missing/duplicate")
            index += 1
            cursor += 1
            report["category"] = "UNKNOWN_PREPARATION"
            if cursor == len(stream):
                break
            row = stream[cursor]
            if row["kind"] == "PREPARATION_FAILED":
                partial = obj(row["data"])
                require(
                    set(partial) == {"allocation_id", "failure", "partial"}
                    and partial["allocation_id"] == allocation["allocation_id"]
                    and partial["failure"] == "PREPARATION_ERROR",
                    "preparation failure binding",
                )
                report["category"] = "INFRASTRUCTURE_PREPARATION"
                stop_reason = "PREPARATION_ERROR"
                cursor += 1
                continue
            require(row["kind"] == "INTENT", "intent before response")
            intent = obj(row["data"])
            require(
                set(intent) == {"allocation_id", "prepared"}
                and intent["allocation_id"] == allocation["allocation_id"],
                "intent binding",
            )
            consumed += 1
            require(consumed <= cast(int, plan["call_cap"]), "call cap")
            report["category"] = "UNKNOWN_RESPONSE"
            session = PickupSession(allocation, cast(int, plan["engine_seed"]), obj(plan["config"]))
            same(
                intent["prepared"], session.prepare(), "observation/input/full-body reconstruction"
            )
            summary["preparation_submissions"] = consumed * 5
            cursor += 1
            if cursor == len(stream):
                break
            row = stream[cursor]
            require(row["kind"] == "RESPONSE", "response boundary")
            response_row = obj(row["data"])
            require(
                response_row["allocation_id"] == allocation["allocation_id"], "response allocation"
            )
            response, failure = response_fields(response_row)
            report["category"] = "UNKNOWN_EXECUTION"
            report["response_obtained"] = response is not None
            cursor += 1
            if cursor == len(stream):
                break
            row = stream[cursor]
            require(row["kind"] == "RESULT", "result boundary")
            linkage = obj(row["data"])
            filename = f"{allocation['allocation_id']}.json"
            require(
                set(linkage) == {"allocation_id", "file", "record_sha256"}
                and linkage["file"] == filename
                and linkage["allocation_id"] == allocation["allocation_id"],
                "result linkage",
            )
            files.add(filename)
            record = read_json(directory / filename)
            require(
                set(record)
                == {
                    "export_version",
                    "plan_sha256",
                    "mode",
                    "allocation",
                    "result",
                    "record_sha256",
                },
                "export inventory",
            )
            require(
                record["record_sha256"]
                == linkage["record_sha256"]
                == digest({k: v for k, v in record.items() if k != "record_sha256"}),
                "result seal",
            )
            require(
                record["export_version"] == EXPORT_VERSION
                and record["mode"] == mode
                and record["plan_sha256"] == plan["plan_sha256"],
                "result identity",
            )
            same(record["allocation"], allocation, "result allocation")
            result = obj(record["result"])
            expected = session.finish(response, failure, mock=mode == "recording-mock")
            same(result, expected, "response evaluation/receipt/engine/input reconstruction")
            replay_result(result)
            session.close()
            session = None
            report.update(
                {
                    "category": result["category"],
                    "task_success": obj(result["evaluation"])["task_success"],
                    "engine_replay": True,
                    "input_reconstruction": True,
                }
            )
            decision = obj(result["decision"])
            summary["evaluation_submissions"] = cast(int, summary["evaluation_submissions"]) + int(
                decision["engine_submitted"] is True
            )
            if result["category"] != "BEHAVIORAL":
                stop_reason = cast(str, result["category"])
            cursor += 1
        # An export written before RESULT is an orphan, never presumed successful.
        marker_path = directory / "recording-failure.json"
        marker = read_json(marker_path) if marker_path.exists() else None
        if marker is not None:
            require(
                set(marker) == {"plan_sha256", "failure", "phase", "allocation_id"}
                and marker["plan_sha256"] == plan["plan_sha256"]
                and marker["failure"] == "RECORDING_ERROR"
                and marker["phase"]
                in (
                    "plan",
                    "journal",
                    "allocation",
                    "intent",
                    "response",
                    "evaluation",
                    "export",
                    "result",
                    "end",
                ),
                "failure marker",
            )
            files.add(marker_path.name)
            summary["recording_failure"] = marker
            for entry in reports:
                affected = obj(entry)
                if affected["allocation_id"] == marker["allocation_id"]:
                    affected.update({"category": "UNKNOWN_RECORDING", "task_success": None})
        extras = {p.name for p in directory.iterdir()} - files
        possible_orphan = f"{allocations[index - 1]['allocation_id']}.json" if index else ""
        require(
            not extras or (not ended and extras == {possible_orphan}), "unexpected output files"
        )
        summary["orphan_export"] = list(sorted(extras))
        summary["stop"] = stop_reason if ended else "INTERRUPTED"
        summary["status"] = "INCLUDED" if ended and marker is None else "INCOMPLETE"
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, OverflowError):
        summary["reason"] = "PLAN_JOURNAL_OR_EXPORT_INTEGRITY"
        # Do not hide any successfully read allocations when a later boundary is invalid.
        for entry in reports:
            report = obj(entry)
            report["category"] = "INTEGRITY_EXCLUSION"
            report["task_success"] = None
    finally:
        if session is not None:
            session.close()
    categories = [obj(r)["category"] for r in reports]
    summary["behavioral_denominator"] = categories.count("BEHAVIORAL")
    summary["task_success_count"] = sum(obj(r)["task_success"] is True for r in reports)
    summary["infrastructure"] = categories.count("INFRASTRUCTURE") + categories.count(
        "INFRASTRUCTURE_PREPARATION"
    )
    summary["unknown"] = sum(str(c).startswith("UNKNOWN_") for c in categories)
    summary["integrity_exclusions"] = categories.count("INTEGRITY_EXCLUSION")
    return summary
