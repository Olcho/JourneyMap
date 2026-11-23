"""Researcher-only pilot plan; no default selection of unresolved study seeds."""

import random
import re
from dataclasses import asdict, dataclass
from typing import cast

from journeymap.adapters.llm import Record, parameters_v1
from journeymap.adapters.memory_horizon_live import (
    CONFIGURATION_VERSION,
    ENDPOINT,
    WIRE_SCHEMA_VERSION,
)
from journeymap.adapters.memory_horizon_prompt import PROFILE
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.experiments.alderwick import TrialPolicy
from journeymap.experiments.pilot_journal import digest
from journeymap.experiments.provenance import code_identity, runtime_identity

STUDY_PLAN_VERSION = "memory-horizon-pilot-study-1"
PROTOCOL_VERSION = "alderwick-memory-horizon-live-1"
AUDIT_VERSION = "memory-horizon-live-preflight-audit-1"
COHORT_SCHEMA_VERSION = 1
TRIAL_SCHEMA_VERSION = "memory-horizon-pilot-evidence-1"
CONDITIONS = ("no-event-memory", "recency-k1", "recency-k2", "recency-k3")
RANDOMIZATION_VERSION = "python-random-shuffle-three-blocks-1"
REQUIRES_HUMAN_DECISION = (
    "final provider/model identity and snapshot-drift acceptance",
    "accept or revise historical M8 reasoning, token cap, timeouts and bounds",
    "final engine seed and allocation randomization seed (mock seeds are not approvals)",
    "infrastructure replacement authorization and maximum replacements",
    "live transport review/enablement; exact outgoing schema service compatibility",
)


@dataclass(frozen=True)
class PilotConfiguration:
    provider_name: str
    model: str
    reference_adapter_version: str
    reasoning_effort: str
    max_output_tokens: int
    transport_timeout_seconds: float
    max_provider_calls: int
    max_decisions: int
    max_consecutive_failures: int
    wall_timeout_seconds: float

    def __post_init__(self) -> None:
        if self.provider_name != "openai" or any(
            type(value) is not str or not value
            for value in (self.model, self.reference_adapter_version)
        ):
            raise ValueError("explicit provider/model/reference identity required")
        parameters_v1(self.parameters)
        _ = self.policy  # Validate the full termination policy, not only parameters.
        TrialPolicy(wall_timeout_seconds=self.transport_timeout_seconds)

    @property
    def parameters(self) -> JsonObject:
        return {
            "reasoning": {"effort": self.reasoning_effort},
            "max_output_tokens": self.max_output_tokens,
        }

    @property
    def policy(self) -> TrialPolicy:
        return TrialPolicy(
            self.max_decisions,
            self.max_provider_calls,
            self.max_consecutive_failures,
            self.wall_timeout_seconds,
        )

    def to_json(self) -> JsonObject:
        return cast(JsonObject, asdict(self))


def official_m8_reference() -> PilotConfiguration:
    """Explicit historical reference, NOT a default or final live-pilot approval.

    manifest.json supplies model/parameters/termination_policy; the official
    report supplies socket timeout 20. No environment or artifact is rewritten.
    """
    return PilotConfiguration(
        "openai", "gpt-5.6-sol", "responses-http-2", "medium", 4096, 20, 24, 24, 3, 240
    )


def allocations(cohort_id: str, randomization_seed: int) -> list[JsonObject]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", cohort_id):
        raise ValueError("safe nonempty cohort ID required")
    if type(randomization_seed) is not int:
        raise ValueError("explicit integer randomization seed required")
    rng = random.Random(randomization_seed)
    result: list[JsonObject] = []
    for block in (1, 2, 3):
        order = list(CONDITIONS)
        rng.shuffle(order)
        for condition in order:
            index = len(result) + 1
            result.append(
                {
                    "allocation_id": f"{cohort_id}-a{index:03d}",
                    "block": block,
                    "order": index,
                    "condition": condition,
                    "intended_trial_id": f"{cohort_id}-t{index:03d}",
                    "execution_status": "PLANNED",
                    "replacement_of": None,
                    "replacement_reason": None,
                    "integrity_status": "NOT_ASSESSED",
                    "behavioral_denominator": None,
                    "infrastructure_failure": None,
                    "primary_success": None,
                    "trial_record_sha256": None,
                }
            )
    return result


def build_cohort(
    *,
    cohort_id: str,
    engine_seed: int,
    allocation_randomization_seed: int,
    configuration: PilotConfiguration,
) -> JsonObject:
    return _cohort_plan(
        cohort_id,
        engine_seed,
        allocation_randomization_seed,
        configuration,
        code_identity(),
        runtime_identity(),
    )


def _cohort_plan(
    cohort_id: str,
    engine_seed: int,
    allocation_randomization_seed: int,
    configuration: PilotConfiguration,
    source: JsonObject,
    runtime: JsonObject,
) -> JsonObject:
    if type(engine_seed) is not int:
        raise ValueError("explicit engine seed required")
    result: JsonObject = {
        "schema_version": COHORT_SCHEMA_VERSION,
        "study_plan_version": STUDY_PLAN_VERSION,
        "cohort_id": cohort_id,
        "protocol_version": PROTOCOL_VERSION,
        "prompt_version": PROFILE.version,
        "wire_schema_version": WIRE_SCHEMA_VERSION,
        "configuration_version": CONFIGURATION_VERSION,
        "endpoint": ENDPOINT,
        "audit_version": AUDIT_VERSION,
        "source": source,
        "runtime": runtime,
        "configuration": configuration.to_json(),
        "engine_seed": engine_seed,
        "allocation_randomization_seed": allocation_randomization_seed,
        "randomization_version": RANDOMIZATION_VERSION,
        "purpose": "live protocol pilot; not a behavioral-effect demonstration",
        "execution_mode": "RECORDING_MOCK_ONLY",
        "live_transport_enabled": False,
        "configuration_approval": "REQUIRES HUMAN DECISION",
        "remaining_human_decisions": list(REQUIRES_HUMAN_DECISION),
        "primary_endpoint": "task_completed AND pre_completion_repeat_count == 0",
        "horizon": {"start_tick": 0, "end_tick": 24, "early_stop": False},
        "allocations": cast(list[JsonValue], allocations(cohort_id, allocation_randomization_seed)),
    }
    result["frozen_plan_sha256"] = digest({k: v for k, v in result.items() if k != "allocations"})
    return result


def derived_metrics(metrics: JsonObject) -> JsonObject:
    repeats, post = metrics["repeat_destination_count"], metrics["post_completion_repeat_count"]
    if type(repeats) is not int or type(post) is not int or not 0 <= post <= repeats:
        raise ValueError("invalid repeat counts")
    if type(metrics["task_completed"]) is not bool:
        raise ValueError("invalid completion")
    before = repeats - post
    return {
        "pre_completion_repeat_count": before,
        "primary_success": metrics["task_completed"] and before == 0,
    }


def validate_cohort(cohort: JsonObject, *, execution: bool = True) -> PilotConfiguration:
    """Execution checks today's identity; later audit only checks recorded contracts.

    The plan seal binds recorded identities, not external proof of source authorship.
    Journal/snapshot linkage supplies the independently retained comparison anchor.
    """
    configuration = PilotConfiguration(**cast(dict[str, object], cohort["configuration"]))  # type: ignore[arg-type]
    source, runtime = cohort["source"], cohort["runtime"]
    if not isinstance(source, dict) or not isinstance(runtime, dict):
        raise ValueError("recorded identity objects required")
    if set(source) != {
        "git_commit",
        "working_tree_dirty",
        "source_sha256",
        "source_identity_version",
        "source_scope",
        "git_source_tree",
        "working_source_sha256",
    } or set(runtime) != {"implementation", "version"}:
        raise ValueError("recorded identity inventory")
    for key in ("source_sha256", "working_source_sha256"):
        if type(source[key]) is not str or not re.fullmatch(
            r"[0-9a-f]{64}", cast(str, source[key])
        ):
            raise ValueError("invalid source digest")
    for key in ("git_commit", "git_source_tree"):
        if source[key] is not None and (
            type(source[key]) is not str
            or not re.fullmatch(r"[0-9a-f]{40}", cast(str, source[key]))
        ):
            raise ValueError("invalid recorded git identity")
    if source["working_tree_dirty"] is not None and type(source["working_tree_dirty"]) is not bool:
        raise ValueError("invalid dirty flag")
    if (
        source["source_identity_version"] != "utf8-lf-path-lengths-1"
        or source["source_scope"] != "src/journeymap/**/*.py+pyproject.toml"
        or type(runtime["implementation"]) is not str
        or not runtime["implementation"]
        or not isinstance(runtime["version"], list)
        or len(runtime["version"]) != 3
        or any(type(n) is not int or n < 0 for n in runtime["version"])
    ):
        raise ValueError("invalid recorded identity")
    expected = _cohort_plan(
        cast(str, cohort["cohort_id"]),
        cast(int, cohort["engine_seed"]),
        cast(int, cohort["allocation_randomization_seed"]),
        configuration,
        source,
        runtime,
    )
    if set(cohort) != set(expected):
        raise ValueError("cohort field inventory")
    if execution and (source != code_identity() or runtime != runtime_identity()):
        raise ValueError("source/runtime changed since cohort freeze")
    for key in expected.keys() - {"source", "runtime", "allocations"}:
        if (
            Record.capture({"v": cohort[key]}).serialized
            != Record.capture({"v": expected[key]}).serialized
        ):
            raise ValueError(f"cohort contract mismatch: {key}")
    rows = cast(list[JsonObject], cohort["allocations"])
    planned = cast(list[JsonObject], expected["allocations"])
    if len(rows) < 12:
        raise ValueError("twelve original allocations required")
    seen: dict[str, JsonObject] = {}
    trial_ids: set[str] = set()
    replaced: set[str] = set()
    for index, row in enumerate(rows, 1):
        if (
            set(row) != set(planned[0])
            or row["order"] != index
            or type(row["order"]) is not int
            or type(row["block"]) is not int
        ):
            raise ValueError("allocation shape/order")
        if index <= 12:
            for key in (
                "allocation_id",
                "block",
                "order",
                "condition",
                "intended_trial_id",
                "replacement_of",
                "replacement_reason",
            ):
                if row[key] != planned[index - 1][key]:
                    raise ValueError("balanced allocation reconstruction mismatch")
        else:
            parent_id = cast(str, row["replacement_of"])
            parent = seen.get(parent_id)
            if (
                parent is None
                or parent_id in replaced
                or parent["infrastructure_failure"] is not True
                or parent["execution_status"] != "FINISHED"
                or parent["integrity_status"] != "VERIFIED"
                or row["replacement_reason"] != "INFRASTRUCTURE_FAILURE"
                or (row["condition"], row["block"]) != (parent["condition"], parent["block"])
                or row["allocation_id"] != f"{cohort['cohort_id']}-a{index:03d}"
                or row["intended_trial_id"] != f"{cohort['cohort_id']}-t{index:03d}"
            ):
                raise ValueError("invalid replacement linkage")
            replaced.add(parent_id)
        identity, trial = cast(str, row["allocation_id"]), cast(str, row["intended_trial_id"])
        if (
            identity in seen
            or trial in trial_ids
            or row["execution_status"] not in ("PLANNED", "FINISHED")
        ):
            raise ValueError("duplicate identity or invalid execution status")
        if row["execution_status"] == "PLANNED":
            if row["integrity_status"] != "NOT_ASSESSED" or any(
                row[key] is not None
                for key in (
                    "behavioral_denominator",
                    "infrastructure_failure",
                    "primary_success",
                    "trial_record_sha256",
                )
            ):
                raise ValueError("planned allocation contains execution results")
        elif (
            row["integrity_status"] not in ("VERIFIED", "EXCLUDED")
            or any(
                type(row[key]) is not bool
                for key in (
                    "behavioral_denominator",
                    "infrastructure_failure",
                    "primary_success",
                )
            )
            or type(row["trial_record_sha256"]) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", row["trial_record_sha256"])
        ):
            raise ValueError("invalid finished allocation results")
        seen[identity] = row
        trial_ids.add(trial)
    return configuration


def admission(decisions: list[JsonObject], integrity: bool) -> JsonObject:
    infrastructure = any(
        d.get("failure") in ("INFRASTRUCTURE_FAILURE", "OBSERVATION_UNAVAILABLE", "ENGINE_ERROR")
        for d in decisions
    )
    has_response = any(d.get("raw_output") is not None for d in decisions)
    return {
        "integrity_status": "VERIFIED" if integrity else "EXCLUDED",
        "infrastructure_failure": infrastructure,
        "behavioral_denominator": integrity and (not infrastructure or has_response),
        "behavioral_failure_count": sum(
            d.get("failure") in ("REFUSAL", "INCOMPLETE_OUTPUT", "PARSER_FAILURE", "SCHEMA_INVALID")
            for d in decisions
        ),
        "all_attempts_retained": True,
    }


def add_replacement(cohort: JsonObject, allocation_id: str) -> JsonObject:
    """Explicit researcher operation; no automatic replacement or deletion."""
    result = Record.capture(cohort).data
    rows = cast(list[JsonObject], result["allocations"])
    source = next((a for a in rows if a["allocation_id"] == allocation_id), None)
    if (
        source is None
        or source["infrastructure_failure"] is not True
        or source["execution_status"] != "FINISHED"
        or source["integrity_status"] != "VERIFIED"
    ):
        raise ValueError("replacement requires a recorded infrastructure failure")
    if any(a["replacement_of"] == allocation_id for a in rows):
        raise ValueError("allocation already has a replacement")
    index = len(rows) + 1
    rows.append(
        {
            **source,
            "allocation_id": f"{result['cohort_id']}-a{index:03d}",
            "intended_trial_id": f"{result['cohort_id']}-t{index:03d}",
            "order": index,
            "execution_status": "PLANNED",
            "replacement_of": allocation_id,
            "replacement_reason": "INFRASTRUCTURE_FAILURE",
            "integrity_status": "NOT_ASSESSED",
            "behavioral_denominator": None,
            "infrastructure_failure": None,
            "primary_success": None,
            "trial_record_sha256": None,
        }
    )
    return result
