"""Trusted E04 live/mock execution, balanced allocation and durable pre-send reservations."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from journeymap.adapters.llm import Record, strict_json
from journeymap.adapters.provider import ProviderFailure, RawModelResponse
from journeymap.adapters.route_update import obj, rows
from journeymap.adapters.route_update_live import (
    HTTPTransport,
    RecordingTransport,
    Transport,
    decode,
)
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.experiments.pilot_journal import AttemptJournal, digest, write_snapshot
from journeymap.experiments.route_update_audit import check_first_inputs
from journeymap.experiments.route_update_live_cost import CostStop, Ledger
from journeymap.experiments.route_update_live_plan import EXPORT, preflight
from journeymap.experiments.route_update_live_session import LiveSession
from journeymap.scenarios.route_update import CONDITIONS

GLOBAL_STOPS = ("INFRASTRUCTURE", "INTEGRITY", "OUTPUT_BUDGET_DIAGNOSTIC_STOP", "USAGE_COST_ERROR")


@dataclass(frozen=True)
class LiveFixture:
    initial: str
    branches: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]

    def __post_init__(self) -> None:
        if (
            type(self.initial) is not str
            or type(self.branches) is not tuple
            or len(self.branches) != 3
            or any(
                type(b) is not tuple or len(b) > 6 or any(type(t) is not str for t in b)
                for b in self.branches
            )
        ):
            raise ValueError("E04 inert initial plus three independent transcripts required")


def seal(plan: JsonObject, result: JsonObject) -> JsonObject:
    value: JsonObject = {
        "export_version": EXPORT,
        "plan_sha256": plan["plan_sha256"],
        "mode": plan["mode"],
        "result": result,
    }
    value["record_sha256"] = digest(value)
    return value


def prepare_branches(initial: LiveSession, parent: JsonObject) -> dict[str, LiveSession]:
    result: dict[str, LiveSession] = {}
    try:
        for condition in CONDITIONS:
            session = LiveSession(
                initial.kernel.manifest.run_id.removesuffix(":initial") + ":" + condition,
                initial.config,
                initial.mode,
            )
            result[condition] = session
            replay = session.reconstruct_prefix(obj(obj(initial.prefix)["candidate"]), parent)
            if obj(replay["checkpoint"])["state_digest"] != obj(initial.checkpoint)["state_digest"]:
                raise ValueError("E04 independent checkpoint mismatch")
            session.install_branch(condition)
            session.prepare()
        base, hidden, notice = (obj(result[c].pending) for c in CONDITIONS)
        check_first_inputs(base, hidden, notified=False)
        check_first_inputs(base, notice, notified=True)
        return result
    except Exception:
        for session in result.values():
            session.close()
        raise


def complete(
    session: LiveSession,
    ledger: Ledger,
    raw: str | None,
    failure: JsonObject | None,
    *,
    initial: bool,
) -> JsonObject:
    response: RawModelResponse | None = None
    global_stop = None
    if raw is not None:
        try:
            document = obj(strict_json(raw))
            response = decode(document, session.mode)
            if document["model"] not in cast(
                list[JsonValue], session.config["returned_models"]
            ) or document["status"] not in ("completed", "incomplete"):
                global_stop = "INTEGRITY"
            else:
                ledger.settle(document)
        except (ValueError, TypeError, KeyError):
            global_stop = "USAGE_COST_ERROR" if response is not None else "INTEGRITY"
            if response is None:
                failure = {"kind": "RESPONSE_IDENTITY_OR_DOCUMENT_INVALID"}
    return session.finish_live(response, failure, initial=initial, global_stop=global_stop)


def branch_summary(
    prefixes: list[JsonObject], branches: list[JsonObject], plan: JsonObject
) -> JsonObject:
    if plan["stage"] == "smoke":
        return {
            "stage": "smoke",
            "initial_records": len(prefixes),
            "initial_status": prefixes[0]["stop"] if prefixes else None,
            "branches_planned": 0,
            "branches_attempted": 0,
            "pilot_statistics": "NOT_APPLICABLE; never reuse smoke as pilot prefix",
        }
    cases_table: list[JsonValue] = []
    for alloc in rows(plan["allocation"]):
        prefix_id = cast(str, alloc["prefix_id"])
        prefix = next((r for r in prefixes if r["run_id"] == prefix_id + ":initial"), None)
        for condition in CONDITIONS:
            branch = next((r for r in branches if r["run_id"] == prefix_id + ":" + condition), None)
            status = (
                branch["stop"]
                if branch
                else (
                    "NOT_ATTEMPTED"
                    if prefix is None or prefix["stop"] == "PREFIX_READY"
                    else "SKIPPED_NO_VALID_PREFIX"
                )
            )
            cases_table.append(
                {
                    "prefix_id": prefix_id,
                    "condition": condition,
                    "status": status,
                    "first_appropriate": obj(branch["metrics"])["first_appropriate"]
                    if branch
                    else None,
                    "eventual_arrival": obj(branch["metrics"])["eventual_arrival"]
                    if branch
                    else None,
                    "metrics": branch["metrics"] if branch else None,
                    "response_status_counts": {
                        status: sum(
                            step["response_status"] == status for step in rows(branch["steps"])
                        )
                        for status in sorted(
                            {cast(str, step["response_status"]) for step in rows(branch["steps"])}
                        )
                    }
                    if branch
                    else {},
                }
            )
    stratum: JsonObject = {}
    paired = {
        k: 0
        for k in ("both_true", "notice_only_true", "hidden_only_true", "both_false", "null_pair")
    }
    pairs: list[JsonValue] = []
    for start in ("A", "B"):
        eligible = [
            r
            for r in prefixes
            if r["stop"] == "PREFIX_READY" and obj(r["checkpoint"])["position"] == start
        ]
        ids = {cast(str, p["run_id"]).removesuffix(":initial") for p in eligible}
        cases = [r for r in branches if cast(str, r["run_id"]).split(":")[0] in ids]
        stratum[start] = {
            "prefix_denominator": len(eligible),
            "conditions": {
                condition: {
                    "planned_branch_denominator": len(eligible),
                    "started_branches": sum(r["condition"] == condition for r in cases),
                    "attempted_branches": sum(
                        r["condition"] == condition
                        and any(step["provider_called"] is True for step in rows(r["steps"]))
                        for r in cases
                    ),
                    "judgeable_first_denominator": sum(
                        r["condition"] == condition
                        and type(obj(r["metrics"])["first_appropriate"]) is bool
                        for r in cases
                    ),
                    "first_appropriate": sum(
                        r["condition"] == condition
                        and obj(r["metrics"])["first_appropriate"] is True
                        for r in cases
                    ),
                    "verified_final_denominator": sum(
                        r["condition"] == condition
                        and type(obj(r["metrics"])["eventual_arrival"]) is bool
                        for r in cases
                    ),
                    "eventual_arrivals": sum(
                        r["condition"] == condition
                        and obj(r["metrics"])["eventual_arrival"] is True
                        for r in cases
                    ),
                    "recovery_eligible_denominator": sum(
                        r["condition"] == condition
                        and obj(r["metrics"])["first_appropriate"] is False
                        and type(obj(r["metrics"])["eventual_arrival"]) is bool
                        for r in cases
                    ),
                    "recovered_arrivals": sum(
                        r["condition"] == condition
                        and obj(r["metrics"])["recovered_arrival"] is True
                        for r in cases
                    ),
                    "B_first_D_direction": sum(
                        start == "B"
                        and r["condition"] == condition
                        and obj(r["first_judgment"])["destination"] == "D"
                        for r in cases
                        if r["first_judgment"] is not None
                    ),
                }
                for condition in CONDITIONS
            },
        }
        if start == "A":
            for prefix_id in sorted(ids):
                values: dict[str, JsonValue] = {}
                for condition in CONDITIONS[1:]:
                    found = [r for r in cases if r["run_id"] == prefix_id + ":" + condition]
                    values[condition] = (
                        obj(found[0]["metrics"])["first_appropriate"] if found else None
                    )
                hidden, notice = (values[c] for c in CONDITIONS[1:])
                key = (
                    "null_pair"
                    if hidden is None or notice is None
                    else (
                        "both_true"
                        if hidden and notice
                        else "notice_only_true"
                        if notice
                        else "hidden_only_true"
                        if hidden
                        else "both_false"
                    )
                )
                paired[key] += 1
                pairs.append(
                    {"prefix_id": prefix_id, "hidden": hidden, "notice": notice, "pair": key}
                )
    actual = [
        {
            "prefix_id": a["prefix_id"],
            "planned_order": a["order"],
            "actual_order": [
                r["condition"]
                for r in branches
                if cast(str, r["run_id"]).split(":")[0] == a["prefix_id"]
            ],
        }
        for a in rows(plan["allocation"])
    ]
    A = obj(stratum["A"])["prefix_denominator"]
    return {
        "stage": "pilot",
        "planned_initial_attempts": plan["prefix_count"],
        "planned_branches": 18,
        "branch_cases": cases_table,
        "initial_records": len(prefixes),
        "initial_attempts": sum(cast(int, r["prefix_calls"]) for r in prefixes),
        "initial_not_attempted": cast(int, plan["prefix_count"])
        - sum(cast(int, r["prefix_calls"]) for r in prefixes),
        "strata": stratum,
        "A_matched_pairs": cast(JsonObject, paired),
        "pairs": pairs,
        "A_interpretation": "PRIMARY_COMPARISON_UNAVAILABLE"
        if A == 0
        else "CASE_OBSERVATION_ONLY"
        if cast(int, A) <= 2
        else "EXPLORATORY_ONLY",
        "orders": cast(list[JsonValue], actual),
        "actual_balance_complete": all(a["planned_order"] == a["actual_order"] for a in actual),
        "independent_sample_unit": "initial attempt/prefix; branches/calls are dependent",
        "not_pooled_with": ["smoke", "E03", "offline v1/v2", "other mock/live runs"],
    }


def execute(
    plan: JsonObject,
    directory: Path,
    *,
    fixtures: tuple[LiveFixture, ...] | None = None,
    authorization: JsonObject | None = None,
    mock_input_tokens: int = 1000,
) -> JsonObject:
    preflight(plan, directory, authorization)
    mock = plan["mode"] == "recording-mock"
    if mock and (
        type(fixtures) is not tuple
        or len(fixtures) != plan["prefix_count"]
        or any(type(f) is not LiveFixture for f in fixtures)
    ):
        raise ValueError("E04 explicit independent mock transcripts required")
    if not mock and fixtures is not None:
        raise ValueError("E04 no mock transcript in live")
    # Exclusive claim is retained even after recording/transport failure; no resume.
    claims = Path(__file__).resolve().parents[3] / "trials/e04-plan-claims"
    claims.mkdir(parents=True, exist_ok=True)
    write_snapshot(
        claims / (cast(str, plan["plan_sha256"]) + ".json"),
        {
            "plan_sha256": plan["plan_sha256"],
            "output_path": plan["output_path"],
            "stage": plan["stage"],
        },
    )
    directory.mkdir()
    write_snapshot(directory / "plan.json", plan)
    journal = AttemptJournal(directory / "attempts.jsonl")
    ledger = Ledger(
        cast(str, obj(plan["budget_review"])["stage_reservation_cap_usd"]),
        cast(int, plan["call_cap"]),
    )
    sessions: dict[str, LiveSession] = {}
    current: LiveSession | None = None
    initial: LiveSession | None = None
    calls = 0
    stop = "COMPLETE"
    prefixes: list[JsonObject] = []
    branches: list[JsonObject] = []
    phase = "plan"
    outcomes: list[str] = []

    def invoke(*, first: bool = False) -> bool:
        nonlocal calls, phase, stop
        if current is None or current.pending is None:
            raise ValueError("E04 prepared decision required")
        phase = "reserve"
        try:
            reservation = ledger.reserve(
                obj(current.pending["wire"]),
                cast(str, plan["mode"]),
                mock_input_tokens=mock_input_tokens,
            )
        except CostStop as error:
            stop = str(error)
            current.stop = stop
            journal.append(
                "STOP_NOT_SENT",
                {
                    "reason": stop,
                    "snapshot": current.snapshot(),
                    "ledger": ledger.snapshot(),
                    "mock_input_tokens": mock_input_tokens,
                },
            )
            return False
        prepared = Record.capture(current.pending).data
        number = len(ledger.entries)
        phase = "intent"
        payload = cast(str, obj(prepared["wire"])["canonical_utf8"]).encode()
        with (directory / f"request-{number:03d}.utf8").open("xb") as stream:
            stream.write(payload)
        journal.append(
            "INTENT",
            {
                "prepared": prepared,
                "reservation": reservation,
                "initial": first,
                "request_number": number,
                "requested_at": datetime.now(UTC).isoformat(),
            },
        )
        # Provider construction is after complete preflight, guard, reservation and intent.
        transport: Transport = (
            RecordingTransport(outcomes.pop(0))
            if mock and outcomes
            else (
                RecordingTransport("TRANSPORT_ERROR")
                if mock
                else HTTPTransport(obj(plan["config"]))
            )
        )
        phase = "send"
        ledger.entries[-1]["sent"] = "ATTEMPTED_CHARGE_UNKNOWN"
        journal.append("SEND", {"request_number": number})
        calls += 1
        raw = None
        failure: JsonObject | None = None
        try:
            response_bytes = transport.send(payload)
            raw = response_bytes.decode("utf-8")
            phase = "response-file"
            with (directory / f"response-{number:03d}.utf8").open("xb") as stream:
                stream.write(response_bytes)
        except ProviderFailure as error:
            failure = {"kind": error.kind}
        except UnicodeError:
            failure = {"kind": "RESPONSE_ENCODING_INVALID"}
        phase = "response"
        journal.append(
            "RESPONSE",
            {"raw_utf8": raw, "failure": failure, "received_at": datetime.now(UTC).isoformat()},
        )
        phase = "result"
        result = complete(current, ledger, raw, failure, initial=first)
        journal.append("RESULT", {"snapshot": result, "ledger": ledger.snapshot()})
        if current.stop in GLOBAL_STOPS:
            stop = current.stop
        return True

    try:
        journal.append("PLAN", {"plan_sha256": plan["plan_sha256"], "mode": plan["mode"]})
        for index, alloc in enumerate(rows(plan["allocation"])):
            prefix_id = cast(str, alloc["prefix_id"])
            outcomes = [fixtures[index].initial] if fixtures else []
            initial = LiveSession(
                prefix_id + ":initial", obj(plan["config"]), cast(str, plan["mode"])
            )
            current = initial
            journal.append("INITIAL_START", {"run_id": prefix_id + ":initial"})
            initial.prepare(initial=True)
            invoke(first=True)
            result = initial.snapshot()
            prefixes.append(result)
            record = seal(plan, result)
            write_snapshot(directory / (prefix_id + ".json"), record)
            journal.append("INITIAL_END", record)
            if stop != "COMPLETE":
                break
            if plan["stage"] == "smoke" or initial.stop != "PREFIX_READY":
                initial.close()
                initial = None
                continue
            parent: JsonObject = {
                "record_sha256": record["record_sha256"],
                "parent_run_id": result["run_id"],
                "original_response_sha256": digest(obj(obj(result["prefix"])["raw_response"])),
            }
            sessions = prepare_branches(initial, parent)
            journal.append(
                "ALL_FIRST_INPUTS_VALIDATED", {c: s.snapshot() for c, s in sessions.items()}
            )
            for condition_value in cast(list[JsonValue], alloc["order"]):
                condition = cast(str, condition_value)
                current = sessions[condition]
                outcomes = (
                    list(fixtures[index].branches[CONDITIONS.index(condition)]) if fixtures else []
                )
                journal.append("BRANCH_START", {"condition": condition})
                while current.stop == "RUNNING":
                    if current.pending is None:
                        current.prepare()
                    if not invoke():
                        break
                result = current.snapshot()
                branches.append(result)
                record = seal(plan, result)
                write_snapshot(directory / (prefix_id + "-" + condition + ".json"), record)
                journal.append("BRANCH_END", record)
                if stop != "COMPLETE":
                    break
            for session in sessions.values():
                session.close()
            sessions = {}
            initial.close()
            initial = current = None
            if stop != "COMPLETE":
                break
        journal.append(
            "END",
            {
                "stop": stop,
                "send_attempts": calls,
                "ledger": ledger.snapshot(),
                "summary": branch_summary(prefixes, branches, plan),
            },
        )
    except Exception:
        stop = "INTEGRITY_RECORDING"
        write_snapshot(
            directory / "recording-failure.json",
            {"phase": phase, "send_attempts": calls, "stop": stop, "ledger": ledger.snapshot()},
        )
    finally:
        for session in sessions.values():
            session.close()
        if initial is not None:
            initial.close()
        journal.close()
    from journeymap.experiments.route_update_live_audit import audit_directory

    return audit_directory(directory, expected_plan_sha256=cast(str, plan["plan_sha256"]))
