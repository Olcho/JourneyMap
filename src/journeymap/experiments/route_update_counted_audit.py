"""Provider/key/network-free E04 raw replay. Shares Session/metrics with execution."""

from datetime import datetime
from pathlib import Path
from typing import cast

from journeymap.adapters.route_update import obj, rows
from journeymap.adapters.route_update_count import count_wire
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.experiments.pickup_cue_live_audit import read_json
from journeymap.experiments.pilot_journal import digest, inspect_journal
from journeymap.experiments.route_update_audit import same
from journeymap.experiments.route_update_counted import (
    GLOBAL_STOPS,
    complete,
    prepare_branches,
    seal,
)
from journeymap.experiments.route_update_counted_cost import ledger_for_plan
from journeymap.experiments.route_update_counted_plan import AUDIT, authorize_network, validate_plan
from journeymap.experiments.route_update_counted_session import CountedSession, summary


def audit_directory(directory: Path, *, expected_plan_sha256: str | None = None) -> JsonObject:
    sessions: dict[str, CountedSession] = {}
    current: CountedSession | None = None
    initial: CountedSession | None = None
    prefixes: list[JsonObject] = []
    branches: list[JsonObject] = []
    calls = count_calls = initial_sends = 0
    prepared_baselines: JsonObject = {}
    pending_count_response: JsonObject | None = None
    phase = "PLAN"
    pending_response: JsonObject | None = None
    end: JsonObject | None = None
    stop = "COMPLETE"
    result: JsonObject = {"audit_version": AUDIT, "status": "EXCLUDED", "api_calls_during_audit": 0}
    try:
        plan = read_json(directory / "plan.json")
        if plan.get("protocol") == "experiment04-responses-execution-4":
            result["audit_version"] = "experiment04-responses-audit-4"
        if plan.get("protocol") == "experiment04-responses-execution-3":
            result["audit_version"] = "experiment04-responses-audit-3"
        validate_plan(plan)  # no source/runtime lookup; external gate checks those identities
        if expected_plan_sha256 is not None and plan["plan_sha256"] != expected_plan_sha256:
            raise ValueError("E04 expected plan mismatch")
        if "cost_policy" in plan:
            authorization = (
                read_json(directory / "authorization.json") if plan["mode"] == "live" else None
            )
            if plan["mode"] == "recording-mock" and (directory / "authorization.json").exists():
                raise ValueError("E04 limited mock/live approval record mixing")
            authorize_network(plan, authorization, "count")
            authorize_network(plan, authorization, "generate")
        allocation = rows(plan["allocation"])
        ledger = ledger_for_plan(plan)
        journal = inspect_journal(directory / "attempts.jsonl")
        if journal.status not in ("COMPLETE", "TORN_TAIL"):
            raise ValueError("E04 journal integrity")
        next_condition = 0
        initial_record: JsonObject | None = None
        for row in journal.rows:
            kind, data = row["kind"], obj(row["data"])
            if end is not None:
                raise ValueError("E04 trailing records")
            if kind == "PLAN" and phase == "PLAN":
                same(data, {"plan_sha256": plan["plan_sha256"], "mode": plan["mode"]})
                phase = "INITIAL_START"
            elif kind == "INITIAL_START" and phase == "INITIAL_START" and stop == "COMPLETE":
                if len(prefixes) >= len(allocation):
                    raise ValueError("E04 extra initial attempt")
                if initial is not None:
                    initial.close()
                for s in sessions.values():
                    s.close()
                sessions = {}
                run_id = cast(str, allocation[len(prefixes)]["prefix_id"]) + ":initial"
                same(data, {"run_id": run_id})
                initial = CountedSession(run_id, obj(plan["config"]), cast(str, plan["mode"]))
                current = initial
                initial.prepare(initial=True)
                phase = "COUNT_INTENT"
            elif kind == "COUNT_INTENT" and phase == "COUNT_INTENT" and current is not None:
                if current.pending is None:
                    current.prepare()
                prepared = obj(current.pending)
                same(data["prepared"], prepared)
                same(data["initial"], current is initial)
                counted = count_wire(
                    obj(prepared["wire"]), obj(prepared["semantic_input"]), obj(plan["config"])
                )
                same(data["count_wire"], counted)
                reservation = ledger.begin_count(counted, cast(str, plan["mode"]))
                same(data["reservation"], reservation)
                same(data["count_number"], len(ledger.counts))
                if datetime.fromisoformat(cast(str, data["requested_at"])).tzinfo is None:
                    raise ValueError("E04 count timestamp")
                if (
                    directory / f"count-request-{len(ledger.counts):03d}.utf8"
                ).read_bytes() != cast(str, counted["canonical_utf8"]).encode():
                    raise ValueError("E04 count wire original mismatch")
                phase = "COUNT_SEND"
            elif kind == "COUNT_SEND" and phase == "COUNT_SEND":
                same(data, {"count_number": len(ledger.counts)})
                ledger.counts[-1]["sent"] = "ATTEMPTED_CHARGE_UNKNOWN"
                count_calls += 1
                phase = "COUNT_RESPONSE"
            elif kind == "COUNT_RESPONSE" and phase == "COUNT_RESPONSE":
                if datetime.fromisoformat(cast(str, data["received_at"])).tzinfo is None:
                    raise ValueError("E04 count receipt timestamp")
                raw = data["raw_utf8"]
                if (
                    raw is not None
                    and (directory / f"count-response-{len(ledger.counts):03d}.utf8").read_bytes()
                    != cast(str, raw).encode()
                ):
                    raise ValueError("E04 count response original mismatch")
                pending_count_response = data
                phase = "COUNT_RESULT"
            elif (
                kind == "COUNT_RESULT"
                and phase == "COUNT_RESULT"
                and current is not None
                and pending_count_response is not None
            ):
                count_stop = ledger.receive_count(
                    cast(str | None, pending_count_response["raw_utf8"]),
                    cast(JsonObject | None, pending_count_response["failure"]),
                )
                if count_stop is not None:
                    current.stop = stop = count_stop
                same(
                    data,
                    {
                        "snapshot": current.snapshot(),
                        "ledger": ledger.snapshot(),
                        "stop": count_stop,
                    },
                )
                phase = (
                    ("INITIAL_END" if current is initial else "BRANCH_END")
                    if count_stop is not None
                    else (
                        "COUNT_ONLY_COMPLETE"
                        if plan["scope"] == "count-only"
                        else "GENERATION_INTENT"
                    )
                )
                pending_count_response = None
            elif (
                kind == "COUNT_ONLY_COMPLETE"
                and phase == "COUNT_ONLY_COMPLETE"
                and current is not None
            ):
                current.stop = stop = "COUNT_ONLY_COMPLETE"
                same(data, {"snapshot": current.snapshot(), "ledger": ledger.snapshot()})
                phase = "INITIAL_END"
            elif kind == "PRE_COUNT_STOP" and phase == "COUNT_INTENT" and current is not None:
                from journeymap.experiments.route_update_live_cost import CostStop

                counted = count_wire(
                    obj(obj(current.pending)["wire"]),
                    obj(obj(current.pending)["semantic_input"]),
                    obj(plan["config"]),
                )
                try:
                    ledger.begin_count(counted, cast(str, plan["mode"]))
                except CostStop as error:
                    same(data["reason"], str(error))
                else:
                    raise ValueError("E04 forged pre-count stop")
                current.stop = stop = cast(str, data["reason"])
                same(data["snapshot"], current.snapshot())
                same(data["ledger"], ledger.snapshot())
                phase = "INITIAL_END" if current is initial else "BRANCH_END"
            elif kind == "INTENT" and phase == "GENERATION_INTENT" and current is not None:
                if current.pending is None:
                    current.prepare()
                same(data["prepared"], current.pending)
                is_initial = current is initial
                if data["initial"] is not is_initial:
                    raise ValueError("E04 initial binding")
                timestamp = datetime.fromisoformat(cast(str, data["requested_at"]))
                if timestamp.tzinfo is None:
                    raise ValueError("E04 request timestamp")
                reservation = ledger.reserve(
                    obj(obj(current.pending)["wire"]),
                    cast(str, plan["mode"]),
                )
                same(data["reservation"], reservation)
                same(data["request_number"], len(ledger.entries))
                expected = cast(str, obj(obj(current.pending)["wire"])["canonical_utf8"]).encode()
                if (directory / f"request-{len(ledger.entries):03d}.utf8").read_bytes() != expected:
                    raise ValueError("E04 recorded/send wire mismatch")
                phase = "SEND"
            elif kind == "SEND" and phase == "SEND":
                same(data, {"request_number": len(ledger.entries)})
                ledger.entries[-1]["sent"] = "ATTEMPTED_CHARGE_UNKNOWN"
                calls += 1
                initial_sends += current is initial
                phase = "RESPONSE"
            elif kind == "RESPONSE" and phase == "RESPONSE":
                timestamp = datetime.fromisoformat(cast(str, data["received_at"]))
                if timestamp.tzinfo is None:
                    raise ValueError("E04 response timestamp")
                if data["raw_utf8"] is not None:
                    raw = cast(str, data["raw_utf8"])
                    if (
                        directory / f"response-{len(ledger.entries):03d}.utf8"
                    ).read_bytes() != raw.encode():
                        raise ValueError("E04 response original mismatch")
                pending_response = data
                phase = "RESULT"
            elif (
                kind == "RESULT"
                and phase == "RESULT"
                and pending_response is not None
                and current is not None
            ):
                outcome = complete(
                    current,
                    ledger,
                    cast(str | None, pending_response["raw_utf8"]),
                    cast(JsonObject | None, pending_response["failure"]),
                    initial=current is initial,
                )
                same(data, {"snapshot": outcome, "ledger": ledger.snapshot()})
                if current.stop in GLOBAL_STOPS:
                    stop = current.stop
                phase = (
                    "INITIAL_END"
                    if current is initial
                    else ("COUNT_INTENT" if current.stop == "RUNNING" else "BRANCH_END")
                )
                pending_response = None
            elif kind == "STOP_NOT_SENT" and phase == "GENERATION_INTENT" and current is not None:
                reason = cast(str, data["reason"])
                if reason not in (
                    "INPUT_LIMIT_NOT_SENT",
                    "BUDGET_LIMIT_NOT_SENT",
                    "CALL_LIMIT_NOT_SENT",
                    "COUNT_GENERATION_BINDING_ERROR",
                    "COUNT_EVIDENCE_MISSING",
                ):
                    raise ValueError("E04 unknown pre-send reason")
                from journeymap.experiments.route_update_live_cost import CostStop

                try:
                    ledger.reserve(
                        obj(obj(current.pending)["wire"]),
                        cast(str, plan["mode"]),
                    )
                except CostStop as error:
                    if str(error) != reason:
                        raise ValueError("E04 pre-send reason mismatch") from None
                else:
                    raise ValueError("E04 forged pre-send stop")
                current.stop, stop = reason, reason
                same(data["snapshot"], current.snapshot())
                same(data["ledger"], ledger.snapshot())
                phase = "INITIAL_END" if current is initial else "BRANCH_END"
            elif kind == "INITIAL_END" and phase == "INITIAL_END" and initial is not None:
                snapshot = initial.snapshot()
                initial_record = seal(plan, snapshot)
                same(data, initial_record)
                name = cast(str, snapshot["run_id"]).removesuffix(":initial") + ".json"
                same(read_json(directory / name), initial_record)
                prefixes.append(snapshot)
                phase = (
                    "ALL_FIRST_INPUTS_VALIDATED"
                    if initial.stop == "PREFIX_READY" and plan["stage"] == "pilot"
                    else "INITIAL_START"
                )
            elif (
                kind == "ALL_FIRST_INPUTS_VALIDATED"
                and phase == "ALL_FIRST_INPUTS_VALIDATED"
                and initial is not None
                and initial_record is not None
            ):
                parent: JsonObject = {
                    "record_sha256": initial_record["record_sha256"],
                    "parent_run_id": initial.snapshot()["run_id"],
                    "original_response_sha256": digest(obj(obj(initial.prefix)["raw_response"])),
                }
                sessions = prepare_branches(initial, parent)
                prepared_baselines.update(
                    {
                        session.kernel.manifest.run_id: session.first_move_policies
                        for session in sessions.values()
                    }
                )
                same(data, {c: s.snapshot() for c, s in sessions.items()})
                next_condition, phase = 0, "BRANCH_START"
            elif kind == "BRANCH_START" and phase == "BRANCH_START" and stop == "COMPLETE":
                condition = cast(
                    str,
                    cast(list[JsonValue], allocation[len(prefixes) - 1]["order"])[next_condition],
                )
                same(data, {"condition": condition})
                current = sessions[condition]
                phase = "COUNT_INTENT"
            elif kind == "BRANCH_END" and phase == "BRANCH_END" and current is not None:
                snapshot = current.snapshot()
                same(data, seal(plan, snapshot))
                name = cast(str, snapshot["run_id"]).replace(":", "-") + ".json"
                same(read_json(directory / name), data)
                branches.append(snapshot)
                next_condition += 1
                phase = "INITIAL_START" if next_condition == 3 else "BRANCH_START"
            elif kind == "END":
                if stop == "COMPLETE" and (
                    phase != "INITIAL_START" or len(prefixes) != len(allocation)
                ):
                    raise ValueError("E04 premature successful end")
                expected_end: JsonObject = {
                    "stop": stop,
                    "send_attempts": calls,
                    "count_send_attempts": count_calls,
                    "network_send_attempts": calls + count_calls,
                    "ledger": ledger.snapshot(),
                    "summary": summary(prefixes, branches, plan, prepared_baselines),
                }
                same(data, expected_end)
                end = data
            else:
                raise ValueError("E04 illegal durable lifecycle")
        pending_snapshot = None
        if end is None and current is not None:
            current.stop = "UNKNOWN"
            pending_snapshot = current.snapshot()
        result.update(
            {
                "status": "INCLUDED"
                if end is not None and journal.status == "COMPLETE"
                else "INCOMPLETE",
                "plan_sha256": plan["plan_sha256"],
                "mode": plan["mode"],
                "stage": plan["stage"],
                "journal_status": journal.status,
                "send_attempts": calls,
                "count_send_attempts": count_calls,
                "network_send_attempts": calls + count_calls,
                "stop": stop if end is not None else "UNKNOWN_INCOMPLETE_RECORDING",
                "prefixes": cast(list[JsonValue], prefixes),
                "branches": cast(list[JsonValue], branches),
                "pending_unknown": pending_snapshot,
                "ledger": ledger.snapshot(),
                "summary": summary(
                    prefixes,
                    branches
                    + (
                        [pending_snapshot]
                        if pending_snapshot is not None
                        and pending_snapshot["condition"] is not None
                        else []
                    ),
                    plan,
                    prepared_baselines,
                ),
                "initial_send_attempts": initial_sends,
                "scope": "Raw wire/response/application/receipt/events/metrics/cost replay. "
                "Session and metrics are shared; source/runtime identity is a separate gate.",
            }
        )
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        result["reason"] = "E04_CONTRACT_OR_RECORD_REPLAY_MISMATCH"
    finally:
        for session in sessions.values():
            session.close()
        if initial is not None:
            initial.close()
    return result
