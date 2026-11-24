"""Unsigned saved live-smoke witness. Replay consistency is not provider authentication."""

from pathlib import Path
from typing import cast

from journeymap.adapters.route_update import obj
from journeymap.adapters.route_update_count import count_wire
from journeymap.core.canonical import JsonObject
from journeymap.experiments.pickup_cue_live_audit import read_json
from journeymap.experiments.pilot_journal import digest
from journeymap.experiments.route_update_audit import same


def validate_review(review: JsonObject) -> None:
    from journeymap.experiments.route_update_counted import complete
    from journeymap.experiments.route_update_counted_cost import ledger_for_plan
    from journeymap.experiments.route_update_counted_plan import authorize_network, validate_plan
    from journeymap.experiments.route_update_counted_session import CountedSession

    plan = obj(obj(review.get("witness"))["plan"])
    limited = plan.get("protocol") == "experiment04-responses-execution-3"
    expected_keys = {"status", "witness", "witness_sha256", "authenticity_limit"}
    if limited:
        from journeymap.experiments.route_update_limited_smoke import COST

        expected_keys.add("cost_review")
        same(review.get("cost_review"), COST)
    if (
        set(review) != expected_keys
        or review["status"] != "RECORDED_LIVE_SMOKE"
        or review["authenticity_limit"]
        != "UNSIGNED_INTERNAL_CONSISTENCY; not proof of service facts"
        or review["witness_sha256"] != digest(obj(review["witness"]))
    ):
        raise ValueError("E04 saved raw smoke witness required; status flag is not evidence")
    witness = obj(review["witness"])
    if set(witness) != {
        "plan",
        "authorization",
        "prepared",
        "count_response_raw",
        "generation_response_raw",
        "result",
        "ledger",
    }:
        raise ValueError("E04 service witness inventory")
    plan = obj(witness["plan"])
    validate_plan(plan)
    if (
        plan["mode"] != "live"
        or plan["stage"] != "smoke"
        or plan["scope"] != "count-and-generate"
        or plan["service_review"] is not None
    ):
        raise ValueError("E04 actual-mode separate smoke witness required")
    authorize_network(plan, obj(witness["authorization"]), "generate")
    ledger = ledger_for_plan(plan)
    session = CountedSession("smoke:initial", obj(plan["config"]), "live")
    try:
        prepared = session.prepare(initial=True)
        same(witness["prepared"], prepared)
        counted = count_wire(
            obj(prepared["wire"]), obj(prepared["semantic_input"]), obj(plan["config"])
        )
        ledger.begin_count(counted, "live")
        ledger.counts[-1]["sent"] = "ATTEMPTED_CHARGE_UNKNOWN"
        if ledger.receive_count(cast(str, witness["count_response_raw"]), None) is not None:
            raise ValueError("E04 service count not valid")
        ledger.reserve(obj(prepared["wire"]), "live")
        ledger.entries[-1]["sent"] = "ATTEMPTED_CHARGE_UNKNOWN"
        result = complete(
            session, ledger, cast(str, witness["generation_response_raw"]), None, initial=True
        )
        if obj(result["prefix"])["response_status"] != "VALID":
            raise ValueError("E04 service action/schema acceptance not established")
        if ledger.entries[-1].get("usage_count_mismatch") is not None:
            raise ValueError("E04 service count/usage does not agree")
        same(witness["result"], result)
        same(witness["ledger"], ledger.snapshot())
        # The same live request schema/reasoning projection was sent, count/usage agrees.
    finally:
        session.close()


def review_smoke(directory: Path) -> JsonObject:
    from journeymap.experiments.route_update_counted_audit import audit_directory

    report = audit_directory(directory)
    if (
        report.get("status") != "INCLUDED"
        or report.get("mode") != "live"
        or report.get("stage") != "smoke"
        or report.get("send_attempts") != 1
        or report.get("count_send_attempts") != 1
        or report.get("stop") != "COMPLETE"
    ):
        raise ValueError("E04 complete saved live smoke required; mock cannot confirm service")
    ledger = obj(report["ledger"])
    prefix = cast(list[JsonObject], report["prefixes"])[0]
    # Capture the original INTENT, not a result mutated by submission.
    from journeymap.experiments.pilot_journal import inspect_journal

    journal = inspect_journal(directory / "attempts.jsonl")
    intent = next(obj(row["data"]) for row in journal.rows if row["kind"] == "INTENT")
    prepared = obj(intent["prepared"])
    witness: JsonObject = {
        "plan": read_json(directory / "plan.json"),
        "authorization": read_json(directory / "authorization.json"),
        "prepared": prepared,
        "count_response_raw": (directory / "count-response-001.utf8").read_text(encoding="utf-8"),
        "generation_response_raw": (directory / "response-001.utf8").read_text(encoding="utf-8"),
        "result": prefix,
        "ledger": ledger,
    }
    result: JsonObject = {
        "status": "RECORDED_LIVE_SMOKE",
        "witness": witness,
        "witness_sha256": digest(witness),
        "authenticity_limit": "UNSIGNED_INTERNAL_CONSISTENCY; not proof of service facts",
    }
    if obj(witness["plan"]).get("protocol") == "experiment04-responses-execution-3":
        from journeymap.experiments.route_update_limited_smoke import COST

        result["cost_review"] = dict(COST)
    validate_review(result)
    return result
