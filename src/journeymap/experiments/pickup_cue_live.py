"""Explicit live entry and default offline transport integration, without automatic resume."""

from contextlib import suppress
from pathlib import Path
from typing import cast

from journeymap.adapters.llm import Record
from journeymap.adapters.pickup_cue_live import (
    DEFAULT_OUTCOME,
    HTTPTransport,
    MockOutcome,
    PickupProvider,
    RecordingTransport,
    Transport,
)
from journeymap.adapters.provider import ProviderFailure
from journeymap.core.canonical import JsonObject
from journeymap.experiments.memory_horizon_audit import obj, require, rows
from journeymap.experiments.pickup_cue import seal
from journeymap.experiments.pickup_cue_live_plan import EXPORT_VERSION, validate_plan
from journeymap.experiments.pickup_cue_session import PickupSession
from journeymap.experiments.pilot_journal import AttemptJournal, write_snapshot


def preflight(
    plan: JsonObject,
    directory: Path,
    *,
    live: bool,
    enable_live: bool,
    approved_plan_sha256: str | None,
) -> None:
    """No key read, transport construction or network in this function."""
    validate_plan(plan, execution=True)
    require(plan["mode"] == ("live" if live else "recording-mock"), "runner/planned mode mismatch")
    require(not directory.exists() and directory.parent.is_dir(), "new output directory required")
    if live:
        require(enable_live, "explicit --enable-live required after human authorization")
        require(approved_plan_sha256 == plan["plan_sha256"], "approved plan digest mismatch")
    else:
        require(not enable_live and approved_plan_sha256 is None, "live flags in offline mode")


def execute(
    plan: JsonObject,
    directory: Path,
    *,
    live: bool = False,
    enable_live: bool = False,
    approved_plan_sha256: str | None = None,
    outcome: MockOutcome = DEFAULT_OUTCOME,
) -> JsonObject:
    # Freeze caller-owned data before checking it. There is no arbitrary Provider factory.
    plan = Record.capture(plan).data
    preflight(
        plan,
        directory,
        live=live,
        enable_live=enable_live,
        approved_plan_sha256=approved_plan_sha256,
    )
    require(
        type(outcome) is MockOutcome and (not live or outcome == MockOutcome()),
        "mock outcome not permitted for live execution",
    )
    directory.mkdir(exist_ok=False)
    journal: AttemptJournal | None = None
    session: PickupSession | None = None
    phase = "plan"
    current: JsonObject | None = None
    recording_failed = False
    try:
        write_snapshot(directory / "plan.json", plan)
        phase = "journal"
        journal = AttemptJournal(directory / "attempts.jsonl")
        mode = cast(str, plan["mode"])
        journal.append("PLAN", {"plan_sha256": plan["plan_sha256"], "mode": mode})
        consumed = 0
        stop = "COMPLETE"
        for allocation in rows(plan["allocations"]):
            current = allocation
            phase = "allocation"
            require(consumed < cast(int, plan["call_cap"]), "call cap reached")
            journal.append("ALLOCATION", allocation)
            try:
                session = PickupSession(
                    allocation, cast(int, plan["engine_seed"]), obj(plan["config"])
                )
                prepared = session.prepare()
            except Exception:
                journal.append(
                    "PREPARATION_FAILED",
                    {
                        "allocation_id": allocation["allocation_id"],
                        "failure": "PREPARATION_ERROR",
                        "partial": session.snapshot() if session is not None else None,
                    },
                )
                stop = "PREPARATION_ERROR"
                break
            # A durable intent is counted even if process death prevents transmission/result.
            phase = "intent"
            journal.append(
                "INTENT", {"allocation_id": allocation["allocation_id"], "prepared": prepared}
            )
            consumed += 1
            response = None
            failure: JsonObject | None = None
            try:
                transport: Transport = (
                    HTTPTransport(obj(plan["config"])) if live else RecordingTransport(outcome)
                )
                provider = PickupProvider(transport, obj(plan["config"]))
                response = provider.generate(session.request)
            except ProviderFailure as error:
                failure = {"kind": error.kind, "http_status": error.http_status}
            except Exception:
                failure = {"kind": "TRANSPORT_ERROR", "http_status": None}
            raw: JsonObject | None = (
                None
                if response is None
                else {
                    "text": response.text,
                    "metadata": response.metadata,
                    "schema_version": response.schema_version,
                }
            )
            phase = "response"
            journal.append(
                "RESPONSE",
                {
                    "allocation_id": allocation["allocation_id"],
                    "raw_response": raw,
                    "failure": failure,
                },
            )
            phase = "evaluation"
            result = session.finish(response, failure, mock=not live)
            record: JsonObject = {
                "export_version": EXPORT_VERSION,
                "plan_sha256": plan["plan_sha256"],
                "mode": mode,
                "allocation": allocation,
                "result": result,
            }
            seal(record)
            filename = f"{allocation['allocation_id']}.json"
            phase = "export"
            write_snapshot(directory / filename, record)
            phase = "result"
            journal.append(
                "RESULT",
                {
                    "allocation_id": allocation["allocation_id"],
                    "file": filename,
                    "record_sha256": record["record_sha256"],
                },
            )
            session.close()
            session = None
            if result["category"] != "BEHAVIORAL":
                stop = cast(str, result["category"])
                break
        phase = "end"
        journal.append("END", {"consumed": consumed, "stop": stop})
    except Exception:
        # Failure to persist any boundary stops the loop. Do not pretend a retry can repair it.
        # A read-only inspector reports precisely what the durable prefix actually contains.
        recording_failed = True
        # Best-effort independent marker; the failed journal itself must never be reused.
        with suppress(Exception):
            write_snapshot(
                directory / "recording-failure.json",
                {
                    "plan_sha256": plan["plan_sha256"],
                    "failure": "RECORDING_ERROR",
                    "phase": phase,
                    "allocation_id": None if current is None else current["allocation_id"],
                },
            )
    finally:
        if session is not None:
            session.close()
        if journal is not None:
            with suppress(OSError):
                journal.close()
    from journeymap.experiments.pickup_cue_live_audit import audit_directory

    report = audit_directory(directory)
    if recording_failed:
        report["status"] = "INCOMPLETE"
        report["execution_failure"] = "RECORDING_ERROR"
    return report
