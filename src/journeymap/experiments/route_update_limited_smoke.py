"""Explicit smoke-only UNKNOWN count fee policy; no API/model/request changes."""

from journeymap.adapters.route_update import obj
from journeymap.adapters.route_update_count import COUNT_ENDPOINT
from journeymap.adapters.route_update_live import CONFIG, ENDPOINT
from journeymap.core.canonical import JsonObject, canonical_json
from journeymap.experiments.pilot_journal import digest

POLICY = "SMOKE_COUNT_FEE_UNKNOWN_V1"
PROTOCOL = "experiment04-responses-execution-3"
EXPORT = "experiment04-responses-export-3"
AUDIT = "experiment04-responses-audit-3"
COST: JsonObject = {
    "cost_policy": POLICY,
    "count_price_status": "UNKNOWN",
    "count_fee_upper_bound_usd": None,
    "generation_reservation_cap_usd": "0.07048",
    "combined_cost_upper_bound_usd": None,
    "total_cost_bound_guaranteed": False,
}


def limited_plan(value: JsonObject) -> JsonObject:
    updates: JsonObject = {
        **COST,
        "protocol": PROTOCOL,
        "export_version": EXPORT,
        "audit_version": AUDIT,
        "state": "READY_SERVICE_CHECK" if value["mode"] == "live" else "READY",
        "readiness": {
            "implementation_contract": "VALIDATED_BY_OFFLINE_MOCK",
            "service_confirmation": "UNVERIFIED",
            "count_price": "UNKNOWN",
            "meaning": "Reviewable candidate only; final send authorization still required",
            "live_blockers": ["exact human approval accepting unknown count fee; no total bound"],
        },
        "count_price_review": {
            "status": "UNKNOWN",
            "upper_bound_usd": None,
            "reason": "Explicit limited smoke exception; not a price confirmation",
        },
        "budget_review": {
            **COST,
            "status": "UNAUTHORIZED_CONDITIONAL_GENERATION_ONLY",
            "approved_usd": None,
            "count_charge_estimate_usd": None,
            "generation_rates_basis": (
                "reviewed conservative input 2.50/output 10.00 USD per million"
            ),
            "pilot_budget_reused": False,
        },
    }
    value.update(updates)
    validate_scope(value)
    value["plan_sha256"] = digest({k: v for k, v in value.items() if k != "plan_sha256"})
    return value


def validate_scope(plan: JsonObject) -> None:
    expected: JsonObject = {
        **COST,
        "protocol": PROTOCOL,
        "export_version": EXPORT,
        "audit_version": AUDIT,
        "stage": "smoke",
        "scope": "count-and-generate",
        "prefix_count": 1,
        "count_cap": 1,
        "generation_cap": 1,
        "network_cap": 2,
        "call_cap": 1,
        "allocation": [{"prefix_id": "smoke", "order": []}],
        "config": CONFIG,
        "count_endpoint": COUNT_ENDPOINT,
        "generation_endpoint": ENDPOINT,
        "automatic_retry": False,
        "fallback": False,
        "resume": False,
        "engine_seed": 42,
    }
    if canonical_json({k: plan.get(k) for k in expected}) != canonical_json(expected):
        raise ValueError("E04 UNKNOWN count fee exception requires exact smoke 1/1/2 contract")
    if plan.get("mode") not in ("live", "recording-mock") or plan.get("service_review") is not None:
        raise ValueError("E04 limited smoke mode/service-review mismatch")


def authorization_template(plan: JsonObject) -> JsonObject:
    validate_scope(plan)
    return {
        **COST,
        "status": "PENDING_USER_APPROVAL",
        "mode": "live",
        "plan_sha256": plan["plan_sha256"],
        "stage": "smoke",
        "scope": "count-and-generate",
        "output_path": plan["output_path"],
        "count_cap": 1,
        "generation_cap": 1,
        "network_cap": 2,
        "endpoints": [COUNT_ENDPOINT, ENDPOINT],
        "accept_count_fee_unknown": False,
        "automatic_retry": False,
        "pilot_allowed": False,
        "additional_execution_allowed": False,
    }


def authorize(plan: JsonObject, authorization: JsonObject | None) -> None:
    validate_scope(plan)
    if plan["mode"] == "recording-mock":
        if authorization is not None:
            raise ValueError("E04 limited mock/live authorization mixing")
        return
    expected = authorization_template(plan)
    expected.update({"status": "HUMAN_APPROVED", "accept_count_fee_unknown": True})
    if authorization is None or canonical_json(authorization) != canonical_json(expected):
        raise ValueError("E04 exact HUMAN_APPROVED smoke UNKNOWN-fee acceptance required")
    # Digest binds source/runtime/config; bool/int/null strictness comes from canonical JSON.
    if obj(plan["authorization"])["status"] != "UNAUTHORIZED":
        raise ValueError("E04 frozen candidate authorization must remain separate")
