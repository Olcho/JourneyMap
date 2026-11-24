"""Separate mode-bound sequential plan, six opportunities and total durable-intent cap."""

from typing import cast

from journeymap.adapters.llm import Record
from journeymap.adapters.pickup_sequential import (
    CONFIGURATION_VERSION,
    INSTRUCTIONS_SHA256,
    PROFILE,
    WIRE_VERSION,
)
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.experiments.memory_horizon_audit import obj, require
from journeymap.experiments.pickup_cue_audit import same
from journeymap.experiments.pickup_cue_live_plan import allocations as one_shot_allocations
from journeymap.experiments.pickup_cue_live_plan import (
    mock_config,
    source_identity,
    validate_mode_config,
)
from journeymap.experiments.pilot_journal import digest
from journeymap.experiments.provenance import runtime_identity

PLAN_VERSION = "pickup-sequential-plan-1"
PROTOCOL_VERSION = "pickup-sequential-execution-1"
EXPORT_VERSION = "pickup-sequential-export-1"
AUDIT_VERSION = "pickup-sequential-audit-1"
METRICS_VERSION = "pickup-sequential-arrival-cost-1"
CONDITIONS = ("no-event-memory", "recency-k1", "recency-k2", "recency-k3", "full-history")
MEMORY_SEMANTICS: JsonObject = {
    "no-event-memory": "no-event-memory-1",
    "recency-k1": "recency-event-memory-k1-1",
    "recency-k2": "recency-event-memory-k2-1",
    "recency-k3": "recency-event-memory-k3-1",
    "full-history": "pickup-sequential-full-history-1",
}


def allocations(seed: int, count: int) -> list[JsonValue]:
    result = one_shot_allocations(seed, count)
    for entry in result:
        item = obj(entry)
        if item["condition"] == "full-prefix":
            item["condition"] = "full-history"
    return result


def make_plan(
    *,
    config: JsonObject | None = None,
    mode: str = "recording-mock",
    engine_seed: int = 42,
    allocation_seed: int = 6,
    allocation_count: int = 40,
    call_cap: int | None = None,
    reviewed: bool = False,
) -> JsonObject:
    config = mock_config() if config is None and mode == "recording-mock" else config
    if config is None:
        raise ValueError("explicit candidate live config required")
    plan: JsonObject = {
        "plan_version": PLAN_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "export_version": EXPORT_VERSION,
        "audit_version": AUDIT_VERSION,
        "metrics_version": METRICS_VERSION,
        "configuration_version": CONFIGURATION_VERSION,
        "wire_schema_version": WIRE_VERSION,
        "profile_version": PROFILE.version,
        "instructions_sha256": INSTRUCTIONS_SHA256,
        "memory_semantics": MEMORY_SEMANTICS,
        "mode": mode,
        "state": "READY" if mode == "recording-mock" or reviewed else "DRAFT",
        "source": source_identity(),
        "runtime": runtime_identity(),
        "config": config,
        "engine_seed": engine_seed,
        "allocation_seed": allocation_seed,
        "allocation_count": allocation_count,
        "allocations": allocations(allocation_seed, allocation_count),
        "max_decisions": 6,
        "call_cap": allocation_count * 6 if call_cap is None else call_cap,
        "tick_limit": None,
        "stop_policy": "first-infrastructure-recording-integrity",
        "response_failure_policy": "terminate-case-no-recall",
        "retry": False,
        "replacement": False,
        "resume": False,
    }
    plan = Record.capture(plan).data
    plan["plan_sha256"] = digest(plan)
    validate_plan(plan)
    return plan


def validate_plan(plan: JsonObject, *, execution: bool = False) -> None:
    require(
        set(plan)
        == {
            "plan_version",
            "protocol_version",
            "export_version",
            "audit_version",
            "metrics_version",
            "configuration_version",
            "wire_schema_version",
            "profile_version",
            "instructions_sha256",
            "memory_semantics",
            "mode",
            "state",
            "source",
            "runtime",
            "config",
            "engine_seed",
            "allocation_seed",
            "allocation_count",
            "allocations",
            "max_decisions",
            "call_cap",
            "tick_limit",
            "stop_policy",
            "response_failure_policy",
            "retry",
            "replacement",
            "resume",
            "plan_sha256",
        },
        "sequential plan inventory",
    )
    for key, value in (
        ("plan_version", PLAN_VERSION),
        ("protocol_version", PROTOCOL_VERSION),
        ("export_version", EXPORT_VERSION),
        ("audit_version", AUDIT_VERSION),
        ("metrics_version", METRICS_VERSION),
        ("configuration_version", CONFIGURATION_VERSION),
        ("wire_schema_version", WIRE_VERSION),
        ("profile_version", PROFILE.version),
        ("instructions_sha256", INSTRUCTIONS_SHA256),
        ("memory_semantics", MEMORY_SEMANTICS),
        ("max_decisions", 6),
        ("tick_limit", None),
        ("stop_policy", "first-infrastructure-recording-integrity"),
        ("response_failure_policy", "terminate-case-no-recall"),
        ("retry", False),
        ("replacement", False),
        ("resume", False),
    ):
        same(plan[key], value, key)
    require(
        plan["plan_sha256"] == digest({k: v for k, v in plan.items() if k != "plan_sha256"}),
        "plan digest",
    )
    require(plan["state"] in ("DRAFT", "READY"), "state")
    for field in (
        "engine_seed",
        "allocation_seed",
        "allocation_count",
        "call_cap",
        "max_decisions",
    ):
        require(type(plan[field]) is int, "integer limits")
    count = cast(int, plan["allocation_count"])
    require(1 <= count <= 40 and 1 <= cast(int, plan["call_cap"]) <= count * 6, "call cap")
    same(
        plan["allocations"],
        allocations(cast(int, plan["allocation_seed"]), count),
        "frozen allocations",
    )
    validate_mode_config(cast(str, plan["mode"]), obj(plan["config"]), ready=True)
    source, runtime = obj(plan["source"]), obj(plan["runtime"])
    require(source.get("source_identity_version") == "utf8-lf-path-lengths-1", "source identity")
    for field, length in (("git_commit", 40), ("working_source_sha256", 64)):
        source_value = source.get(field)
        require(
            type(source_value) is str
            and len(source_value) == length
            and all(c in "0123456789abcdef" for c in source_value),
            "source digest",
        )
    require(
        "branch" in source
        and (
            source["branch"] is None or (type(source["branch"]) is str and bool(source["branch"]))
        ),
        "branch",
    )
    require(
        bool(runtime.get("implementation")) and isinstance(runtime.get("version"), list), "runtime"
    )
    if plan["mode"] == "live" and plan["state"] == "READY":
        require(
            not cast(str, obj(plan["config"])["budget_review"]).startswith("PENDING"),
            "new budget review required",
        )
    if execution:
        require(plan["state"] == "READY", "draft cannot execute")
        same(source, source_identity(), "execution source drift")
        same(runtime, runtime_identity(), "execution runtime drift")
