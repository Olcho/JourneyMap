"""Frozen allocation/configuration contract; unresolved drafts cannot call providers."""

import random
import subprocess
from pathlib import Path
from typing import cast

from journeymap.adapters.pickup_cue_live import CONFIGURATION_VERSION, WIRE_VERSION, validate_config
from journeymap.adapters.pickup_cue_prompt import INSTRUCTIONS_SHA256, PROFILE
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.experiments.memory_horizon_audit import obj, require
from journeymap.experiments.pickup_cue import CONDITIONS
from journeymap.experiments.pickup_cue_audit import same
from journeymap.experiments.pilot_journal import digest
from journeymap.experiments.provenance import code_identity, runtime_identity
from journeymap.scenarios.alderwick.pickup_cue import SCENARIO_VERSION, TARGETS

LEGACY_PLAN_VERSION = "pickup-cue-execution-plan-1"
PLAN_VERSION = "pickup-cue-execution-plan-2"
PROTOCOL_VERSION = "pickup-cue-live-execution-2"
EXPORT_VERSION = "pickup-cue-live-export-2"
AUDIT_VERSION = "pickup-cue-live-audit-2"
MODES = ("recording-mock", "live")


def validate_mode_config(mode: str, config: JsonObject, *, ready: bool) -> None:
    """Pure checks shared by execution and audit; no source/runtime/key/transport access."""
    require(mode in MODES, "explicit planned execution mode required")
    validate_config(config, ready=ready)
    if mode == "live" and ready:
        review = cast(str, config["model_support_review"])
        require(
            review.startswith(("https://developers.openai.com/", "https://platform.openai.com/")),
            "review model parameter support in official documentation",
        )
        require(
            config["model"] != "pickup-recording-mock"
            and "pickup-recording-mock" not in cast(list[JsonValue], config["returned_models"])
            and config["budget_review"] != "NO_NETWORK_NO_COST",
            "mock-only configuration cannot be planned for live execution",
        )


def source_identity() -> JsonObject:
    source = code_identity()
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=Path(__file__).resolve().parents[3],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    ).stdout.strip()
    source["branch"] = branch or None
    return source


def allocations(seed: int, count: int) -> list[JsonValue]:
    combinations = [(t, d, c) for t in TARGETS for d in (1, 2, 3, 4) for c in CONDITIONS]
    random.Random(seed).shuffle(combinations)
    return [
        {"allocation_id": f"a{i:03}", "target": t, "d": d, "condition": c}
        for i, (t, d, c) in enumerate(combinations[:count], 1)
    ]


def seal_plan(plan: JsonObject) -> None:
    plan["plan_sha256"] = digest({k: v for k, v in plan.items() if k != "plan_sha256"})


def make_plan(
    *,
    config: JsonObject | None = None,
    mode: str = "recording-mock",
    engine_seed: int = 42,
    allocation_seed: int = 0,
    allocation_count: int = 40,
    call_cap: int = 40,
) -> JsonObject:
    plan: JsonObject = {
        "plan_version": PLAN_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "export_version": EXPORT_VERSION,
        "audit_version": AUDIT_VERSION,
        "configuration_version": CONFIGURATION_VERSION,
        "wire_schema_version": WIRE_VERSION,
        "prompt_version": PROFILE.version,
        "instructions_sha256": INSTRUCTIONS_SHA256,
        "scenario_version": SCENARIO_VERSION,
        "state": "DRAFT" if config is None else "READY",
        "mode": mode,
        "source": source_identity(),
        "runtime": runtime_identity(),
        "config": config
        if config is not None
        else {
            "provider": "openai",
            "model": None,
            "returned_models": None,
            "reasoning_effort": None,
            "max_output_tokens": None,
            "timeout_seconds": None,
            "max_response_bytes": None,
            "model_support_review": None,
            "budget_review": None,
        },
        "engine_seed": engine_seed,
        "allocation_seed": allocation_seed,
        "allocation_count": allocation_count,
        "allocations": allocations(allocation_seed, allocation_count),
        "call_cap": call_cap,
        "stop_policy": "first-infrastructure-or-recording-failure",
        "retry": False,
        "replacement": False,
        "resume": False,
    }
    seal_plan(plan)
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
            "configuration_version",
            "wire_schema_version",
            "prompt_version",
            "instructions_sha256",
            "scenario_version",
            "state",
            "mode",
            "source",
            "runtime",
            "config",
            "engine_seed",
            "allocation_seed",
            "allocation_count",
            "allocations",
            "call_cap",
            "stop_policy",
            "retry",
            "replacement",
            "resume",
            "plan_sha256",
        },
        "plan inventory",
    )
    for key, value in (
        ("plan_version", PLAN_VERSION),
        ("protocol_version", PROTOCOL_VERSION),
        ("export_version", EXPORT_VERSION),
        ("audit_version", AUDIT_VERSION),
        ("configuration_version", CONFIGURATION_VERSION),
        ("wire_schema_version", WIRE_VERSION),
        ("prompt_version", PROFILE.version),
        ("instructions_sha256", INSTRUCTIONS_SHA256),
        ("scenario_version", SCENARIO_VERSION),
        ("stop_policy", "first-infrastructure-or-recording-failure"),
        ("retry", False),
        ("replacement", False),
        ("resume", False),
    ):
        same(plan[key], value, key)
    require(plan["state"] in ("DRAFT", "READY"), "plan state")
    require(
        plan["plan_sha256"] == digest({k: v for k, v in plan.items() if k != "plan_sha256"}),
        "plan digest",
    )
    for key in ("engine_seed", "allocation_seed", "allocation_count", "call_cap"):
        require(type(plan[key]) is int, "integer allocation/budget fields")
    count, cap = cast(int, plan["allocation_count"]), cast(int, plan["call_cap"])
    require(1 <= count <= cap <= 40, "allocation count exceeds call budget")
    same(
        plan["allocations"],
        allocations(cast(int, plan["allocation_seed"]), count),
        "frozen allocation order",
    )
    source, runtime = obj(plan["source"]), obj(plan["runtime"])
    require(
        "branch" in source
        and (
            source["branch"] is None
            or (type(source["branch"]) is str and bool(source["branch"].strip()))
        ),
        "source branch",
    )
    for key in ("git_commit", "working_source_sha256"):
        source_value = source.get(key)
        length = 40 if key == "git_commit" else 64
        require(
            type(source_value) is str
            and len(source_value) == length
            and all(c in "0123456789abcdef" for c in source_value),
            "source digest required",
        )
    require(
        bool(runtime.get("implementation")) and isinstance(runtime.get("version"), list),
        "runtime required",
    )
    require(type(plan["mode"]) is str, "planned mode type")
    validate_mode_config(
        cast(str, plan["mode"]), obj(plan["config"]), ready=plan["state"] == "READY"
    )
    if execution:
        require(plan["state"] == "READY", "unresolved draft cannot execute")
        same(source, source_identity(), "execution source mismatch; generate a new plan")
        same(runtime, runtime_identity(), "execution runtime mismatch")


def mock_config() -> JsonObject:
    """Explicit synthetic configuration, never the default experimental model."""
    return {
        "provider": "openai",
        "model": "pickup-recording-mock",
        "returned_models": ["pickup-recording-mock"],
        "reasoning_effort": "low",
        "max_output_tokens": 256,
        "timeout_seconds": 20,
        "max_response_bytes": 2_000_000,
        "model_support_review": "OFFLINE_MOCK_ONLY",
        "budget_review": "NO_NETWORK_NO_COST",
    }
