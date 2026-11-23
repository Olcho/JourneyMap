"""Sequential runner: durable per-step intent, no retries or reuse of earlier responses."""

from contextlib import suppress
from pathlib import Path
from typing import cast

from journeymap.adapters.llm import Record
from journeymap.adapters.pickup_cue_live import HTTPTransport, Transport
from journeymap.adapters.pickup_sequential import MockScript, RecordingTransport, SequentialProvider
from journeymap.adapters.provider import ProviderFailure
from journeymap.core.canonical import JsonObject
from journeymap.experiments.alderwick import json_value
from journeymap.experiments.memory_horizon_audit import obj, require, rows
from journeymap.experiments.pickup_sequential_plan import EXPORT_VERSION, validate_plan
from journeymap.experiments.pickup_sequential_session import SequentialSession
from journeymap.experiments.pilot_journal import AttemptJournal, digest, write_snapshot


def execute(
    plan: JsonObject,
    directory: Path,
    *,
    live: bool = False,
    enable_live: bool = False,
    approved_plan_sha256: str | None = None,
    scripts: tuple[MockScript, ...] = (),
) -> JsonObject:
    plan = Record.capture(plan).data
    validate_plan(plan, execution=True)
    require(plan["mode"] == ("live" if live else "recording-mock"), "runner/mode mismatch")
    require(not directory.exists() and directory.parent.is_dir(), "exclusive output required")
    require(
        type(scripts) is tuple and all(type(s) is MockScript for s in scripts),
        "inert scripts required",
    )
    require(not scripts or len(scripts) == plan["allocation_count"], "script allocation count")
    if live:
        require(
            enable_live and approved_plan_sha256 == plan["plan_sha256"] and not scripts,
            "explicit new approval required",
        )
    else:
        require(not enable_live and approved_plan_sha256 is None, "live flags in mock mode")
    directory.mkdir()
    journal: AttemptJournal | None = None
    session: SequentialSession | None = None
    phase, aid, number = "plan", None, 0
    consumed = 0
    recording_failed = False
    try:
        write_snapshot(directory / "plan.json", plan)
        phase = "journal"
        journal = AttemptJournal(directory / "attempts.jsonl")
        journal.append("PLAN", {"plan_sha256": plan["plan_sha256"], "mode": plan["mode"]})
        stop = "COMPLETE"
        for index, allocation in enumerate(rows(plan["allocations"])):
            if consumed == plan["call_cap"]:
                stop = "CALL_CAP"
                break
            aid, number, phase = cast(str, allocation["allocation_id"]), 0, "preparation"
            journal.append("CASE_START", allocation)
            session = SequentialSession(
                allocation, cast(int, plan["engine_seed"]), obj(plan["config"])
            )
            try:
                prepared = session.prepare()
            except Exception:
                journal.append(
                    "PREPARATION_FAILED",
                    {
                        "allocation_id": aid,
                        "failure": "PREPARATION_ERROR",
                        "partial": session.snapshot(),
                    },
                )
                stop = "INFRASTRUCTURE_PREPARATION"
                break
            journal.append("PREPARED", {"allocation_id": aid, "result": prepared})
            script = scripts[index] if scripts else MockScript()
            while session.stop == "RUNNING" and consumed < cast(int, plan["call_cap"]):
                phase = "observation"
                step = session.prepare_step()
                number = cast(int, step["step_number"])
                phase = "intent"
                journal.append(
                    "INTENT", {"allocation_id": aid, "step_number": number, "step": step}
                )
                consumed += 1
                response = None
                failure: JsonObject | None = None
                try:
                    outcome = script.outcomes[number - 1] if number <= len(script.outcomes) else ""
                    transport: Transport = (
                        HTTPTransport(obj(plan["config"])) if live else RecordingTransport(outcome)
                    )
                    response = SequentialProvider(transport, obj(plan["config"])).generate(
                        session.request
                    )
                except ProviderFailure as error:
                    failure = {"kind": error.kind, "http_status": error.http_status}
                except Exception:
                    failure = {"kind": "TRANSPORT_ERROR", "http_status": None}
                phase = "response"
                journal.append(
                    "RESPONSE",
                    {
                        "allocation_id": aid,
                        "step_number": number,
                        "raw_response": json_value(response) if response is not None else None,
                        "failure": failure,
                    },
                )
                phase = "evaluation"
                result = session.finish_step(response, failure, mock=not live)
                phase = "step-result"
                journal.append(
                    "STEP_RESULT", {"allocation_id": aid, "step_number": number, "result": result}
                )
            if session.stop == "RUNNING":
                session.stop = "CALL_CAP"
            result = session.snapshot()
            record: JsonObject = {
                "export_version": EXPORT_VERSION,
                "plan_sha256": plan["plan_sha256"],
                "mode": plan["mode"],
                "allocation": allocation,
                "result": result,
            }
            record["record_sha256"] = digest(record)
            phase = "export"
            filename = f"{aid}.json"
            write_snapshot(directory / filename, record)
            phase = "case-end"
            journal.append(
                "CASE_END",
                {"allocation_id": aid, "file": filename, "record_sha256": record["record_sha256"]},
            )
            terminal = session.stop
            session.close()
            session = None
            if terminal in ("INFRASTRUCTURE", "INTEGRITY", "CALL_CAP"):
                stop = terminal
                break
        phase = "end"
        journal.append("END", {"consumed": consumed, "stop": stop})
    except Exception:
        recording_failed = True
        with suppress(Exception):
            write_snapshot(
                directory / "recording-failure.json",
                {
                    "plan_sha256": plan["plan_sha256"],
                    "failure": "RECORDING_ERROR",
                    "phase": phase,
                    "allocation_id": aid,
                    "step_number": number,
                },
            )
    finally:
        if session is not None:
            session.close()
        if journal is not None:
            with suppress(OSError):
                journal.close()
    from journeymap.experiments.pickup_sequential_audit import audit_directory

    report = audit_directory(directory, expected_plan_sha256=cast(str, plan["plan_sha256"]))
    if recording_failed:
        report["status"] = "INCOMPLETE"
        report["execution_failure"] = "RECORDING_ERROR"
    return report
