"""Snapshot completeness and semantic paid accounting audit, without transport."""

import hashlib
from dataclasses import asdict
from typing import Any, cast
from uuid import UUID

from journeymap.adapters.life_http import parsed_response
from journeymap.adapters.life_llm import raw_record
from journeymap.adapters.life_responses import LifeExecutionPlan, Usage, response_text
from journeymap.adapters.llm import strict_json
from journeymap.core.canonical import canonical_json


def check(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(("paid journal " if reason == "raw mismatch" else "paid audit ") + reason)


def same(a: Any, b: Any) -> bool:
    return canonical_json(a) == canonical_json(b)


def audit_controller(
    c: dict[str, Any], calls: list[Any], observed: list[Any], inputs: list[Any], app: Any
) -> dict[str, Any]:
    p = LifeExecutionPlan()
    rows, seal = c["journal"], c["audit_snapshot"]
    check(bool(rows), "missing claim")
    check(type(seal["entry_count"]) is int and seal["entry_count"] == len(rows), "entry count")
    check(type(seal["call_count"]) is int and seal["call_count"] == len(calls), "call count")
    check(
        seal["tail_digest"] == hashlib.sha256(canonical_json(rows[-1]).encode()).hexdigest(),
        "tail digest",
    )
    check(seal["run_status"] in {"CLAIMED", "FINISHED", "UNCERTAIN"}, "run status")
    check(
        type(seal["worker_in_flight"]) is bool and type(seal["owner_finished"]) is bool,
        "snapshot flags",
    )
    check(seal["owner_finished"] == (seal["run_status"] != "CLAIMED"), "owner lifecycle")
    check(
        rows[0]["stage"] == "RUN_CLAIMED" and same(rows[0]["data"]["binding"], c["binding"]),
        "claim binding",
    )
    check(
        str(UUID(rows[0]["data"]["approval_id"])) == rows[0]["data"]["approval_id"], "approval ID"
    )
    check([call.sequence for call in calls] == list(range(1, len(calls) + 1)), "call sequence")
    ada = [o for o in observed if o.actor_id == p.actor]
    check(
        [call.observation_id for call in calls] == [o.observation_id for o in ada],
        "Ada observation coverage",
    )
    check(
        all(
            call.actor_id == p.actor and call.tick == o.simulation_time
            for call, o in zip(calls, ada, strict=True)
        ),
        "call identity",
    )
    check(
        all(
            type(call.sent) is bool
            and call.sent == (len(canonical_json(call.body).encode()) <= p.max_input_bytes)
            for call in calls
        ),
        "dispatch flag",
    )
    check(
        type(c["requests_started"]) is int
        and c["requests_started"] == sum(call.sent for call in calls),
        "request summary",
    )
    check(c["request_limit"] == p.request_limit and c["actor"] == p.actor, "controller plan")
    submits = [i for i in inputs if i.kind == "submit" and i.actor == p.actor]
    check(
        [i.request for i in submits] == [call.action for call in calls if call.action is not None],
        "Ada action coverage",
    )
    by_seq = {call.sequence: call for call in calls}
    seen: dict[int, dict[str, dict[str, Any]]] = {call.sequence: {} for call in calls}
    docs: dict[int, dict[str, Any]] = {}
    usages: dict[int, Usage] = {}
    state: dict[str, Any] = {
        "plan": asdict(p),
        "attempts": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_nano_usd": 0,
        "reserved_input": 0,
        "reserved_output": 0,
        "reserved_cost": 0,
        "stop": None,
        "cost_state": "NO_USAGE",
        "reported_usage": [],
        "reserved_cost_is_conditional": True,
        "unknown_model_or_tier_cost_bounded": False,
        "rate_card_cost_is_invoice": False,
    }
    terminal = None
    allowed = {
        "DECISION_STARTED",
        "PREPARED_NOT_SENT",
        "ATTEMPTED",
        "HTTP_RECEIVED",
        "RESPONSE_ENVELOPE_RECEIVED",
        "RESPONSE_RECEIVED",
        "USAGE_CHECKED",
        "USAGE_PRICED",
        "REQUEST_ERROR",
        "NOT_SENT",
        "RESPONSE_DELIVERY",
        "OWNER_TIMEOUT",
        "OWNER_PACKET",
        "RESPONSE_CLASSIFIED",
        "ACTOR_RECEIPT",
        "ACTOR_COMPLETION",
        "RESERVATION_BLOCKED",
    }
    worker_stages = {
        "HTTP_RECEIVED",
        "RESPONSE_ENVELOPE_RECEIVED",
        "RESPONSE_RECEIVED",
        "USAGE_CHECKED",
        "USAGE_PRICED",
        "REQUEST_ERROR",
        "NOT_SENT",
        "RESPONSE_DELIVERY",
        "OWNER_PACKET",
        "OWNER_TIMEOUT",
    }
    for row in rows[1:]:
        stage, d = row["stage"], row["data"]
        if stage in {"RUN_FINISHED", "RUN_UNCERTAIN"}:
            check(terminal is None and not d, "duplicate/invalid terminal")
            active = any(
                "DECISION_STARTED" in s and "OWNER_PACKET" not in s and by_seq[n].sent
                for n, s in seen.items()
            )
            uncertain = bool(state["reserved_cost"]) or active
            check(
                stage == ("RUN_UNCERTAIN" if uncertain else "RUN_FINISHED"), "terminal accounting"
            )
            terminal = stage
            continue
        check(stage in allowed, "unknown or duplicate claim stage")
        if terminal:
            check(terminal == "RUN_UNCERTAIN" and stage in worker_stages, "post-terminal action")
        n = d["sequence"]
        check(type(n) is int and n in by_seq, "extra journal sequence")
        call, s = by_seq[n], seen[n]
        check(stage not in s, "duplicate stage")
        request_digest = hashlib.sha256(canonical_json(asdict(call.request)).encode()).hexdigest()
        if "request_digest" in d:
            check(d["request_digest"] == request_digest, "request digest")
        if stage == "DECISION_STARTED":
            check(
                d["observation_id"] == call.observation_id
                and d["request_digest"] == request_digest,
                "decision binding",
            )
            check(
                n == sum("DECISION_STARTED" in stages for stages in seen.values()) + 1,
                "decision order",
            )
        else:
            check("DECISION_STARTED" in s, "missing decision")
        if stage in worker_stages - {"OWNER_PACKET", "OWNER_TIMEOUT"}:
            check("OWNER_PACKET" not in s, "worker stage after packet")
        if stage == "RESERVATION_BLOCKED":
            cost = (
                p.context_input_reserve * p.input_nano_usd_per_token
                + p.max_output_tokens * p.output_nano_usd_per_token
            )
            expected = state["stop"] or (
                "REQUEST_UNCERTAIN"
                if state["reserved_cost"]
                else "INPUT_TOO_LARGE"
                if len(canonical_json(call.body).encode()) > p.max_input_bytes
                else "REQUEST_LIMIT"
                if state["attempts"] >= p.request_limit
                else "INPUT_TOKEN_LIMIT"
                if state["input_tokens"] + p.context_input_reserve > p.total_input_tokens
                else "OUTPUT_TOKEN_LIMIT"
                if state["output_tokens"] + p.max_output_tokens > p.total_output_tokens
                else "COST_LIMIT"
                if state["cost_nano_usd"] + cost > p.cost_limit_nano_usd
                else None
            )
            check(
                expected is not None and d["reason"] == expected and "PREPARED_NOT_SENT" not in s,
                "blocked reservation",
            )
            state["stop"] = expected
            check(same(d["accounting"], state), "blocked accounting")
        if stage == "PREPARED_NOT_SENT":
            check(not state["reserved_cost"] and state["stop"] is None, "reservation lifecycle")
            check(
                state["attempts"] < p.request_limit
                and len(canonical_json(call.body).encode()) <= p.max_input_bytes,
                "reservation admission",
            )
            check(
                state["input_tokens"] + p.context_input_reserve <= p.total_input_tokens
                and state["output_tokens"] + p.max_output_tokens <= p.total_output_tokens,
                "token cap",
            )
            cost = (
                p.context_input_reserve * p.input_nano_usd_per_token
                + p.max_output_tokens * p.output_nano_usd_per_token
            )
            check(state["cost_nano_usd"] + cost <= p.cost_limit_nano_usd, "cost cap")
            state.update(
                attempts=state["attempts"] + 1,
                reserved_input=p.context_input_reserve,
                reserved_output=p.max_output_tokens,
                reserved_cost=cost,
            )
            check(state["attempts"] == n and same(d["accounting"], state), "prepared accounting")
        elif stage == "ATTEMPTED":
            check(
                "PREPARED_NOT_SENT" in s
                and same(d["body"], call.body)
                and same(d["request"], asdict(call.request)),
                "attempt binding",
            )
        elif stage in {"HTTP_RECEIVED", "RESPONSE_ENVELOPE_RECEIVED"}:
            check("ATTEMPTED" in s, "response before send")
            check(
                (d["http_status"] != 200)
                if stage == "HTTP_RECEIVED"
                else (d["http_status"] == 200),
                "HTTP stage",
            )
        elif stage == "RESPONSE_RECEIVED":
            check("RESPONSE_ENVELOPE_RECEIVED" in s, "missing envelope")
            check(d["http_status"] == 200, "raw HTTP status")
            raw = d["raw"].encode("utf-8")
            envelope = s["RESPONSE_ENVELOPE_RECEIVED"]
            check(
                envelope["bytes"] == len(raw)
                and envelope["sha256"] == hashlib.sha256(raw).hexdigest(),
                "response bytes",
            )
            doc = strict_json(d["raw"])
            check(isinstance(doc, dict), "response object")
            docs[n] = cast(dict[str, Any], doc)
        elif stage == "USAGE_CHECKED":
            check("RESPONSE_RECEIVED" in s, "usage before response")
            usage = Usage.parse(docs[n].get("usage"))
            check(same(d["usage"], asdict(usage)), "reported usage")
            usages[n] = usage
            state["reported_usage"].append(asdict(usage))
        elif stage == "USAGE_PRICED":
            check(
                "USAGE_CHECKED" in s
                and docs[n].get("model") == p.model
                and docs[n].get("service_tier") == p.service_tier,
                "unpriceable usage",
            )
            u = usages[n]
            check(
                u.input_tokens <= state["reserved_input"]
                and u.output_tokens <= state["reserved_output"],
                "reservation exceeded",
            )
            state["input_tokens"] += u.input_tokens
            state["output_tokens"] += u.output_tokens
            state["cost_nano_usd"] += (
                (u.input_tokens - u.cached_tokens) * p.input_nano_usd_per_token
                + u.cached_tokens * p.cached_nano_usd_per_token
                + u.output_tokens * p.output_nano_usd_per_token
            )
            state.update(
                reserved_input=0, reserved_output=0, reserved_cost=0, cost_state="KNOWN_RATE_CARD"
            )
            check(same(d["accounting"], state), "priced accounting")
        elif stage in {"REQUEST_ERROR", "NOT_SENT"}:
            check("PREPARED_NOT_SENT" in s, "error without reservation")
            attempted = "ATTEMPTED" in s
            check(attempted == (stage == "REQUEST_ERROR"), "sent classification")
            reason = d["reason"]
            if "HTTP_RECEIVED" in s:
                check(reason == "HTTP_ERROR", "HTTP failure classification")
            if not attempted:
                check(
                    reason
                    in {"CREDENTIAL_UNAVAILABLE", "SECRET_ECHO", "TIMEOUT", "JOURNAL_UNAVAILABLE"},
                    "not-sent reason",
                )
            if n in docs and "USAGE_PRICED" not in s:
                try:
                    u = Usage.parse(docs[n].get("usage"))
                except ValueError as error:
                    check(reason == str(error), "usage error classification")
                else:
                    expected = (
                        "IDENTITY_MISMATCH"
                        if docs[n].get("model") != p.model
                        else "SERVICE_TIER_MISMATCH"
                        if docs[n].get("service_tier") != p.service_tier
                        else "USAGE_EXCEEDS_RESERVATION"
                    )
                    check(
                        reason == expected or reason == "JOURNAL_UNAVAILABLE",
                        "pricing failure classification",
                    )
                    check(n in usages or reason == "JOURNAL_UNAVAILABLE", "missing usage check")
            elif n in docs:
                doc = docs[n]
                expected = None
                if type(doc.get("id")) is not str or not doc["id"]:
                    expected = "INVALID_RESPONSE_ID"
                elif doc.get("status") not in {"completed", "incomplete"}:
                    expected = "RESPONSE_FAILED"
                else:
                    try:
                        text, _ = response_text(doc)
                    except ValueError as error:
                        expected = str(error)
                    else:
                        if len(text.encode()) > p.max_output_bytes:
                            expected = "OUTPUT_TOO_LARGE"
                check(
                    reason == expected or reason == "JOURNAL_UNAVAILABLE",
                    "response error classification",
                )
            if state["reserved_cost"]:
                state["cost_state"] = "UNCERTAIN" if attempted else "NOT_SENT"
                if not attempted:
                    state.update(reserved_input=0, reserved_output=0, reserved_cost=0)
            state["stop"] = reason
            check(same(d["accounting"], state), "error accounting")
        elif stage == "RESPONSE_DELIVERY":
            check("USAGE_PRICED" in s and "REQUEST_ERROR" not in s, "delivery lifecycle")
            text, refusal = response_text(docs[n])
            check(
                type(docs[n].get("id")) is str
                and bool(docs[n]["id"])
                and docs[n].get("status") in {"completed", "incomplete"}
                and len(text.encode()) <= p.max_output_bytes,
                "delivery validation",
            )
            if refusal or docs[n]["status"] != "completed":
                state["stop"] = "REFUSAL" if refusal else "INCOMPLETE"
        elif stage == "OWNER_PACKET":
            check(
                ("RESPONSE_DELIVERY" in s) == (call.raw is not None),
                "owner response retention",
            )
            if "RESPONSE_DELIVERY" in s:
                check(
                    raw_record(parsed_response(docs[n])) == call.raw,
                    "raw mismatch",
                )
            check(d["late_discarded"] == call.discarded_late, "late disposition")
            check(d["status"] in {"READY", "ERROR"}, "packet state")
            check((d["error"] is None) == (d["status"] == "READY"), "packet error")
            if d["status"] == "READY":
                check(
                    "RESPONSE_DELIVERY" in s and not d["late_discarded"],
                    "READY packet delivery",
                )
            if d["status"] == "ERROR":
                check(call.error == d["error"] and call.status == "ERROR", "call error disposition")
                wire_error = s.get("REQUEST_ERROR", s.get("NOT_SENT", s.get("RESERVATION_BLOCKED")))
                check(
                    d["late_discarded"]
                    or (wire_error is not None and d["error"] == wire_error["reason"]),
                    "missing wire error",
                )
        elif stage == "OWNER_TIMEOUT":
            check(
                d["world_paused"] is True
                and d["reservation_released"] is False
                and call.error == "TIMEOUT"
                and call.status == "ERROR",
                "timeout disposition",
            )
        elif stage == "RESPONSE_CLASSIFIED":
            check(
                "OWNER_PACKET" in s
                and s["OWNER_PACKET"]["status"] == "READY"
                and call.raw is not None
                and not call.discarded_late,
                "classification lifecycle",
            )
            check(
                same(d["candidate"], call.candidate) and d["error"] == call.error,
                "candidate classification",
            )
        elif stage == "ACTOR_RECEIPT":
            check("RESPONSE_CLASSIFIED" in s and call.action is not None, "receipt lifecycle")
            check(
                same(
                    d,
                    {
                        "sequence": n,
                        "action": asdict(call.action),
                        "receipt": asdict(call.receipt) if call.receipt else None,
                    },
                ),
                "receipt record",
            )
        elif stage == "ACTOR_COMPLETION":
            check(
                "ACTOR_RECEIPT" in s
                and call.final_result is not None
                and same(d["result"], asdict(call.final_result)),
                "completion record",
            )
        s[stage] = d
    check(
        terminal
        == (
            {"CLAIMED": None, "FINISHED": "RUN_FINISHED", "UNCERTAIN": "RUN_UNCERTAIN"}[
                seal["run_status"]
            ]
        ),
        "missing terminal",
    )
    for call in calls:
        s = seen[call.sequence]
        check("DECISION_STARTED" in s, "missing call journal")
        if "PREPARED_NOT_SENT" not in s:
            check(
                call.error
                in {
                    "INPUT_TOO_LARGE",
                    "REQUEST_LIMIT",
                    "INPUT_TOKEN_LIMIT",
                    "OUTPUT_TOKEN_LIMIT",
                    "COST_LIMIT",
                    "ADMISSION_MISMATCH",
                    "JOURNAL_UNAVAILABLE",
                },
                "unexplained unprepared call",
            )
            check(
                "RESERVATION_BLOCKED" in s or (not call.sent and call.error == "INPUT_TOO_LARGE"),
                "missing block evidence",
            )
        if call.raw is not None:
            check("OWNER_PACKET" in s and "RESPONSE_DELIVERY" in s, "missing raw disposition")
            check(raw_record(parsed_response(docs[call.sequence])) == call.raw, "raw mismatch")
        packet = s.get("OWNER_PACKET")
        if packet is not None and packet["status"] == "READY":
            if "RESPONSE_CLASSIFIED" not in s:
                check(call.status == "READY", "unclassified READY status")
            elif call.action is None:
                check(
                    call.status == "ERROR"
                    and call.error is not None
                    and call.error == s["RESPONSE_CLASSIFIED"]["error"]
                    and call.candidate is None
                    and call.receipt is None
                    and call.final_result is None,
                    "classified error disposition",
                )
        check(("ACTOR_RECEIPT" in s) == (call.action is not None), "receipt coverage")
        if call.status == "READY":
            check(
                call.raw is not None
                and call.error is None
                and call.action is None
                and call.candidate is None
                and "RESPONSE_CLASSIFIED" not in s,
                "READY disposition",
            )
        if call.discarded_late:
            check(
                call.status == "ERROR"
                and call.error == "TIMEOUT"
                and call.action is None
                and call.candidate is None
                and call.receipt is None
                and call.final_result is None,
                "late disposition",
            )
        if call.action is not None:
            check(
                call.receipt is not None
                and call.error is None
                and call.status
                == (
                    call.final_result.status
                    if call.final_result is not None
                    else call.receipt.status
                ),
                "action status",
            )
            actual = app.game_for(call.actor_id).result(call.action.action_request_id)
            complete = actual is not None and actual.status in {"SUCCEEDED", "FAILED"}
            check(
                complete == (call.final_result is not None) == ("ACTOR_COMPLETION" in s),
                "completion coverage",
            )
            if complete:
                check(call.final_result == actual, "completion result")
        if "RESPONSE_DELIVERY" in s and "OWNER_PACKET" not in s:
            check(
                seal["worker_in_flight"] or s["RESPONSE_DELIVERY"]["owner_closed"],
                "missing owner packet",
            )
    unresolved = any(call.sent and "OWNER_PACKET" not in seen[call.sequence] for call in calls)
    check(seal["worker_in_flight"] == unresolved, "worker disposition")
    check(same(c["usage"], state), "accounting summary")
    return {
        "audit_verified": True,
        "snapshot_state": seal["run_status"],
        "outcome_certain": seal["run_status"] == "FINISHED"
        and not unresolved
        and not state["reserved_cost"],
    }
