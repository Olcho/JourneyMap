"""E04-specific immutable candidate plans; readiness never implies authorization."""

from itertools import permutations
from pathlib import Path
from typing import cast

from journeymap.adapters.llm import Record
from journeymap.adapters.route_update import PROFILE, SCHEMA, obj
from journeymap.adapters.route_update_live import CONFIG, CONFIG_VERSION, WIRE_VERSION
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.experiments.pickup_cue_live_plan import source_identity
from journeymap.experiments.pilot_journal import digest
from journeymap.experiments.provenance import runtime_identity
from journeymap.experiments.route_update_live_cost import COUNTER_REVIEW, RATES
from journeymap.scenarios.route_update import CONDITIONS, VERSION

PROTOCOL = "experiment04-responses-execution-1"
EXPORT = "experiment04-responses-export-1"
AUDIT = "experiment04-responses-audit-1"
REVIEW: JsonObject = {
    "checked_date_kst": "2026-10-09",
    "model_id": "gpt-6.1-sol",
    "low_and_structured_outputs": "officially documented",
    "snapshot_id": None,
    "account_access_and_e04_schema_acceptance": "UNTESTED; future authorized smoke",
    "sources": [
        "https://developers.openai.com/api/docs/models/gpt-6.1-sol",
        "https://developers.openai.com/api/docs/pricing",
        "https://developers.openai.com/api/docs/guides/reasoning",
        "https://developers.openai.com/api/docs/guides/structured-outputs",
        "https://developers.openai.com/api/docs/guides/token-counting",
    ],
}


def allocation(stage: str) -> list[JsonValue]:
    if stage == "smoke":
        return [{"prefix_id": "smoke", "order": []}]
    if stage != "pilot":
        raise ValueError("E04 smoke or pilot stage required")
    return [
        {"prefix_id": f"p{i:03d}", "order": list(order)}
        for i, order in enumerate(permutations(CONDITIONS), 1)
    ]


def make_plan(
    stage: str,
    mode: str,
    output: Path | str,
    *,
    saved_source: JsonObject | None = None,
    saved_runtime: JsonObject | None = None,
) -> JsonObject:
    if mode not in ("live", "recording-mock"):
        raise ValueError("E04 mode required")
    if type(output) is str and not output:
        raise ValueError("E04 nonempty saved provenance path required")
    rows = allocation(stage)
    cap = 1 if stage == "smoke" else 114
    value: JsonObject = {
        "protocol": PROTOCOL,
        "export_version": EXPORT,
        "audit_version": AUDIT,
        "configuration_version": CONFIG_VERSION,
        "wire_version": WIRE_VERSION,
        "scenario_version": VERSION,
        "profile_version": PROFILE.version,
        "instructions_sha256": digest({"instructions": PROFILE.instructions}),
        "schema_sha256": digest(SCHEMA),
        "config": CONFIG,
        "mode": mode,
        "stage": stage,
        "output_path": output if isinstance(output, str) else str(output.resolve()),
        "source": source_identity() if saved_source is None else saved_source,
        "runtime": runtime_identity() if saved_runtime is None else saved_runtime,
        "state": "DRAFT" if mode == "live" else "READY",
        "readiness": "INCOMPLETE_TOKEN_BOUND" if mode == "live" else "MOCK_ONLY",
        "authorization": {"status": "UNAUTHORIZED", "approved_usd": None},
        "prefix_count": len(rows),
        "allocation": rows,
        "call_cap": cap,
        "engine_seed": 42,
        "max_decisions": 6,
        "max_elapsed_ticks": 24,
        "order_kind": "fixed balanced; not random",
        "model_support_review": REVIEW,
        "rates": RATES,
        "counter_review": COUNTER_REVIEW
        if mode == "live"
        else {
            "status": "SYNTHETIC_MOCK_ONLY",
            "method": "synthetic-mock-only-1",
            "input_bound": 20000,
            "scope": "mock contract only; no service token evidence",
        },
        "budget_review": {
            "status": "PROPOSED",
            "combined_proposed_usd": "10.00",
            "stage_reservation_cap_usd": "0.07048" if stage == "smoke" else "8.03472",
            "conditional_per_call_usd": "0.07048",
            "conditional_combined_115_usd": "8.10520",
            "approved_usd": None,
            "cache_savings_assumed": False,
            "excludes": "tax, FX, regional premium, special account contracts",
        },
        "automatic_retry": False,
        "fallback": False,
        "resume": False,
        "replace_failed_prefix": False,
        "reuse_smoke_or_prior_response": False,
        "global_stop": [
            "infrastructure",
            "integrity",
            "recording",
            "usage/cost failure",
            "input/budget/call limit before send",
            "max_output_tokens incomplete",
        ],
    }
    value = Record.capture(value).data
    value["plan_sha256"] = digest(value)
    return value


def validate_plan(plan: JsonObject, *, current: bool = False) -> None:
    if plan.get("plan_sha256") != digest({k: v for k, v in plan.items() if k != "plan_sha256"}):
        raise ValueError("E04 plan digest mismatch")
    expected = make_plan(
        cast(str, plan.get("stage")),
        cast(str, plan.get("mode")),
        Path(cast(str, plan.get("output_path"))) if current else cast(str, plan.get("output_path")),
        saved_source=None if current else obj(plan["source"]),
        saved_runtime=None if current else obj(plan["runtime"]),
    )
    if not current:
        # Pure audit contract binding: saved software identity is checked externally.
        expected["source"], expected["runtime"] = plan["source"], plan["runtime"]
        expected["plan_sha256"] = digest({k: v for k, v in expected.items() if k != "plan_sha256"})
    if canonical_json(expected) != canonical_json(plan):
        raise ValueError("E04 source/profile/configuration/plan contract mismatch")


def preflight(plan: JsonObject, output: Path, authorization: JsonObject | None = None) -> None:
    validate_plan(plan, current=True)
    if (
        output.exists()
        or not output.parent.is_dir()
        or str(output.resolve()) != plan["output_path"]
    ):
        raise ValueError("E04 new exact planned output required")
    if plan["mode"] == "recording-mock":
        if authorization is not None:
            raise ValueError("E04 mock/live authorization mixing")
        return
    if authorization is None:
        raise ValueError("E04 explicit human authorization missing")
    if (
        authorization.get("plan_sha256") != plan["plan_sha256"]
        or authorization.get("stage") != plan["stage"]
        or authorization.get("call_cap") != plan["call_cap"]
        or authorization.get("output_path") != plan["output_path"]
        or authorization.get("mode") != "live"
        or authorization.get("status") != "HUMAN_APPROVED"
        or authorization.get("approved_usd")
        != obj(plan["budget_review"])["stage_reservation_cap_usd"]
    ):
        raise ValueError("E04 exact stage/digest/call/budget/output authorization required")
    # A JSON edit cannot manufacture a verified counter. No LIVE READY issuer exists yet.
    raise ValueError(
        "E04 TOKEN_COUNTER_UNVERIFIED: no live READY; authorization cannot bypass readiness"
    )
