"""Provider-free sequential prefix audit and fresh input/engine/metric reconstruction."""

from pathlib import Path
from typing import cast

from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.experiments.memory_horizon_audit import obj, require, rows
from journeymap.experiments.pickup_cue_audit import same
from journeymap.experiments.pickup_cue_live_audit import read_json, replay_result, response_fields
from journeymap.experiments.pickup_sequential_plan import (
    AUDIT_VERSION,
    EXPORT_VERSION,
    validate_plan,
)
from journeymap.experiments.pickup_sequential_session import SequentialSession
from journeymap.experiments.pilot_journal import digest, inspect_journal


def withhold_final(assessment: JsonObject, reason: str) -> None:
    """Keep verified first score/spent costs; withheld final evidence has no success cost."""
    for field in (
        "eventual_success",
        "recovered_success",
        "steps_to_success",
        "ticks_to_success",
        "extra_actions",
        "extra_ticks",
    ):
        assessment[field] = None
    assessment["stop_reason"] = reason


def audit_directory(directory: Path, *, expected_plan_sha256: str | None = None) -> JsonObject:
    reports: list[JsonValue] = []
    summary: JsonObject = {
        "audit_version": AUDIT_VERSION,
        "status": "EXCLUDED",
        "mode": None,
        "allocations": reports,
        "planned_denominator": 0,
        "attempted_denominator": 0,
        "immediate_result_denominator": 0,
        "eventual_result_denominator": 0,
        "calls_consumed": 0,
        "immediate_success_count": 0,
        "eventual_success_count": 0,
        "recovered_success_count": 0,
    }
    session: SequentialSession | None = None
    current: JsonObject | None = None
    index, consumed, cursor = 0, 0, 1
    ended, global_stop = False, "COMPLETE"
    files = {"plan.json", "attempts.jsonl"}
    try:
        plan = read_json(directory / "plan.json")
        validate_plan(plan)
        require(plan["state"] == "READY", "draft artifact")
        if expected_plan_sha256 is not None:
            same(plan["plan_sha256"], expected_plan_sha256, "external plan digest")
        allocations = rows(plan["allocations"])
        reports.extend(
            {**a, "category": "NOT_ATTEMPTED", "metrics": None, "attempted": False}
            for a in allocations
        )
        summary["planned_denominator"] = len(allocations)
        summary["mode"] = plan["mode"]
        journal = inspect_journal(directory / "attempts.jsonl")
        summary["journal_status"] = journal.status
        stream = journal.rows
        summary["calls_consumed"] = sum(r["kind"] == "INTENT" for r in stream)
        require(bool(stream) and stream[0]["kind"] == "PLAN", "plan row")
        same(
            stream[0]["data"],
            {"plan_sha256": plan["plan_sha256"], "mode": plan["mode"]},
            "mode binding",
        )
        while cursor < len(stream):
            row = stream[cursor]
            if row["kind"] == "END":
                same(row["data"], {"consumed": consumed, "stop": global_stop}, "end accounting")
                require(global_stop != "COMPLETE" or index == len(allocations), "premature end")
                require(
                    cursor == len(stream) - 1 and journal.status == "COMPLETE", "data after end"
                )
                ended = True
                break
            require(
                global_stop == "COMPLETE"
                and index < len(allocations)
                and consumed < cast(int, plan["call_cap"]),
                "case after stop/cap",
            )
            allocation = allocations[index]
            current = obj(reports[index])
            same(row["kind"], "CASE_START", "case order")
            same(row["data"], allocation, "allocation order")
            index += 1
            cursor += 1
            current["category"] = "UNKNOWN_PREPARATION"
            session = SequentialSession(
                allocation, cast(int, plan["engine_seed"]), obj(plan["config"])
            )
            if cursor == len(stream):
                break
            if stream[cursor]["kind"] == "PREPARATION_FAILED":
                failed = obj(stream[cursor]["data"])
                require(
                    set(failed) == {"allocation_id", "failure", "partial"}
                    and failed["allocation_id"] == allocation["allocation_id"]
                    and failed["failure"] == "PREPARATION_ERROR",
                    "preparation failure",
                )
                partial = obj(failed["partial"])
                same(obj(partial["case"])["target"], allocation["target"], "preparation target")
                replay_result(partial)
                current["category"] = global_stop = "INFRASTRUCTURE_PREPARATION"
                cursor += 1
                session.close()
                session = None
                continue
            require(stream[cursor]["kind"] == "PREPARED", "prepared boundary")
            same(
                stream[cursor]["data"],
                {"allocation_id": allocation["allocation_id"], "result": session.prepare()},
                "five-action preparation",
            )
            cursor += 1
            while cursor < len(stream) and stream[cursor]["kind"] == "INTENT":
                require(session.stop == "RUNNING", "call after terminal case")
                number = len(session.steps) + 1
                step = session.prepare_step()
                same(
                    stream[cursor]["data"],
                    {
                        "allocation_id": allocation["allocation_id"],
                        "step_number": number,
                        "step": step,
                    },
                    "step/input/body/cue reconstruction",
                )
                consumed += 1
                require(consumed <= cast(int, plan["call_cap"]), "total cap")
                current.update(
                    {
                        "attempted": True,
                        "category": "UNKNOWN_RESPONSE",
                        "metrics": session.snapshot()["metrics"],
                    }
                )
                cursor += 1
                if cursor == len(stream):
                    break
                same(stream[cursor]["kind"], "RESPONSE", "response boundary")
                response_row = obj(stream[cursor]["data"])
                require(response_row.get("step_number") == number, "response step order")
                require(
                    response_row.get("allocation_id") == allocation["allocation_id"],
                    "response allocation",
                )
                response, failure = response_fields(
                    {k: v for k, v in response_row.items() if k != "step_number"}
                )
                cursor += 1
                current["category"] = "UNKNOWN_EXECUTION"
                if cursor == len(stream):
                    break
                same(stream[cursor]["kind"], "STEP_RESULT", "step result boundary")
                result = session.finish_step(
                    response, failure, mock=plan["mode"] == "recording-mock"
                )
                same(
                    stream[cursor]["data"],
                    {
                        "allocation_id": allocation["allocation_id"],
                        "step_number": number,
                        "result": result,
                    },
                    "engine/receipt/step/metrics reconstruction",
                )
                replay_result(result)
                current.update(
                    {
                        "metrics": result["metrics"],
                        "category": "UNKNOWN_CASE_END",
                        "input_reconstruction": True,
                        "engine_replay": True,
                    }
                )
                cursor += 1
                if consumed == plan["call_cap"]:
                    break
            if cursor == len(stream):
                break
            if session.stop == "RUNNING":
                require(consumed == plan["call_cap"], "case stopped without terminal reason")
                session.stop = "CALL_CAP"
            same(stream[cursor]["kind"], "CASE_END", "case-end boundary")
            linkage = obj(stream[cursor]["data"])
            filename = f"{allocation['allocation_id']}.json"
            require(
                set(linkage) == {"allocation_id", "file", "record_sha256"}
                and linkage["allocation_id"] == allocation["allocation_id"]
                and linkage["file"] == filename,
                "case linkage",
            )
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
                "export seal",
            )
            same(
                {k: v for k, v in record.items() if k != "record_sha256"},
                {
                    "export_version": EXPORT_VERSION,
                    "plan_sha256": plan["plan_sha256"],
                    "mode": plan["mode"],
                    "allocation": allocation,
                    "result": session.snapshot(),
                },
                "case/summary reconstruction",
            )
            files.add(filename)
            category = session.stop
            current.update({"metrics": obj(record["result"])["metrics"], "category": category})
            if category in ("INFRASTRUCTURE", "INTEGRITY", "CALL_CAP"):
                global_stop = category
            elif consumed == plan["call_cap"] and index < len(allocations):
                global_stop = "CALL_CAP"
            session.close()
            session = None
            current = None
            cursor += 1
        require(journal.status in ("COMPLETE", "TORN_TAIL"), "corrupt journal")
        marker_path = directory / "recording-failure.json"
        if marker_path.exists():
            marker = read_json(marker_path)
            require(
                set(marker) == {"plan_sha256", "failure", "phase", "allocation_id", "step_number"}
                and marker["plan_sha256"] == plan["plan_sha256"]
                and marker["failure"] == "RECORDING_ERROR",
                "failure marker",
            )
            files.add(marker_path.name)
            summary["recording_failure"] = marker
            for item in reports:
                affected = obj(item)
                if affected["allocation_id"] == marker["allocation_id"]:
                    affected["category"] = "UNKNOWN_RECORDING"
                    if isinstance(affected["metrics"], dict):
                        withhold_final(obj(affected["metrics"]), "UNKNOWN_RECORDING")
            ended = False
        extras = {p.name for p in directory.iterdir()} - files
        orphan = f"{allocations[index - 1]['allocation_id']}.json" if index else ""
        require(not extras or (not ended and extras == {orphan}), "unexpected output files")
        summary.update(
            {
                "orphan_export": cast(list[JsonValue], sorted(extras)),
                "stop": global_stop if ended else "INTERRUPTED",
                "status": "INCLUDED" if ended else "INCOMPLETE",
            }
        )
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, OverflowError):
        summary["reason"] = "SEQUENTIAL_PLAN_JOURNAL_OR_EXPORT_INTEGRITY"
        if current is not None:
            current["category"] = "INTEGRITY"
            if isinstance(current.get("metrics"), dict):
                withhold_final(obj(current["metrics"]), "INTEGRITY")
    finally:
        if session is not None:
            session.close()
    items = [obj(r) for r in reports]
    assessments = [obj(r["metrics"]) for r in items if isinstance(r["metrics"], dict)]
    summary["attempted_denominator"] = sum(r["attempted"] is True for r in items)
    for key in ("immediate", "eventual"):
        summary[f"{key}_result_denominator"] = sum(
            type(m[f"{key}_success"]) is bool for m in assessments
        )
        summary[f"{key}_success_count"] = sum(m[f"{key}_success"] is True for m in assessments)
    summary["recovered_success_count"] = sum(m["recovered_success"] is True for m in assessments)
    for category in (
        "NOT_ATTEMPTED",
        "INFRASTRUCTURE",
        "INTEGRITY",
        "RESPONSE_FAILURE",
        "DECISION_LIMIT",
        "CALL_CAP",
    ):
        summary[category.lower()] = sum(r["category"] == category for r in items)
    summary["unknown"] = sum(cast(str, r["category"]).startswith("UNKNOWN") for r in items)
    return summary
