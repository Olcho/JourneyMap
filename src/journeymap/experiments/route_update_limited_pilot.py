"""Explicit v4 pilot-only UNKNOWN fee contract; service evidence is not send approval."""

from journeymap.adapters.route_update import obj
from journeymap.adapters.route_update_count import COUNT_ENDPOINT
from journeymap.adapters.route_update_live import CONFIG, ENDPOINT
from journeymap.core.canonical import JsonObject, canonical_json
from journeymap.experiments.pilot_journal import digest
from journeymap.experiments.route_update_live_plan import allocation
from journeymap.experiments.route_update_pilot_compatibility import validate_compatibility

POLICY = "PILOT_COUNT_FEE_UNKNOWN_V1"
PROTOCOL = "experiment04-responses-execution-4"
EXPORT = "experiment04-responses-export-4"
AUDIT = "experiment04-responses-audit-4"
COST: JsonObject = {
    "cost_policy": POLICY,
    "count_price_status": "UNKNOWN",
    "count_fee_upper_bound_usd": None,
    "generation_reservation_cap_usd": "8.03472",
    "combined_cost_upper_bound_usd": None,
    "total_cost_bound_guaranteed": False,
}


def limited_plan(
    value: JsonObject, review: JsonObject | None, compatibility: JsonObject | None
) -> JsonObject:
    live = value["mode"] == "live"
    if live:
        if review is None or compatibility is None:
            raise ValueError("E04 v4 pilot requires actual smoke and compatibility evidence")
        validate_compatibility(compatibility, review, obj(value["source"]), obj(value["runtime"]))
    elif review is not None or compatibility is not None:
        raise ValueError("E04 mock fixtures cannot establish production service evidence")
    value.update(
        {
            **COST,
            "protocol": PROTOCOL,
            "export_version": EXPORT,
            "audit_version": AUDIT,
            "state": "READY_FOR_REVIEW" if live else "READY",
            "service_review": review,
            "service_compatibility": compatibility,
            "service_witness_sha256": review["witness_sha256"] if review else None,
            "service_compatibility_sha256": compatibility["record_sha256"]
            if compatibility
            else None,
            "readiness": {
                "implementation_contract": "VALIDATED_BY_OFFLINE_MOCK",
                "service_evidence": "RECORDED_ACTUAL_V3_SMOKE"
                if live
                else "MOCK_NOT_SERVICE_EVIDENCE",
                "source_compatibility": "VALIDATED_V3_TO_V4_UNSIGNED"
                if live
                else "NOT_SERVICE_EVIDENCE",
                "count_price": "UNKNOWN",
                "send_authorization": "PENDING_USER_APPROVAL",
                "meaning": "Reviewable only; no send permission or total cost guarantee",
            },
            "count_price_review": {
                "status": "UNKNOWN",
                "upper_bound_usd": None,
                "reason": "Explicit pilot exception; not price confirmation",
            },
            "budget_review": {
                **COST,
                "status": "PENDING_USER_APPROVAL_GENERATION_SUBTOTAL_ONLY",
                "approved_usd": None,
                "count_charge_estimate_usd": None,
                "generation_rates_basis": "114 * 0.07048; input 2.50/output 10.00 USD per million",
                "prior_smoke_included": False,
                "combined_10_usd_guarantee": False,
            },
        }
    )
    validate_scope(value)
    value["plan_sha256"] = digest({k: v for k, v in value.items() if k != "plan_sha256"})
    return value


def validate_scope(plan: JsonObject) -> None:
    expected: JsonObject = {
        **COST,
        "protocol": PROTOCOL,
        "export_version": EXPORT,
        "audit_version": AUDIT,
        "stage": "pilot",
        "scope": "count-and-generate",
        "prefix_count": 6,
        "allocation": allocation("pilot"),
        "call_cap": 114,
        "generation_cap": 114,
        "count_cap": 114,
        "network_cap": 228,
        "config": CONFIG,
        "count_endpoint": COUNT_ENDPOINT,
        "generation_endpoint": ENDPOINT,
        "count_timeout_seconds": 120,
        "count_max_response_bytes": 2000000,
        "automatic_retry": False,
        "fallback": False,
        "resume": False,
        "replace_failed_prefix": False,
        "reuse_smoke_or_prior_response": False,
        "engine_seed": 42,
        "max_decisions": 6,
        "max_elapsed_ticks": 24,
    }
    if canonical_json({k: plan.get(k) for k in expected}) != canonical_json(expected):
        raise ValueError("E04 v4 requires exact pilot 6 prefixes / 114/114/228 contract")
    if plan.get("mode") not in ("live", "recording-mock"):
        raise ValueError("E04 v4 explicit mode required")


def authorization_template(plan: JsonObject) -> JsonObject:
    validate_scope(plan)
    return {
        **COST,
        "status": "PENDING_USER_APPROVAL",
        "mode": "live",
        "stage": "pilot",
        "scope": "count-and-generate",
        "protocol": PROTOCOL,
        "plan_sha256": plan["plan_sha256"],
        "output_path": plan["output_path"],
        "prefix_count": 6,
        "allocation": plan["allocation"],
        "count_cap": 114,
        "generation_cap": 114,
        "network_cap": 228,
        "endpoints": [COUNT_ENDPOINT, ENDPOINT],
        "accept_count_fee_unknown": False,
        "automatic_retry": False,
        "fallback": False,
        "resume": False,
        "replace_failed_prefix": False,
        "additional_execution_allowed": False,
    }


def authorize(plan: JsonObject, authorization: JsonObject | None) -> None:
    validate_scope(plan)
    if plan["mode"] == "recording-mock":
        if authorization is not None:
            raise ValueError("E04 v4 mock/live approval mixing")
        return
    expected = authorization_template(plan)
    expected.update(status="HUMAN_APPROVED", accept_count_fee_unknown=True)
    if authorization is None or canonical_json(authorization) != canonical_json(expected):
        raise ValueError("E04 v4 exact HUMAN_APPROVED pilot UNKNOWN-fee acceptance required")
    if canonical_json(plan["authorization"]) != canonical_json(
        {"status": "UNAUTHORIZED", "approved_usd": None}
    ):
        raise ValueError("E04 frozen pilot approval must remain separate")
