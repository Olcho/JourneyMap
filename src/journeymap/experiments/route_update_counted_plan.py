"""E04 counted protocol: implementation readiness and unverified service/pricing separate."""

from decimal import Decimal
from pathlib import Path
from typing import cast

from journeymap.adapters.route_update import obj
from journeymap.adapters.route_update_count import COUNT_ENDPOINT, COUNT_PROJECTION, COUNT_VERSION
from journeymap.adapters.route_update_live import ENDPOINT
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.experiments.pilot_journal import digest
from journeymap.experiments.route_update_live_plan import make_plan as legacy_plan

PROTOCOL = "experiment04-responses-execution-2"
EXPORT = "experiment04-responses-export-2"
AUDIT = "experiment04-responses-audit-2"
COUNT_PRICE: JsonObject = {
    "status": "UNKNOWN",
    "per_count_max_usd": None,
    "source": "https://developers.openai.com/api/docs/pricing",
    "checked_date_kst": "2026-10-09",
    "separate_count_endpoint_fee_not_established": True,
}


def make_plan(
    stage: str,
    mode: str,
    output: Path | str,
    *,
    saved_source: JsonObject | None = None,
    saved_runtime: JsonObject | None = None,
    scope: str = "count-and-generate",
    price_review: JsonObject | None = None,
    service_review: JsonObject | None = None,
    cost_policy: str | None = None,
    compatibility: JsonObject | None = None,
) -> JsonObject:
    if scope not in ("count-only", "count-and-generate") or (
        scope == "count-only" and stage != "smoke"
    ):
        raise ValueError("E04 count-only limited to smoke stage")
    from journeymap.experiments.route_update_limited_pilot import POLICY as PILOT_POLICY
    from journeymap.experiments.route_update_limited_smoke import POLICY

    if cost_policy == PILOT_POLICY:
        if stage != "pilot" or scope != "count-and-generate" or price_review is not None:
            raise ValueError("E04 v4 is explicit pilot/count-and-generate only")
        from journeymap.experiments.route_update_limited_pilot import limited_plan as pilot_plan

        base = make_plan(
            stage, mode, output, saved_source=saved_source, saved_runtime=saved_runtime
        )
        return pilot_plan(base, service_review, compatibility)
    if compatibility is not None:
        raise ValueError("E04 compatibility evidence is v4 pilot only")
    if cost_policy is not None and (
        cost_policy != POLICY
        or stage != "smoke"
        or scope != "count-and-generate"
        or price_review is not None
        or service_review is not None
    ):
        raise ValueError("E04 UNKNOWN count fee policy is explicit smoke-only; no review mixing")
    fee = None
    if price_review is not None:
        fee = reviewed_count_fee(price_review)
    if service_review is not None:
        validate_service_review(service_review)
    value = legacy_plan(stage, mode, output, saved_source=saved_source, saved_runtime=saved_runtime)
    cap = cast(int, value["call_cap"])
    generation_cap = cap if scope == "count-and-generate" else 0
    value.update(
        {
            "protocol": PROTOCOL,
            "export_version": EXPORT,
            "audit_version": AUDIT,
            "count_configuration_version": COUNT_VERSION,
            "count_projection": COUNT_PROJECTION,
            "scope": scope,
            "generation_cap": generation_cap,
            "count_cap": cap,
            "network_cap": cap + generation_cap,
            "call_cap": generation_cap,
            "count_timeout_seconds": 120,
            "count_max_response_bytes": 2000000,
            "count_endpoint": COUNT_ENDPOINT,
            "generation_endpoint": ENDPOINT if generation_cap else None,
            "state": "DRAFT" if mode == "live" else "READY",
            "readiness": {
                "implementation_contract": "VALIDATED_BY_OFFLINE_MOCK",
                "service_confirmation": "UNVERIFIED",
                "count_price": "UNKNOWN" if mode == "live" else "SYNTHETIC_MOCK_ONLY",
                "live_blockers": [
                    "count price/bound evidence",
                    "authorized smoke schema/reasoning/count-vs-usage confirmation",
                ],
            },
            "counter_review": {
                "method": COUNT_VERSION,
                "input_cap_tokens": 20000,
                "service_confirmation": "UNVERIFIED",
                "reuse_or_retry": False,
                "mock_is_not_service_evidence": True,
            },
            "count_price_review": (price_review or COUNT_PRICE)
            if mode == "live"
            else {
                "status": "SYNTHETIC_MOCK_ONLY",
                "per_count_max_usd": "0.00010",
                "actual_count_fee": "UNKNOWN",
            },
            "budget_review": {
                "status": "PROPOSED",
                "combined_proposed_usd": "10.00",
                "approved_usd": None,
                "conditional_generation_115_usd": "8.10520",
                "count_price": "UNKNOWN",
                "combined_network_total_usd": None,
                "stage_reservation_cap_usd": None
                if mode == "live"
                else (
                    "0.00010"
                    if not generation_cap
                    else "0.07058"
                    if stage == "smoke"
                    else "8.04612"
                ),
                "mock_reservation_not_count_price_evidence": True,
                "requires_external_review_before_live": True,
            },
            "code_baseline_scope": (
                "tick-3 first semantic input only; policy-computed differs from model-called"
            ),
        }
    )
    if service_review is not None:
        smoke_plan = obj(obj(service_review["witness"])["plan"])
        if smoke_plan["source"] != value["source"] or smoke_plan["runtime"] != value["runtime"]:
            raise ValueError("E04 smoke witness source/runtime mismatch")
    value["service_review"] = service_review
    if mode == "live" and fee is not None:
        readiness = obj(value["readiness"])
        readiness["count_price"] = "EXTERNALLY_REVIEWED_BOUND"
        obj(value["budget_review"])["count_price"] = "EXTERNALLY_REVIEWED_BOUND"
        monetary_cap = Decimal(generation_cap) * Decimal("0.07048") + Decimal(cap) * fee
        obj(value["budget_review"])["stage_reservation_cap_usd"] = str(monetary_cap)
        if stage == "smoke":
            value["state"] = "READY_SERVICE_CHECK"
            readiness["live_blockers"] = ["human authorization; service acceptance is being tested"]
        elif service_review is not None:
            value["state"] = "READY"
            readiness["service_confirmation"] = "RECORDED_LIVE_SMOKE; unsigned evidence"
            readiness["live_blockers"] = ["human authorization"]
    value["global_stop"] = [
        *cast(list[JsonValue], value["global_stop"]),
        "count failure/invalid/over-limit",
        "count-generation wire drift",
        "count/returned usage mismatch",
    ]
    value["plan_sha256"] = digest({k: v for k, v in value.items() if k != "plan_sha256"})
    if cost_policy is not None:
        from journeymap.experiments.route_update_limited_smoke import limited_plan

        return limited_plan(value)
    return value


def validate_plan(plan: JsonObject, *, current: bool = False) -> None:
    if plan.get("plan_sha256") != digest({k: v for k, v in plan.items() if k != "plan_sha256"}):
        raise ValueError("E04 counted plan digest mismatch")
    output = (
        Path(cast(str, plan.get("output_path"))) if current else cast(str, plan.get("output_path"))
    )
    expected = make_plan(
        cast(str, plan.get("stage")),
        cast(str, plan.get("mode")),
        output,
        saved_source=None if current else obj(plan["source"]),
        saved_runtime=None if current else obj(plan["runtime"]),
        scope=cast(str, plan["scope"]),
        price_review=obj(plan["count_price_review"])
        if obj(plan["count_price_review"])["status"] == "EXTERNALLY_REVIEWED_BOUND"
        else None,
        service_review=cast(JsonObject | None, plan["service_review"]),
        cost_policy=cast(str | None, plan.get("cost_policy")),
        compatibility=cast(JsonObject | None, plan.get("service_compatibility")),
    )
    if canonical_json(expected) != canonical_json(plan):
        raise ValueError("E04 counted source/config/profile/price/readiness/plan mismatch")


def authorize_network(plan: JsonObject, authorization: JsonObject | None, operation: str) -> None:
    if operation not in ("count", "generate"):
        raise ValueError("E04 unknown network operation")
    if operation == "generate" and not plan["generation_cap"]:
        raise ValueError("E04 count-only approval cannot authorize generation")
    from journeymap.experiments.route_update_limited_pilot import POLICY as PILOT_POLICY

    if plan.get("cost_policy") == PILOT_POLICY:
        from journeymap.experiments.route_update_limited_pilot import authorize as authorize_pilot

        authorize_pilot(plan, authorization)
        return
    if "cost_policy" in plan:
        from journeymap.experiments.route_update_limited_smoke import authorize

        authorize(plan, authorization)
        return
    if plan["mode"] == "recording-mock":
        if authorization is not None:
            raise ValueError("E04 counted mock/live authorization mixing")
        return
    if authorization is None:
        raise ValueError("E04 counted human authorization missing")
    expected: JsonObject = {
        "status": "HUMAN_APPROVED",
        "mode": "live",
        "plan_sha256": plan["plan_sha256"],
        "stage": plan["stage"],
        "scope": plan["scope"],
        "output_path": plan["output_path"],
        "count_cap": plan["count_cap"],
        "generation_cap": plan["generation_cap"],
        "network_cap": plan["network_cap"],
        "approved_usd": obj(plan["budget_review"])["stage_reservation_cap_usd"],
        "endpoints": [COUNT_ENDPOINT, ENDPOINT] if plan["generation_cap"] else [COUNT_ENDPOINT],
    }
    if authorization != expected or expected["approved_usd"] is None:
        raise ValueError(
            "E04 exact count/generation/network/endpoint/amount authorization required"
        )
    if operation == "generate" and (plan["scope"] == "count-only" or plan["generation_cap"] == 0):
        raise ValueError("E04 count-only approval cannot authorize generation")


def preflight(plan: JsonObject, output: Path, authorization: JsonObject | None = None) -> None:
    validate_plan(plan, current=True)
    if (
        output.exists()
        or not output.parent.is_dir()
        or str(output.resolve()) != plan["output_path"]
    ):
        raise ValueError("E04 counted new exact native output required")
    authorize_network(plan, authorization, "count")
    if plan["scope"] == "count-and-generate":
        authorize_network(plan, authorization, "generate")
    if "cost_policy" in plan:
        # Validated explicit v3 candidate; authorization has already accepted uncertainty.
        return
    if plan["mode"] == "live":
        readiness = obj(plan["readiness"])
        if (
            readiness["count_price"] != "EXTERNALLY_REVIEWED_BOUND"
            or obj(plan["budget_review"])["stage_reservation_cap_usd"] is None
        ):
            raise ValueError("E04 COUNT_PRICE_UNKNOWN; no live READY/monetary reservation")
        if plan["state"] not in ("READY", "READY_SERVICE_CHECK"):
            raise ValueError("E04 service-confirmation readiness not verified")


def reviewed_count_fee(review: JsonObject) -> Decimal:
    if (
        set(review)
        != {
            "status",
            "source",
            "checked_date_kst",
            "upper_bound_usd",
            "evidence_text",
            "evidence_sha256",
        }
        or review["status"] != "EXTERNALLY_REVIEWED_BOUND"
        or type(review["source"]) is not str
        or not review["source"].startswith("https://developers.openai.com/")
        or type(review["evidence_text"]) is not str
        or not review["evidence_text"]
        or review["evidence_sha256"] != digest({"text": review["evidence_text"]})
    ):
        raise ValueError("E04 external count-price review evidence required; not a status flag")
    if type(review["upper_bound_usd"]) is not str:
        raise ValueError("E04 count price decimal required")
    fee = Decimal(review["upper_bound_usd"])
    if not fee.is_finite() or fee < 0:
        raise ValueError("E04 count price bound invalid")
    return fee


def validate_service_review(review: JsonObject) -> None:
    from journeymap.experiments.route_update_counted_service import validate_review

    validate_review(review)
