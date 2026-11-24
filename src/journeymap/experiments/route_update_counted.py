"""E04 counted execution: count once before each generation; no retry/cache/resume."""

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from journeymap.adapters.llm import Record, strict_json
from journeymap.adapters.provider import ProviderFailure, RawModelResponse
from journeymap.adapters.route_update import obj, rows
from journeymap.adapters.route_update_count import (
    CountHTTPTransport,
    RecordingCountTransport,
    count_wire,
)
from journeymap.adapters.route_update_live import (
    HTTPTransport,
    RecordingTransport,
    Transport,
    decode,
)
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.experiments.pilot_journal import AttemptJournal, digest, write_snapshot
from journeymap.experiments.route_update_audit import check_first_inputs
from journeymap.experiments.route_update_counted_cost import CountedLedger, ledger_for_plan
from journeymap.experiments.route_update_counted_plan import (
    authorize_network,
    preflight,
    validate_plan,
)
from journeymap.experiments.route_update_counted_session import CountedSession, summary
from journeymap.experiments.route_update_live import LiveFixture
from journeymap.experiments.route_update_live_cost import CostStop
from journeymap.scenarios.route_update import CONDITIONS

GLOBAL_STOPS = (
    "INFRASTRUCTURE",
    "INTEGRITY",
    "OUTPUT_BUDGET_DIAGNOSTIC_STOP",
    "USAGE_COST_ERROR",
    "COUNT_USAGE_MISMATCH",
)


def fee_bound(plan: JsonObject) -> str | None:
    review = obj(plan["count_price_review"])
    return cast(
        str | None,
        review.get("per_count_max_usd")
        if plan["mode"] == "recording-mock"
        else review.get("upper_bound_usd"),
    )


def seal(plan: JsonObject, result: JsonObject) -> JsonObject:
    value: JsonObject = {
        "export_version": plan["export_version"],
        "plan_sha256": plan["plan_sha256"],
        "mode": plan["mode"],
        "result": result,
    }
    value["record_sha256"] = digest(value)
    return value


def prepare_branches(initial: CountedSession, parent: JsonObject) -> dict[str, CountedSession]:
    result: dict[str, CountedSession] = {}
    try:
        for condition in CONDITIONS:
            session = CountedSession(
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
    session: CountedSession,
    ledger: CountedLedger,
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
        except CostStop as error:
            global_stop = (
                "COUNT_USAGE_MISMATCH"
                if str(error) == "COUNT_USAGE_MISMATCH"
                else "USAGE_COST_ERROR"
            )
        except (ValueError, TypeError, KeyError):
            global_stop = "USAGE_COST_ERROR" if response is not None else "INTEGRITY"
            if response is None:
                failure = {"kind": "RESPONSE_IDENTITY_OR_DOCUMENT_INVALID"}
    return session.finish_live(response, failure, initial=initial, global_stop=global_stop)


def execute(
    plan: JsonObject,
    directory: Path,
    *,
    fixtures: tuple[LiveFixture, ...] | None = None,
    authorization: JsonObject | None = None,
    mock_input_tokens: int = 1000,
    count_outcomes: tuple[int | str, ...] | None = None,
) -> JsonObject:
    preflight(plan, directory, authorization)
    if count_outcomes is not None and (
        plan["mode"] != "recording-mock" or type(count_outcomes) is not tuple
    ):
        raise ValueError("E04 explicit mock count transcript only")
    counts_to_return = list(count_outcomes) if count_outcomes is not None else None
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
    if authorization is not None:
        write_snapshot(directory / "authorization.json", authorization)
    journal = AttemptJournal(directory / "attempts.jsonl")
    ledger = ledger_for_plan(plan)
    sessions: dict[str, CountedSession] = {}
    current: CountedSession | None = None
    initial: CountedSession | None = None
    calls = count_calls = 0
    prepared_baselines: JsonObject = {}
    stop = "COMPLETE"
    prefixes: list[JsonObject] = []
    branches: list[JsonObject] = []
    phase = "plan"
    outcomes: list[str] = []

    def invoke(*, first: bool = False) -> bool:
        nonlocal calls, count_calls, phase, stop
        if current is None or current.pending is None:
            raise ValueError("E04 prepared decision required")
        if current.pending is None:
            raise ValueError("missing observation before count")
        phase = "count-intent"
        counted = count_wire(
            obj(current.pending["wire"]),
            obj(current.pending["semantic_input"]),
            obj(plan["config"]),
        )
        try:
            count_reservation = ledger.begin_count(counted, cast(str, plan["mode"]))
        except CostStop as error:
            stop = str(error)
            current.stop = stop
            journal.append(
                "PRE_COUNT_STOP",
                {"reason": stop, "snapshot": current.snapshot(), "ledger": ledger.snapshot()},
            )
            return False
        count_number = len(ledger.counts)
        count_payload = cast(str, counted["canonical_utf8"]).encode()
        with (directory / f"count-request-{count_number:03d}.utf8").open("xb") as stream:
            stream.write(count_payload)
        journal.append(
            "COUNT_INTENT",
            {
                "prepared": Record.capture(current.pending).data,
                "count_wire": counted,
                "reservation": count_reservation,
                "initial": first,
                "count_number": count_number,
                "requested_at": datetime.now(UTC).isoformat(),
            },
        )
        authorize_network(plan, authorization, "count")
        if not mock:
            validate_plan(plan, current=True)
        count_transport: Transport = (
            RecordingCountTransport(
                mock_input_tokens
                if counts_to_return is None
                else counts_to_return.pop(0)
                if counts_to_return
                else "TRANSPORT_ERROR"
            )
            if mock
            else CountHTTPTransport(obj(plan["config"]))
        )
        phase = "count-send"
        ledger.counts[-1]["sent"] = "ATTEMPTED_CHARGE_UNKNOWN"
        journal.append("COUNT_SEND", {"count_number": count_number})
        count_calls += 1
        count_raw = None
        count_failure: JsonObject | None = None
        try:
            count_bytes = count_transport.send(count_payload)
            if len(count_bytes) > 2000000:
                raise ProviderFailure("RESPONSE_TOO_LARGE")
            count_raw = count_bytes.decode("utf-8")
            phase = "count-response-file"
            with (directory / f"count-response-{count_number:03d}.utf8").open("xb") as stream:
                stream.write(count_bytes)
        except ProviderFailure as error:
            count_failure = {"kind": error.kind}
        except UnicodeError:
            count_failure = {"kind": "RESPONSE_ENCODING_INVALID"}
        phase = "count-response"
        journal.append(
            "COUNT_RESPONSE",
            {
                "raw_utf8": count_raw,
                "failure": count_failure,
                "received_at": datetime.now(UTC).isoformat(),
            },
        )
        count_stop = ledger.receive_count(count_raw, count_failure)
        if count_stop is not None:
            current.stop = stop = count_stop
        phase = "count-result"
        journal.append(
            "COUNT_RESULT",
            {"snapshot": current.snapshot(), "ledger": ledger.snapshot(), "stop": count_stop},
        )
        if count_stop is not None:
            return False
        if plan["scope"] == "count-only":
            current.stop = stop = "COUNT_ONLY_COMPLETE"
            journal.append(
                "COUNT_ONLY_COMPLETE", {"snapshot": current.snapshot(), "ledger": ledger.snapshot()}
            )
            return False
        authorize_network(plan, authorization, "generate")
        if not mock:
            validate_plan(plan, current=True)
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
            initial = CountedSession(
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
            prepared_baselines.update(
                {s.kernel.manifest.run_id: s.first_move_policies for s in sessions.values()}
            )
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
                "count_send_attempts": count_calls,
                "network_send_attempts": calls + count_calls,
                "ledger": ledger.snapshot(),
                "summary": summary(prefixes, branches, plan, prepared_baselines),
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
    from journeymap.experiments.route_update_counted_audit import audit_directory

    return audit_directory(directory, expected_plan_sha256=cast(str, plan["plan_sha256"]))
