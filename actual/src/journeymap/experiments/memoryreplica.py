"""Actual-owned dynamic MemoryReplica coordinator and data-only cross-process audit."""

import argparse
import json
from pathlib import Path
from typing import Any

from journeymap.adapters.memoryreplica import (
    PROTOCOL,
    RETENTION_PROTOCOL,
    Research,
    TaskApproval,
    TaskLedger,
    digest,
    episode_key,
    plan_from_binding,
)
from journeymap.adapters.memoryreplica_journal import (
    TaskJournal,
    available_plan,
    check,
    split,
    wire_check,
)
from journeymap.adapters.probe_continuation import Anchor
from journeymap.adapters.probe_diagnostics import (
    TransportDiagnostic,
    exception_diagnostic,
    exception_error,
)
from journeymap.adapters.probe_lineage import read_snapshot
from journeymap.core.canonical import canonical_json
from journeymap.experiments.paid_probe import HTTPProbeTransport, RecordingProbeTransport, WireReply


def case_input(case: dict[str, Any], frames: list[dict[str, Any]]) -> dict[str, Any]:
    if "policy" in case:
        return {"case_id": case["case_id"], "policy": case["policy"], "saved": frames}
    return {**case, "frames": frames, "max_decisions": 6, "max_ticks": 24}


def make_report(
    snapshot: dict[str, Any],
    research: Research,
    parent_source: Path,
    *,
    actual_root: Path | None = None,
) -> dict[str, Any]:
    anchor, reduced = check(snapshot, parent_source, actual_root=actual_root)
    _, run, _ = split(snapshot)
    assert run is not None
    bound = run["binding"]
    if research.data != bound["research"]:
        raise ValueError("research binding mismatch")
    outputs = []
    for index, case in enumerate(bound["cases"]):
        requests = [r for r in reduced["requests"] if episode_key(r) == episode_key(case)]
        if not requests:
            outputs.append({"case": case, "attempted": False, "result": None})
            continue
        result = research.call(case_input(case, reduced["frames"][index]))
        for request in requests:
            ordinal = request["ordinal"]
            expected = (
                result["packets"][ordinal]["wire"]
                if ordinal < len(result["packets"])
                else result["next_wire"]
            )
            if request["wire"] != expected:
                raise ValueError("saved request differs from source-bound Engine reconstruction")
        if index < len(reduced["cases"]) and digest(result) != digest(reduced["cases"][index]):
            raise ValueError("saved case differs from source-bound Engine reconstruction")
        outputs.append({"case": case, "attempted": True, "result": result})
    report = {
        "protocol": bound["protocol"],
        "authority": snapshot,
        "checkpoint": anchor.data,
        "accounting": reduced,
        "research_cases": outputs,
        "planned_cases": len(bound["cases"]),
        "request_cap": bound["plan"]["request_limit"],
        "provider_recalled": False,
        "paid_journal_verified": True,
        "current_execution_eligibility_verified": False,
        "human_approval_authenticity_verified": False,
        "lineage_total_cost_nano_usd": None,
    }
    if bound["protocol"] == RETENTION_PROTOCOL:
        # A stopped transport or budget leaves a prefix, never a scored task failure.
        report.update(
            phase=bound["phase"],
            evidence_kind=(
                "OFFLINE_ACTUAL_RECORDING" if bound["synthetic"] else "RECORDED_MODEL_RESPONSES"
            ),
            behavioral_evidence=not bound["synthetic"],
            complete_scored_episodes=sum(
                c["result"] is not None and c["result"]["score"] is not None for c in outputs
            ),
            accounting_rate_basis=bound["accounting_rate_basis"],
        )
    return report


def execute(
    path: Path,
    anchor: Anchor | None,
    approval: TaskApproval | None,
    research: Research,
    parent_source: Path,
    output: Path,
    *,
    transport: RecordingProbeTransport | HTTPProbeTransport | None = None,
) -> dict[str, Any]:
    """No bootstrap, resume, retries or approval files. HTTP requires an existing real authority."""
    if anchor is None or type(approval) is not TaskApproval:
        raise PermissionError("explicit MemoryReplica approval and latest B checkpoint required")
    if transport is not None and type(transport) not in {
        RecordingProbeTransport,
        HTTPProbeTransport,
    }:
        raise PermissionError("unreviewed task transport")
    wire = transport if transport is not None else HTTPProbeTransport()
    if wire.claimed:
        raise PermissionError("transport already consumed")
    journal = TaskJournal(
        path,
        anchor,
        approval,
        research,
        parent_source,
        output,
        synthetic=type(wire) is RecordingProbeTransport,
    )
    try:
        output.mkdir(parents=False, exist_ok=False)
    except BaseException:
        journal.close()
        raise
    return continue_task(journal, wire, output)


def continue_task(
    journal: TaskJournal,
    wire: RecordingProbeTransport | HTTPProbeTransport,
    output: Path,
    *,
    actual_root: Path | None = None,
) -> dict[str, Any]:
    plan = available_plan(
        plan_from_binding(journal.bound),
        journal.reduced.get("carried_main_reservation_nano_usd", 0),
    )
    research = journal.research
    wire.claimed = True
    try:
        stop = "ALL_CASES_COMPLETED"
        completed = len(journal.reduced["cases"])
        for index, case in enumerate(journal.bound["cases"][completed:], completed):
            frames = journal.reduced["frames"][index]
            while True:
                journal.assert_current()
                result = research.call(case_input(case, frames))
                if result["stop"] != "NEED_RESPONSE":
                    if result["stop"] == "ENGINE_OR_RECORDING_ERROR":
                        raise RuntimeError("research Engine/recording failure; no further sends")
                    journal.append("CASE", {"result": result})
                    stopped = journal.reduced["stop_reason"]
                    if stopped is not None:
                        stop = stopped
                    break
                next_wire = result["next_wire"]
                wire_check(next_wire)
                reduced = journal.reduced
                ledger = TaskLedger(**reduced["ledger"])
                blocked = ledger.blocked(plan)
                if blocked:
                    stop = blocked
                    break
                request = {
                    "request_id": f"{episode_key(case)}:{len(frames)}",
                    "case_id": case["case_id"],
                    "ordinal": len(frames),
                    "wire": next_wire,
                    "body": next_wire["body"],
                    "body_digest": digest(next_wire["body"]),
                }
                if "episode_id" in case:
                    request["episode_id"] = case["episode_id"]
                ledger.reserve(plan)
                journal.append("RESERVED", {"request": request, "ledger": ledger.data})
                journal.assert_current()
                try:
                    wire.prepare()
                except Exception as exc:
                    journal.append(
                        "NOT_SENT",
                        {
                            "reason": "CREDENTIAL_UNAVAILABLE",
                            "diagnostic": exception_diagnostic("CREDENTIAL", exc).linked(
                                journal.bound["run_id"],
                                request,
                                "NOT_SENT",
                                len(journal.entries) + 1,
                            ),
                        },
                    )
                    stop = "CREDENTIAL_UNAVAILABLE"
                    break
                journal.append(
                    "ATTEMPTED",
                    {
                        "request_id": request["request_id"],
                        "canonical_utf8": next_wire["canonical_utf8"],
                    },
                )
                try:
                    # Only the validated body crosses this boundary; world/result stays local.
                    reply = wire.send(json.loads(canonical_json(next_wire["body"])))
                except Exception as exc:
                    d = exception_diagnostic("RUNNER", exc)
                    reply = WireReply(None, None, 0, exception_error(d), d)
                diagnostic = reply.diagnostic
                if diagnostic is None and type(wire) is RecordingProbeTransport:
                    diagnostic = TransportDiagnostic("RECORDING", "NONE", "RECORDED")
                if diagnostic is None:
                    raise ValueError("missing transport diagnostic; task remains OPEN")
                packet = {
                    "request_id": request["request_id"],
                    "body_digest": request["body_digest"],
                    "raw_response": reply.raw_response,
                    "http_status": reply.http_status,
                    "elapsed_ms": reply.elapsed_ms,
                }
                journal.append(
                    "RESPONSE",
                    {
                        "packet": packet,
                        "transport_error": reply.error,
                        "diagnostic": diagnostic.linked(
                            journal.bound["run_id"], request, "RESPONSE", len(journal.entries) + 1
                        ),
                    },
                )
                reduced = journal.reduced
                outcome = reduced["packets"][-1]["outcome"]
                ledger = TaskLedger(**reduced["ledger"])
                ledger.settle(outcome)
                journal.append("SETTLED", {"result": outcome, "ledger": ledger.data})
                # The response is delivered to research only after durable settlement.
                reduced = journal.reduced
                if reduced["stop_reason"] is not None:
                    stop = reduced["stop_reason"]
                    break
                frames = reduced["frames"][index]
            if stop != "ALL_CASES_COMPLETED":
                break
        reduced = journal.reduced
        journal.append("RUN_STOPPED", {"reason": stop, "ledger": reduced["ledger"]})
    finally:
        journal.close()
        if type(wire) is HTTPProbeTransport:
            wire._key = None
    report = make_report(
        read_snapshot(journal.path), research, journal.parent_source, actual_root=actual_root
    )
    with (output / "report.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=True, indent=2) + "\n")
    return report


def audit_report(
    report: dict[str, Any],
    research: Research,
    parent_source: Path,
    *,
    actual_root: Path | None = None,
) -> dict[str, Any]:
    if report.get("protocol") not in {PROTOCOL, RETENTION_PROTOCOL}:
        raise ValueError("not a MemoryReplica report")
    rebuilt = make_report(report["authority"], research, parent_source, actual_root=actual_root)
    if digest(rebuilt) != digest(report):
        raise ValueError("paid journal/research/summary mismatch")
    return {
        "data_only_equal": True,
        "provider_recalled": False,
        "paid_journal_verified": True,
        "status": rebuilt["accounting"]["status"],
        "lineage_total_cost_nano_usd": None,
        "current_execution_eligibility_verified": False,
    }


def main() -> None:
    # There is intentionally no send or approval-generation CLI.
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--research-root", type=Path, required=True)
    parser.add_argument("--parent-source", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.input.read_text(encoding="utf-8"))
    # Runs are sorted by UUID; locate the task explicitly rather than relying on order.
    _, task, _ = split(report["authority"])
    assert task is not None
    research = Research.parse(task["binding"]["research"])
    if research.root != args.research_root.resolve():
        raise ValueError("research root differs from saved binding")
    result = audit_report(report, research, args.parent_source)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
