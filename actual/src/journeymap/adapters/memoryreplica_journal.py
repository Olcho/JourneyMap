"""MemoryReplica protocol in the existing lineage/probe tables; no second ledger DB."""

import base64
import os
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

from journeymap.adapters.llm import strict_json
from journeymap.adapters.memoryreplica import (
    RETENTION_PROTOCOL,
    Research,
    TaskApproval,
    TaskLedger,
    TaskPlan,
    _binding,
    binding,
    digest,
    episode_key,
    parent_check,
    plan_from_binding,
    recorded_runtime,
    runtime,
    sources,
)
from journeymap.adapters.probe_continuation import Anchor, timestamp, utc_now
from journeymap.adapters.probe_diagnostics import validate_diagnostic
from journeymap.adapters.probe_execution import CONTINUE, classify, object_
from journeymap.adapters.probe_history import spec_from_bundle, verify_history
from journeymap.adapters.probe_journal import ProbeJournal
from journeymap.adapters.probe_lineage import (
    WriterLease,
    _anchor,
    _event,
    device_id,
    fresh_task_bundle,
    read_snapshot,
    snapshot_db,
)
from journeymap.core.canonical import canonical_json

ZERO = "0" * 64
RESUME = "memoryreplica-main-resume-1"
RESUME_STAGE = "RESUME_AUTHORIZED"
RESUME_ID_KIND = "memoryreplica-resume-approval"
DNS_STAGE = "PRE_SEND_DNS_FOLLOWUP_AUTHORIZED"
DNS_POLICY = "memoryreplica-main-pre-send-dns-2189"
DNS_RUN = "b5b81806-88e9-4b3d-93b3-40d45663600f"
DNS_TAIL = "29f02a164195bbea626167c817799821345336995e65dd7b1e3bd552b9c60295"
DNS_REQUEST = "main-ring-10--F:5"
DNS_WIRE = "49d28730d26aa9643a8287533e8e43b8e835260d61f7c8882e2fd1c2753fddd2"
DNS_RESERVATION = 336_864_000
DNS_ID_KIND = "memoryreplica-dns-followup-approval"
HTTP503_STAGE = "HTTP503_FOLLOWUP_AUTHORIZED"
HTTP503_POLICY = "memoryreplica-main-http503-4822"
HTTP503_TAIL = "dbf76621b049ce7c39ff0ad3579353aefb21118bfbed55e92bcdbeea986fe1ae"
HTTP503_REQUEST = "main-bridge-3--M:1"
HTTP503_WIRE = "72a90ba06c5e01e82b5bc09b8155feaefa9beb5d7afed1c55234680123bbafaa"
HTTP503_ID_KIND = "memoryreplica-http503-followup-approval"
INTERRUPTED_STAGE = "INTERRUPTED_ATTEMPT_FOLLOWUP_AUTHORIZED"
INTERRUPTED_POLICY = "memoryreplica-followup-interrupted-attempt-7366"
INTERRUPTED_RUN = "ba6f82f1-1069-494e-b025-3684a88e6255"
INTERRUPTED_SEQUENCE = 7366
INTERRUPTED_TAIL = "02e607de368b7c5d181ab69e67eb19bb8ff07c579ffba26cbe69fcfc8fa7eca2"
INTERRUPTED_ENTRY = "4856eddd6b040b9a1237c3e3783a208cd55acbb17dcdde9df1fd6dd14c25a953"
INTERRUPTED_REQUEST = "followup-20261118-ring-9--R:4"
INTERRUPTED_WIRE = "a932010cc1a1d53fc12b20174f5f4a3498ac55330a8b6c671bb7933b6312a7ac"
INTERRUPTED_ID_KIND = "memoryreplica-interrupted-attempt-approval"
FOLLOWUP_STAGES = {
    RESUME_STAGE: RESUME_ID_KIND,
    DNS_STAGE: DNS_ID_KIND,
    HTTP503_STAGE: HTTP503_ID_KIND,
    INTERRUPTED_STAGE: INTERRUPTED_ID_KIND,
}


def available_plan(plan: TaskPlan, carried: int) -> TaskPlan:
    return replace(plan, cost_limit_nano_usd=plan.cost_limit_nano_usd - carried)


def wire_check(wire: dict[str, Any]) -> None:
    encoded = canonical_json(wire["body"])
    import hashlib

    if (
        encoded != wire["canonical_utf8"]
        or hashlib.sha256(encoded.encode()).hexdigest() != wire["sha256"]
        or len(encoded.encode()) != wire["request_bytes"]
    ):
        raise ValueError("wire/body bytes mismatch")


def reduce(
    bound: dict[str, Any],
    entries: list[dict[str, Any]],
    checkpoints: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    plan = plan_from_binding(bound)
    retention = bound["protocol"] == RETENTION_PROTOCOL
    if retention and (
        len(bound["cases"]) * 7 != plan.request_limit
        or len({episode_key(c) for c in bound["cases"]}) != len(bound["cases"])
    ):
        raise ValueError("invalid fixed retention allocation")
    ledger, seen = TaskLedger(), set[str]()
    phase, previous, reason = "NEW", ZERO, None
    current: dict[str, Any] | None = None
    packet: dict[str, Any] | None = None
    outcome: dict[str, Any] | None = None
    packets: list[dict[str, Any]] = []
    frames: list[list[dict[str, Any]]] = [[] for _ in bound["cases"]]
    cases: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    counts = [0] * len(frames)
    resume: dict[str, Any] | None = None
    dns_followup: dict[str, Any] | None = None
    http503_followup: dict[str, Any] | None = None
    interrupted_followup: dict[str, Any] | None = None
    carried = 0
    for ordinal, entry in enumerate(entries, 1):
        if set(entry) != {"ordinal", "run_id", "previous", "stage", "data"} or (
            type(entry["ordinal"]) is not int
            or entry["ordinal"] != ordinal
            or entry["run_id"] != bound["run_id"]
            or entry["previous"] != previous
        ):
            raise ValueError("MemoryReplica journal sequence mismatch")
        stage, data = entry["stage"], entry["data"]
        if bound.get("independent_authority") and stage in FOLLOWUP_STAGES:
            raise PermissionError("independent batches cannot recover or retry")
        if stage == "RUN_CLAIMED" and phase == "NEW":
            if set(data) != {"binding"} or data["binding"] != bound:
                raise ValueError("task claim mismatch")
            phase = "IDLE"
        elif stage == RESUME_STAGE and phase == "IDLE" and reason is None:
            if (
                resume is not None
                or not retention
                or bound["phase"] != "main"
                or bound["synthetic"]
                or entries[ordinal - 2]["stage"] != "SETTLED"
                or ledger.reserved_cost_nano_usd
                or len(cases) >= len(frames)
                or not 1 < counts[len(cases)] < 7
                or ledger.blocked(plan) is not None
                or set(data) != {"approval", "authorized_at"}
            ):
                raise ValueError("recovery requires a settled partial main episode")
            resume = parse_resume(data, bound, previous, len(cases), counts[len(cases)], ledger)
        elif stage == DNS_STAGE:
            if (
                dns_followup is not None
                or resume is None
                or phase != "DONE"
                or reason != "TRANSPORT_UNCERTAIN"
                or entries[ordinal - 2]["stage"] != "RUN_STOPPED"
                or len(cases) != 52
                or counts[52] != 6
                or len(frames[52]) != 5
                or set(data) != {"approval", "authorized_at"}
            ):
                raise ValueError("DNS follow-up requires the specific terminal partial episode")
            dns_followup = parse_dns_followup(data, bound, previous, ledger, packets[-1])
            carried = ledger.reserved_cost_nano_usd
            ledger = replace(ledger, reserved_cost_nano_usd=0)
            counts[52] = len(frames[52])
            plan = available_plan(plan, carried)
            reason, phase = None, "IDLE"
        elif stage == HTTP503_STAGE:
            if (
                http503_followup is not None
                or dns_followup is None
                or phase != "DONE"
                or reason != "INVALID_RESPONSE"
                or entries[ordinal - 2]["stage"] != "RUN_STOPPED"
                or len(cases) != 215
                or counts[215] != 2
                or len(frames[215]) != 1
                or set(data) != {"approval", "authorized_at"}
            ):
                raise ValueError(
                    "HTTP 503 follow-up requires the specific terminal partial episode"
                )
            http503_followup = parse_terminal_followup(
                data,
                http503_prefix(bound, previous, ledger, packets[-1], carried),
                4822,
                HTTP503_TAIL,
            )
            carried += ledger.reserved_cost_nano_usd
            ledger = replace(ledger, reserved_cost_nano_usd=0)
            counts[215] = len(frames[215])
            plan = available_plan(plan_from_binding(bound), carried)
            reason, phase = None, "IDLE"
        elif stage == INTERRUPTED_STAGE:
            if (
                interrupted_followup is not None
                or phase != "ATTEMPTED"
                or current is None
                or len(cases) != 45
                or len(packets) != 223
                or counts[45] != 5
                or len(frames[45]) != 4
                or set(data) != {"approval", "authorized_at"}
            ):
                raise ValueError("interrupted recovery requires the original outstanding attempt")
            interrupted_followup = parse_terminal_followup(
                data,
                interrupted_prefix(bound, previous, ledger, current),
                INTERRUPTED_SEQUENCE,
                INTERRUPTED_TAIL,
            )
            carried = ledger.reserved_cost_nano_usd
            ledger = replace(ledger, reserved_cost_nano_usd=0)
            counts[45] = len(frames[45])
            plan = available_plan(plan, carried)
            phase = "IDLE"
        elif stage == "RESERVED" and phase == "IDLE" and reason is None:
            if set(data) != {"request", "ledger"} or len(cases) >= len(frames):
                raise ValueError("invalid dynamic reservation")
            current = data["request"]
            i = len(cases)
            case = bound["cases"][i]
            keys = {"request_id", "case_id", "ordinal", "wire", "body", "body_digest"}
            if retention:
                keys.add("episode_id")
            if set(current) != keys:
                raise ValueError("invalid task request")
            wire_check(current["wire"])
            if (
                type(current["ordinal"]) is not int
                or current["ordinal"] != counts[i]
                or counts[i] >= 7
                or current["case_id"] != case["case_id"]
                or current["request_id"] != f"{episode_key(case)}:{counts[i]}"
                or (retention and current["episode_id"] != case["episode_id"])
                or current["body"] != current["wire"]["body"]
                or current["body_digest"] != digest(current["body"])
                or current["wire"]["kind"] != ("reconstruction" if counts[i] == 0 else "action")
            ):
                raise ValueError("dynamic case/request order mismatch")
            prior_stage = entries[ordinal - 2]["stage"]
            if prior_stage in FOLLOWUP_STAGES:
                recovery = {
                    RESUME_STAGE: resume,
                    DNS_STAGE: dns_followup,
                    HTTP503_STAGE: http503_followup,
                    INTERRUPTED_STAGE: interrupted_followup,
                }[prior_stage]
                if (
                    recovery is None
                    or current["request_id"] != recovery["next_request_id"]
                    or current["wire"]["sha256"] != recovery["next_wire_sha256"]
                ):
                    raise ValueError("first recovery request differs from approved wire")
            requests.append(current)
            ledger.reserve(plan)
            counts[i] += 1
            if digest(data["ledger"]) != digest(ledger.data):
                raise ValueError("reservation ledger mismatch")
            phase = "RESERVED"
        elif stage == "NOT_SENT" and phase == "RESERVED":
            assert current is not None
            if set(data) != {"reason", "diagnostic"} or data["reason"] != "CREDENTIAL_UNAVAILABLE":
                raise ValueError("invalid credential failure")
            validate_diagnostic(
                data["diagnostic"],
                run_id=bound["run_id"],
                request=current,
                event="NOT_SENT",
                ordinal=ordinal,
                error=data["reason"],
            )
            ledger.reserved_cost_nano_usd = 0
            reason, phase = "CREDENTIAL_UNAVAILABLE", "IDLE"
        elif stage == "ATTEMPTED" and phase == "RESERVED":
            assert current is not None
            if data != {
                "request_id": current["request_id"],
                "canonical_utf8": current["wire"]["canonical_utf8"],
            }:
                raise ValueError("actual send bytes mismatch")
            phase = "ATTEMPTED"
        elif stage == "RESPONSE" and phase == "ATTEMPTED":
            assert current is not None
            if set(data) != {"packet", "transport_error", "diagnostic"}:
                raise ValueError("invalid response journal")
            packet = data["packet"]
            if set(packet) != {
                "request_id",
                "body_digest",
                "raw_response",
                "http_status",
                "elapsed_ms",
            }:
                raise ValueError("invalid response packet")
            if any(packet[k] != current[k] for k in ("request_id", "body_digest")):
                raise ValueError("response/request mismatch")
            validate_diagnostic(
                data["diagnostic"],
                run_id=bound["run_id"],
                request=current,
                event="RESPONSE",
                ordinal=ordinal,
                error=data["transport_error"],
                status=packet["http_status"],
                raw=packet["raw_response"],
                elapsed=packet["elapsed_ms"],
                synthetic=bound["synthetic"],
            )
            outcome = classify(packet, seen)
            if data["transport_error"] is not None or not data["diagnostic"]["framing_complete"]:
                outcome = {
                    **outcome,
                    "classification": data["transport_error"] or "TRANSPORT_UNCERTAIN",
                    "cost_nano_usd": None,
                }
            packets.append({"request": current, **data, "outcome": outcome})
            phase = "RECEIVED"
        elif stage == "SETTLED" and phase == "RECEIVED":
            assert current is not None and outcome is not None and packet is not None
            ledger.settle(outcome)
            if digest(data) != digest({"result": outcome, "ledger": ledger.data}):
                raise ValueError("settlement differs from raw response")
            if outcome["classification"] not in CONTINUE:
                reason = outcome["classification"]
            else:
                raw = packet["raw_response"]
                frame = {
                    "body_base64": base64.b64encode(raw.encode()).decode(),
                    "http_status": packet["http_status"],
                    "complete": True,
                    "elapsed_ms": packet["elapsed_ms"],
                }
                frames[len(cases)].append(
                    {"wire_sha256": current["wire"]["sha256"], "frame": frame}
                    if retention
                    else frame
                )
            phase = "IDLE"
        elif stage == "CASE" and phase == "IDLE" and reason is None:
            if (
                entries[ordinal - 2]["stage"] in FOLLOWUP_STAGES
                or set(data) != {"result"}
                or len(cases) >= len(frames)
            ):
                raise ValueError("invalid case completion")
            if counts[len(cases)] == 0 or not frames[len(cases)]:
                raise ValueError("case completion requires settled request/response evidence")
            result = data["result"]
            case = bound["cases"][len(cases)]
            if (
                result["case_id"] != case["case_id"]
                or result["policy" if retention else "memory_policy"]
                != case["policy" if retention else "memory_policy"]
                or result["saved" if retention else "frames"] != frames[len(cases)]
                or (
                    retention
                    and (
                        result["episode_id"] != case["episode_id"]
                        or result["input_case_sha256"] != case["input_case_sha256"]
                    )
                )
                or result["stop"] == "NEED_RESPONSE"
            ):
                raise ValueError("case/frame mismatch")
            cases.append(result)
            if result["stop"] not in {
                "ARRIVED",
                "DECISION_LIMIT",
                "TIME_LIMIT",
                "TIME_BUDGET_NOT_SUBMITTED",
                "INVALID_ACTION_OUTPUT",
                "ACTION_INCOMPLETE",
                "ACTION_REFUSAL",
                "MODEL_INCOMPLETE",
                "MODEL_REFUSAL",
            }:
                reason = result["stop"]
        elif stage == "RUN_STOPPED" and phase == "IDLE":
            expected = reason or (
                "ALL_CASES_COMPLETED" if len(cases) == len(frames) else ledger.blocked(plan)
            )
            if expected is None or digest(data) != digest(
                {"reason": expected, "ledger": ledger.data}
            ):
                raise ValueError("invalid task termination")
            reason, phase = expected, "DONE"
        else:
            raise ValueError("invalid MemoryReplica journal transition")
        previous = digest(entry)
        if checkpoints is not None:
            checkpoints.append(
                {
                    "status": (
                        "UNCERTAIN"
                        if phase == "DONE" and (ledger.reserved_cost_nano_usd or carried)
                        else "FINISHED"
                        if phase == "DONE"
                        else "OPEN"
                    ),
                    "exposure": bound["prior_known_nano_usd"]
                    + bound["prior_unresolved_nano_usd"]
                    + ledger.known_cost_nano_usd
                    + ledger.reserved_cost_nano_usd
                    + carried,
                }
            )
    status = (
        "UNCERTAIN"
        if phase == "DONE" and (ledger.reserved_cost_nano_usd or carried)
        else ("FINISHED" if phase == "DONE" else "OPEN")
    )
    result = {
        "phase": phase,
        "status": status,
        "stop_reason": reason,
        "ledger": ledger.data,
        "packets": packets,
        "requests": requests,
        "cases": cases,
        "frames": frames,
        "snapshot": {"entry_count": len(entries), "tail_digest": previous, "status": status},
        "lineage_total_cost_nano_usd": None,
        "conditional_exposure_nano_usd": bound["prior_known_nano_usd"]
        + bound["prior_unresolved_nano_usd"]
        + ledger.known_cost_nano_usd
        + ledger.reserved_cost_nano_usd
        + carried,
    }
    if dns_followup is not None or interrupted_followup is not None:
        result["carried_main_reservation_nano_usd"] = carried
    return result


def resume_prefix(
    bound: dict[str, Any], previous: str, case_index: int, count: int, ledger: TaskLedger
) -> dict[str, Any]:
    plan = plan_from_binding(bound)
    return {
        "schema": RESUME,
        "run_id": bound["run_id"],
        "original_binding_digest": digest(bound),
        "risk_acceptance_id": bound["risk_acceptance_id"],
        "previous_entry_digest": previous,
        "completed_episodes": case_index,
        "next_request_id": f"{episode_key(bound['cases'][case_index])}:{count}",
        "settled_ledger": ledger.data,
        "request_limit": plan.request_limit,
        "cost_limit_nano_usd": plan.cost_limit_nano_usd,
        "prior_unresolved_nano_usd": bound["prior_unresolved_nano_usd"],
    }


def parse_resume(
    data: dict[str, Any],
    bound: dict[str, Any],
    previous: str,
    case_index: int,
    count: int,
    ledger: TaskLedger,
) -> dict[str, Any]:
    approval = TaskApproval.parse(data["approval"])
    recovery = approval.bound
    prefix = resume_prefix(bound, previous, case_index, count, ledger)
    if (
        set(recovery)
        != {
            *prefix,
            "anchor",
            "snapshot_digest",
            "paid_approval_id",
            "frozen_main_root",
            "actual_source",
            "runtime",
            "next_wire_sha256",
            "launcher_sha256",
        }
        or any(recovery[k] != v for k, v in prefix.items())
        or approval.approval_id
        in (bound["run_id"], bound["paid_approval_id"], bound["risk_acceptance_id"])
    ):
        raise PermissionError("recovery approval does not describe this stopped episode")
    approval.validate(recovery, synthetic=False, now=data["authorized_at"])
    return recovery


def dns_prefix(
    bound: dict[str, Any], previous: str, ledger: TaskLedger, failed: dict[str, Any]
) -> dict[str, Any]:
    diagnostic = failed["diagnostic"]
    packet = failed["packet"]
    request = failed["request"]
    plan = plan_from_binding(bound)
    if (
        bound["run_id"] != DNS_RUN
        or bound["synthetic"]
        or bound["phase"] != "main"
        or plan.request_limit != 2100
        or plan.cost_limit_nano_usd != 15_000_000_000
        or bound["prior_unresolved_nano_usd"] != DNS_RESERVATION
        or ledger.attempts != 283
        or ledger.known_cost_nano_usd != 1_653_647_250
        or ledger.reserved_cost_nano_usd != DNS_RESERVATION
        or request["request_id"] != DNS_REQUEST
        or request["wire"]["sha256"] != DNS_WIRE
        or failed["transport_error"] != "TRANSPORT_UNCERTAIN"
        or failed["outcome"]["classification"] != "TRANSPORT_UNCERTAIN"
        or (diagnostic["stage"], diagnostic["category"], diagnostic["disposition"])
        != ("REQUEST", "DNS", "EXCEPTION")
        or packet["http_status"] is not None
        or packet["raw_response"] is not None
    ):
        raise PermissionError("journal does not contain the authorized pre-send DNS failure")
    return {
        **resume_prefix(bound, previous, 52, 5, ledger),
        "schema": DNS_POLICY,
        "next_wire_sha256": DNS_WIRE,
        "failed_attempt": 283,
        "next_attempt": 284,
        "carry_main_reservation_nano_usd": DNS_RESERVATION,
        "additional_request_limit": 1817,
        "additional_cost_limit_nano_usd": 13_009_488_750,
    }


def parse_dns_followup(
    data: dict[str, Any],
    bound: dict[str, Any],
    previous: str,
    ledger: TaskLedger,
    failed: dict[str, Any],
) -> dict[str, Any]:
    return parse_terminal_followup(
        data, dns_prefix(bound, previous, ledger, failed), 2189, DNS_TAIL
    )


def http503_prefix(
    bound: dict[str, Any], previous: str, ledger: TaskLedger, failed: dict[str, Any], carried: int
) -> dict[str, Any]:
    request = failed["request"]
    packet = failed["packet"]
    outcome = failed["outcome"]
    diagnostic = failed["diagnostic"]
    plan = plan_from_binding(bound)
    if (
        bound["run_id"] != DNS_RUN
        or bound["synthetic"]
        or bound["phase"] != "main"
        or plan.request_limit != 2100
        or plan.cost_limit_nano_usd != 15_000_000_000
        or bound["prior_unresolved_nano_usd"] != DNS_RESERVATION
        or ledger.attempts != 900
        or ledger.known_cost_nano_usd != 4_182_413_850
        or ledger.reserved_cost_nano_usd != DNS_RESERVATION
        or carried != DNS_RESERVATION
        or request["request_id"] != HTTP503_REQUEST
        or request["wire"]["sha256"] != HTTP503_WIRE
        or packet["http_status"] != 503
        or not diagnostic["framing_complete"]
        or outcome["classification"] != "INVALID_RESPONSE"
        or outcome["cost_nano_usd"] is not None
        or outcome["usage"] is not None
    ):
        raise PermissionError("journal does not contain the authorized HTTP 503 failure")
    return {
        **resume_prefix(bound, previous, 215, 1, ledger),
        "schema": HTTP503_POLICY,
        "next_wire_sha256": HTTP503_WIRE,
        "failed_attempt": 900,
        "next_attempt": 901,
        "carry_main_reservation_nano_usd": carried + ledger.reserved_cost_nano_usd,
        "additional_request_limit": 1200,
        "additional_cost_limit_nano_usd": 10_143_858_150,
    }


def interrupted_prefix(
    bound: dict[str, Any], previous: str, ledger: TaskLedger, request: dict[str, Any]
) -> dict[str, Any]:
    plan = plan_from_binding(bound)
    if (
        bound["run_id"] != INTERRUPTED_RUN
        or bound["synthetic"]
        or previous != INTERRUPTED_ENTRY
        or ledger.attempts != 224
        or ledger.reserved_cost_nano_usd != DNS_RESERVATION
        or request["request_id"] != INTERRUPTED_REQUEST
        or request["wire"]["sha256"] != INTERRUPTED_WIRE
        or request["body_digest"] != INTERRUPTED_WIRE
    ):
        raise PermissionError("interrupted request differs from sequence 7366")
    return {
        "schema": INTERRUPTED_POLICY,
        "run_id": bound["run_id"],
        "original_binding_digest": digest(bound),
        "risk_acceptance_id": bound["risk_acceptance_id"],
        "previous_entry_digest": previous,
        "completed_episodes": 45,
        "settled_responses": 223,
        "original_attempt_number": ledger.attempts,
        "next_attempt_number": ledger.attempts + 1,
        "next_request_id": request["request_id"],
        "next_wire_sha256": request["wire"]["sha256"],
        "ledger_before_recovery": ledger.data,
        "carry_main_reservation_nano_usd": ledger.reserved_cost_nano_usd,
        "request_limit": plan.request_limit,
        "cost_limit_nano_usd": plan.cost_limit_nano_usd,
        "additional_request_limit": plan.request_limit - ledger.attempts,
        "additional_cost_limit_nano_usd": (
            plan.cost_limit_nano_usd - ledger.known_cost_nano_usd - ledger.reserved_cost_nano_usd
        ),
        "identical_resend_limit": 1,
        "automatic_retries": plan.retries,
        "require_system_awake_while_running": True,
    }


def parse_terminal_followup(
    data: dict[str, Any], prefix: dict[str, Any], sequence: int, tail: str
) -> dict[str, Any]:
    approval = TaskApproval.parse(data["approval"])
    recovery = approval.bound
    if (
        set(recovery)
        != {
            *prefix,
            "anchor",
            "snapshot_digest",
            "paid_approval_id",
            "frozen_main_root",
            "actual_source",
            "runtime",
            "launcher_sha256",
            "output",
            "prior_recovery_binding_digest",
        }
        or any(recovery[k] != v for k, v in prefix.items())
        or Anchor.parse(recovery["anchor"]).sequence != sequence
        or Anchor.parse(recovery["anchor"]).tail != tail
    ):
        raise PermissionError("terminal follow-up approval differs from the authorized scope")
    approval.validate(recovery, synthetic=False, now=data["authorized_at"])
    return recovery


def split(
    snapshot: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
    if set(snapshot) != {"meta", "events", "consumed", "runs"}:
        raise ValueError("invalid authority snapshot")
    events = snapshot["events"]
    starts = [i for i, e in enumerate(events) if e["event"]["kind"] == "MEMORYREPLICA_CLAIMED"]
    if not starts:
        return snapshot, None, []
    offset = starts[-1]
    claimed = events[offset]["event"]["data"]["binding"]["run_id"]
    matches = [r for r in snapshot["runs"] if r["run_id"] == claimed]
    if len(matches) != 1:
        raise ValueError("missing MemoryReplica run")
    parent = {
        **snapshot,
        "events": events[:offset],
        "runs": [r for r in snapshot["runs"] if r["run_id"] != claimed],
        "consumed": [c for c in snapshot["consumed"] if c["event_seq"] <= offset],
    }
    return parent, matches[0], events[offset:]


def check(
    snapshot: dict[str, Any], parent_source: Path, *, actual_root: Path | None = None
) -> tuple[Anchor, dict[str, Any]]:
    """Replay a saved journal without treating it as permission to execute today."""
    parent, run, events = split(snapshot)
    verified = parent_check(parent, parent_source)
    if run is None:
        raise ValueError("no MemoryReplica claim")
    claim = events[0]["event"]["data"]
    if set(claim) != {"binding", "approval", "claimed_at", "fence"}:
        raise ValueError("invalid task authority claim")
    bound = claim["binding"]
    if (
        bound != run["binding"]
        or bound["parent_digest"] != digest(parent)
        or bound["authority_checkpoint"] != verified["anchor"]
        or run["corpus"] != {"protocol": bound["protocol"]}
    ):
        raise ValueError("latest B/task binding mismatch")
    expected = _binding(
        Path(bound["authority_checkpoint"]["path"]),
        Path(bound["output"]),
        Anchor.parse(verified["anchor"]),
        parent,
        Research.parse(bound["research"]),
        parent_source,
        bound["run_id"],
        bound["paid_approval_id"],
        bound["risk_acceptance_id"],
        plan_from_binding(bound),
        actual_root=actual_root or Path(__file__).resolve().parents[3],
        execution_runtime=recorded_runtime(bound["runtime"]),
    )
    expected["synthetic"] = bound["synthetic"]
    if type(bound["synthetic"]) is not bool or digest(bound) != digest(expected):
        raise ValueError("task source/plan/schema binding mismatch")
    if not bound["synthetic"] and parent["meta"]["mode"] != "operator-asserted":
        raise PermissionError("synthetic authority cannot authorize HTTP")
    approval = TaskApproval.parse(claim["approval"])
    approval.validate(bound, synthetic=bound["synthetic"], now=claim["claimed_at"])
    if claim["fence"] != digest(approval.data):
        raise ValueError("task attempt fence mismatch")
    ids = (bound["run_id"], approval.approval_id, approval.risk_acceptance_id)
    if len(set(ids)) != 3 or set(ids) & {c["id"] for c in parent["consumed"]}:
        raise ValueError("task consumed ID reused")
    consumed = [
        *parent["consumed"],
        *[
            {"id": value, "kind": role, "event_seq": len(parent["events"]) + 1}
            for value, role in zip(
                ids,
                ("memoryreplica-run", "memoryreplica-approval", "memoryreplica-risk"),
                strict=True,
            )
        ],
    ]
    previous = verified["anchor"]["tail"]
    entries = run["entries"]
    checkpoints: list[dict[str, Any]] = []
    reduced = reduce(bound, entries, checkpoints)
    if len(events) != len(entries) + 1:
        raise ValueError("unlinked task journal entries")
    prior_recovery_digest = None
    for i, wrapper in enumerate(events):
        event = wrapper["event"]
        if (
            set(wrapper) != {"event", "digest"}
            or set(event) != {"sequence", "previous", "kind", "data"}
            or type(event["sequence"]) is not int
            or event["sequence"] != len(parent["events"]) + i + 1
            or event["previous"] != previous
            or digest(event) != wrapper["digest"]
        ):
            raise ValueError("task authority chain mismatch")
        if i and (
            event["kind"] != "MEMORYREPLICA_COMMITTED"
            or event["data"]
            != {
                "run_id": run["run_id"],
                "ordinal": i,
                "entry_digest": digest(entries[i - 1]),
                "status": checkpoints[i - 1]["status"],
                "exposure": checkpoints[i - 1]["exposure"],
            }
        ):
            raise ValueError("task journal/authority mismatch")
        if i and entries[i - 1]["stage"] in FOLLOWUP_STAGES:
            stage = entries[i - 1]["stage"]
            is_terminal = stage in (DNS_STAGE, HTTP503_STAGE)
            data = entries[i - 1]["data"]
            recovery = TaskApproval.parse(data["approval"])
            stopped = {
                **snapshot,
                "events": snapshot["events"][: event["sequence"] - 1],
                "runs": [
                    {
                        **row,
                        "entries": entries[: i - 1],
                        "n": i - 1,
                        "status": "UNCERTAIN" if is_terminal else "OPEN",
                        "tail": entries[i - 1]["previous"],
                    }
                    if row["run_id"] == run["run_id"]
                    else row
                    for row in snapshot["runs"]
                ],
                "consumed": sorted(consumed, key=lambda row: row["id"]),
            }
            if (
                recovery.bound["anchor"]
                != {**verified["anchor"], "sequence": event["sequence"] - 1, "tail": previous}
                or recovery.bound["snapshot_digest"] != digest(stopped)
                or recovery.approval_id in {row["id"] for row in consumed}
                or (
                    stage != INTERRUPTED_STAGE
                    and timestamp(recovery.expires_at) > timestamp(approval.expires_at)
                )
                or timestamp(data["authorized_at"]) < timestamp(claim["claimed_at"])
                or (
                    stage != RESUME_STAGE
                    and recovery.bound["prior_recovery_binding_digest"] != prior_recovery_digest
                )
            ):
                raise PermissionError("recovery authority differs from stopped checkpoint")
            recorded_runtime(recovery.bound["runtime"])
            prior_recovery_digest = digest(recovery.bound)
            consumed.append(
                {
                    "id": recovery.approval_id,
                    "kind": FOLLOWUP_STAGES[stage],
                    "event_seq": event["sequence"],
                }
            )
        previous = wrapper["digest"]
    if sorted(consumed, key=lambda row: row["id"]) != snapshot["consumed"] or (
        run["approval_id"] != approval.approval_id
    ):
        raise ValueError("task consumed index mismatch")
    snap = reduced["snapshot"]
    if (run["status"], run["n"], run["tail"]) != (
        snap["status"],
        snap["entry_count"],
        snap["tail_digest"],
    ):
        raise ValueError("task snapshot differs from replay")
    return Anchor.parse(
        {**verified["anchor"], "sequence": len(snapshot["events"]), "tail": previous}
    ), reduced


def check_attempt_fences(path: Path, parent: dict[str, Any]) -> None:
    old_fences = list(path.parent.glob(path.name + ".attempt-*"))
    if fresh_task_bundle(parent) is None:
        old_claim = parent["events"][1]["event"]["data"]
        expected_fence = Path(str(path) + ".attempt-" + old_claim["approval"]["risk_acceptance_id"])
        if (
            old_fences != [expected_fence]
            or digest(strict_json(expected_fence.read_text(encoding="utf-8"))) != old_claim["fence"]
        ):
            raise PermissionError("missing B fence or unrecognized prior attempt")
    elif old_fences:
        raise PermissionError("independent authority contains an unrelated attempt fence")
    claims = [
        wrapper["event"]["data"]
        for wrapper in parent["events"]
        if wrapper["event"]["kind"] == "MEMORYREPLICA_CLAIMED"
    ]
    expected_fences = {
        str(path) + ".memoryreplica-attempt-" + claim["approval"]["risk_acceptance_id"]: claim[
            "fence"
        ]
        for claim in claims
    }
    actual_fences = {
        str(fence): digest(strict_json(fence.read_text(encoding="utf-8")))
        for fence in path.parent.glob(path.name + ".memoryreplica-attempt-*")
    }
    if actual_fences != expected_fences:
        raise PermissionError("missing historical task fence or uncommitted attempt")


class TaskJournal(ProbeJournal):
    bound: dict[str, Any]
    resume_approval: TaskApproval | None = None
    """Extends the same SQLite tables and writer lease after the completed B successor."""

    def __init__(
        self,
        path: Path,
        anchor: Anchor,
        approval: TaskApproval,
        research: Research,
        parent_source: Path,
        output: Path,
        *,
        synthetic: bool,
    ) -> None:
        if type(approval) is not TaskApproval:
            raise PermissionError("task-specific approval required")
        self.path = path.resolve()
        snapshot = read_snapshot(self.path)
        # The entire current snapshot is the parent of the next explicitly bound batch.
        parent: dict[str, Any] = snapshot
        if approval.bound.get("protocol") != RETENTION_PROTOCOL and any(
            e["event"]["kind"] == "MEMORYREPLICA_CLAIMED" for e in parent["events"]
        ):
            raise PermissionError("legacy task successor already consumed")
        if str(self.path) != anchor.path or device_id() != anchor.device:
            raise PermissionError("authority path/device mismatch")
        verified = parent_check(parent, parent_source)
        if digest(verified["anchor"]) != digest(anchor.data):
            raise PermissionError("stale or import-only authority checkpoint")
        bound = binding(
            path,
            output,
            anchor,
            parent,
            research,
            parent_source,
            str(approval.bound["run_id"]),
            approval.approval_id,
            approval.risk_acceptance_id,
            plan_from_binding(approval.bound),
        )
        bound["synthetic"] = synthetic
        approval.validate(bound, synthetic=synthetic, now=utc_now())
        if not synthetic and parent["meta"]["mode"] != "operator-asserted":
            raise PermissionError("synthetic authority cannot authorize HTTPS")
        if output.exists():
            raise PermissionError("fresh bound output path required")
        self.approval = approval
        self.research, self.parent_source, self.parent = research, parent_source, parent
        self.bound, self.entries = bound, []
        self.synthetic = synthetic
        self.lease = WriterLease(self.path)
        self.lease.acquire()
        try:
            self.db = sqlite3.connect(self.path, timeout=0)
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("BEGIN IMMEDIATE")
            if digest(snapshot_db(self.db)) != digest(snapshot):
                raise PermissionError("authority changed before task claim")
            if fresh_task_bundle(parent) is None:
                fresh, history = verify_history(spec_from_bundle(parent["meta"]["history_bundle"]))
                if fresh != parent["meta"]["history_bundle"] or history != verified["history"]:
                    raise PermissionError("original uncertain history changed")
            ids = (bound["run_id"], approval.approval_id, approval.risk_acceptance_id)
            if len(set(ids)) != 3 or any(
                self.db.execute("SELECT 1 FROM lineage_consumed WHERE id=?", (v,)).fetchone()
                for v in ids
            ):
                raise PermissionError("task ID already consumed")
            check_attempt_fences(self.path, parent)
            fence_path = Path(
                str(self.path) + ".memoryreplica-attempt-" + approval.risk_acceptance_id
            )
            with fence_path.open("xb") as stream:
                stream.write(canonical_json(approval.data).encode())
                stream.flush()
                os.fsync(stream.fileno())
            seq = anchor.sequence + 1
            tail = _event(
                self.db,
                seq,
                anchor.tail,
                "MEMORYREPLICA_CLAIMED",
                {
                    "binding": bound,
                    "approval": approval.data,
                    "claimed_at": utc_now(),
                    "fence": digest(approval.data),
                },
            )
            for v, role in zip(
                ids,
                ("memoryreplica-run", "memoryreplica-approval", "memoryreplica-risk"),
                strict=True,
            ):
                self.db.execute("INSERT INTO lineage_consumed VALUES (?,?,?)", (v, role, seq))
            self.db.execute(
                "INSERT INTO probe_runs VALUES (?,?,?,'OPEN',0,?,?)",
                (
                    bound["run_id"],
                    approval.approval_id,
                    canonical_json(bound),
                    ZERO,
                    canonical_json({"protocol": bound["protocol"]}),
                ),
            )
            self.tip = _anchor(object_(snapshot["meta"]), seq, tail)
            self.append("RUN_CLAIMED", {"binding": bound})
        except BaseException:
            if hasattr(self, "db"):
                self.db.rollback()
                self.db.close()
            self.lease.close()
            raise

    @classmethod
    def _resume(
        cls,
        expected: dict[str, Any],
        approval: TaskApproval,
        snapshot: dict[str, Any],
        reduced: dict[str, Any],
        research: Research,
        parent_source: Path,
    ) -> "TaskJournal":
        if type(approval) is not TaskApproval:
            raise PermissionError("explicit recovery approval required")
        now = utc_now()
        approval.validate(expected, synthetic=False, now=now)
        journal = cls.__new__(cls)
        journal.tip = Anchor.parse(expected["anchor"])
        journal.path = Path(journal.tip.path)
        parent, run, events = split(snapshot)
        if (
            run is None
            or device_id() != journal.tip.device
            or journal.path.resolve() != journal.path
            or snapshot["meta"]["mode"] != "operator-asserted"
        ):
            raise PermissionError("recovery run or device differs")
        journal.bound, journal.entries = run["binding"], run["entries"]
        journal.approval = TaskApproval.parse(events[0]["event"]["data"]["approval"])
        journal.resume_approval = approval
        journal.reduced, journal.synthetic = reduced, False
        journal.research, journal.parent_source, journal.parent = research, parent_source, parent
        journal.lease = WriterLease(journal.path)
        journal.lease.acquire()
        try:
            journal.db = sqlite3.connect(journal.path.as_uri() + "?mode=rw", uri=True, timeout=0)
            journal.db.execute("PRAGMA synchronous=FULL")
            if digest(snapshot_db(journal.db)) != expected["snapshot_digest"]:
                raise PermissionError("authority changed before recovery claim")
            check_attempt_fences(journal.path, snapshot)
            journal.assert_current()
            stage = {
                DNS_POLICY: DNS_STAGE,
                HTTP503_POLICY: HTTP503_STAGE,
                INTERRUPTED_POLICY: INTERRUPTED_STAGE,
            }.get(expected["schema"], RESUME_STAGE)
            if stage != INTERRUPTED_STAGE and timestamp(approval.expires_at) > timestamp(
                journal.approval.expires_at
            ):
                raise PermissionError("recovery approval extends original window")
            if stage in (DNS_STAGE, HTTP503_STAGE):
                prior_stage = RESUME_STAGE if stage == DNS_STAGE else DNS_STAGE
                prior = [e for e in journal.entries if e["stage"] == prior_stage]
                if (
                    len(prior) != 1
                    or digest(TaskApproval.parse(object_(prior[0]["data"])["approval"]).bound)
                    != expected["prior_recovery_binding_digest"]
                ):
                    raise PermissionError("prior recovery binding differs")
            journal.append(stage, {"approval": approval.data, "authorized_at": utc_now()})
        except BaseException:
            if hasattr(journal, "db"):
                journal.db.close()
            journal.lease.close()
            raise
        return journal

    def assert_current(self) -> None:
        current = self.bound if self.resume_approval is None else self.resume_approval.bound
        if (
            self.lease.stream is None
            or sources(Path(__file__).resolve().parents[3]) != current["actual_source"]
            or runtime() != current["runtime"]
        ):
            raise PermissionError("task source/runtime/writer changed")
        now = utc_now()
        # Sequence 7366 requires a fresh execution window after the original window expired.
        # The original claim is still validated at its recorded time by check().
        if current.get("schema") != INTERRUPTED_POLICY:
            self.approval.validate(self.bound, synthetic=self.synthetic, now=now)
        if self.resume_approval is not None:
            self.resume_approval.validate(current, synthetic=False, now=now)
        self.research.check()
        parent_check(self.parent, self.parent_source)
        snapshot = snapshot_db(self.db)
        parent, _, _ = split(snapshot)
        if digest(parent) != digest(self.parent):
            raise PermissionError("parent B history changed")
        if fresh_task_bundle(parent) is None:
            fresh, _ = verify_history(spec_from_bundle(parent["meta"]["history_bundle"]))
            if fresh != parent["meta"]["history_bundle"]:
                raise PermissionError("original history changed")
        if self.db.execute(
            "SELECT seq,digest FROM lineage_events ORDER BY seq DESC LIMIT 1"
        ).fetchone() != (self.tip.sequence, self.tip.tail):
            raise PermissionError("task authority changed")

    def append(self, stage: str, data: dict[str, Any]) -> None:
        self.write_operation = "prepare"
        try:
            self._append(stage, data)
        except (OSError, sqlite3.Error) as exc:
            code = getattr(exc, "sqlite_errorcode", None)
            if not isinstance(code, int):
                code = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
            safe_code = code if isinstance(code, int) else "unavailable"
            raise RuntimeError(
                f"MemoryReplica journal {stage} {self.write_operation} failed (code={safe_code}); "
                "no further sends"
            ) from None

    def _append(self, stage: str, data: dict[str, Any]) -> None:
        previous = digest(self.entries[-1]) if self.entries else ZERO
        entry = {
            "ordinal": len(self.entries) + 1,
            "run_id": self.bound["run_id"],
            "previous": previous,
            "stage": stage,
            "data": data,
        }
        new = [*self.entries, entry]
        reduced = reduce(self.bound, new)
        self.write_operation = "begin"
        if not self.db.in_transaction:
            self.db.execute("BEGIN IMMEDIATE")
        with self.db:
            self.write_operation = "write"
            tail = self._write_entry(entry, reduced)
            self.write_operation = "commit"
        self.entries = new
        self.reduced = reduced
        self.tip = Anchor(
            self.tip.authority_id,
            self.tip.path,
            self.tip.device,
            self.tip.history_digest,
            self.tip.sequence + 1,
            tail,
        )

    def _write_entry(self, entry: dict[str, Any], reduced: dict[str, Any]) -> str:
        stage = entry["stage"]
        terminal_anchor = {DNS_STAGE: (2189, DNS_TAIL), HTTP503_STAGE: (4822, HTTP503_TAIL)}.get(
            stage
        )
        if terminal_anchor is not None and (self.tip.sequence, self.tip.tail) != terminal_anchor:
            raise PermissionError("wrong terminal authority for follow-up")
        if stage == INTERRUPTED_STAGE and (self.tip.sequence, self.tip.tail) != (
            INTERRUPTED_SEQUENCE,
            INTERRUPTED_TAIL,
        ):
            raise PermissionError("wrong interrupted authority for follow-up")
        if self.db.execute(
            "SELECT seq,digest FROM lineage_events ORDER BY seq DESC LIMIT 1"
        ).fetchone() != (self.tip.sequence, self.tip.tail):
            raise ValueError("task authority changed during commit")
        if self.db.execute(
            "SELECT status,n,tail FROM probe_runs WHERE run_id=?", (self.bound["run_id"],)
        ).fetchone() != (
            "UNCERTAIN" if terminal_anchor is not None else "OPEN",
            len(self.entries),
            entry["previous"],
        ):
            raise ValueError("task journal changed or closed")
        if stage in FOLLOWUP_STAGES:
            approval = TaskApproval.parse(entry["data"]["approval"])
            self.db.execute(
                "INSERT INTO lineage_consumed VALUES (?,?,?)",
                (
                    approval.approval_id,
                    FOLLOWUP_STAGES[stage],
                    self.tip.sequence + 1,
                ),
            )
        self.db.execute(
            "INSERT INTO probe_entries VALUES (?,?,?,?)",
            (self.bound["run_id"], entry["ordinal"], canonical_json(entry), digest(entry)),
        )
        self.db.execute(
            "UPDATE probe_runs SET status=?,n=?,tail=? WHERE run_id=?",
            (reduced["status"], entry["ordinal"], digest(entry), self.bound["run_id"]),
        )
        return _event(
            self.db,
            self.tip.sequence + 1,
            self.tip.tail,
            "MEMORYREPLICA_COMMITTED",
            {
                "run_id": self.bound["run_id"],
                "ordinal": entry["ordinal"],
                "entry_digest": digest(entry),
                "status": reduced["status"],
                "exposure": reduced["conditional_exposure_nano_usd"],
            },
        )

    def close(self) -> None:
        self.db.close()
        self.lease.close()
