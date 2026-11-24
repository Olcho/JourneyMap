"""Explicit data-quality gate, independent of execution status or model skill."""

import json
import math
import re
from dataclasses import asdict
from hashlib import sha256
from typing import cast

from journeymap.adapters.llm import PROMPT, parameters_v1
from journeymap.adapters.provider import response_identity_matches
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json, state_digest
from journeymap.experiments.provenance import SOURCE_IDENTITY_VERSION
from journeymap.scenarios.alderwick.experiment import (
    EXPERIMENT_SCENARIO_VERSION,
    experiment_schedule,
)
from journeymap.scenarios.alderwick.social import NPC_ACTIVATIONS

INCLUSION_VERSION = "pre-experiment-1"
TRIAL_FIELDS = frozenset(
    {
        "manifest",
        "metrics",
        "decisions",
        "activations",
        "observations",
        "observation_attempts",
        "action_requests",
        "action_traces",
        "action_results",
        "events",
        "system_event_outcomes",
        "knowledge",
        "initial_knowledge",
        "initial_state",
        "final_state",
        "replay_input",
        "engine_report",
    }
)


def record_digest(data: JsonObject) -> str:
    """Seal config AND artifacts; exclude only derived audit/export fields.

    ASCII escaping also preserves invalid raw model text. This detects accidental
    corruption, not a malicious author recomputing all hashes.
    """
    manifest = dict(_object(data["manifest"]))
    for key in ("research_inclusion", "files_sha256", "record_sha256"):
        manifest.pop(key, None)
    payload = json.dumps(
        {**data, "manifest": manifest},
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def _object(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return value


def _rows(value: JsonValue) -> list[JsonObject]:
    if not isinstance(value, list):
        raise ValueError("expected records")
    return [_object(item) for item in value]


def _hash(value: JsonValue) -> str:
    return sha256(
        json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _hex(value: JsonValue, length: int) -> bool:
    return (
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value) is not None
    )


def inclusion_result(reasons: list[str]) -> JsonObject:
    return {
        "policy_version": INCLUSION_VERSION,
        "status": "EXCLUDED" if reasons else "INCLUDED",
        "reasons": cast(list[JsonValue], sorted(set(reasons))),
    }


def assess_inclusion(data: JsonObject) -> JsonObject:
    """Recompute, never trust a stored INCLUDED flag. Keep the input unchanged.

    INCLUDED describes data quality within the recorded provider kind; fixtures
    remain fixtures and are never evidence of live model behaviour.
    """
    reasons: list[str] = []
    try:
        manifest = _object(data["manifest"])
        if manifest.get("schema_version") != 2:
            return inclusion_result(["NEW_PROVENANCE_REQUIRED"])
        if set(data) != TRIAL_FIELDS or manifest.get("record_sha256") != record_digest(data):
            reasons.append("RECORD_INTEGRITY")
        if not (
            manifest["status"] == "COMPLETED"
            and manifest["stop_reason"] == "HORIZON"
            and manifest["simulation_end"] == manifest["target_end_tick"] == 24
        ):
            reasons.append("EXPECTED_HORIZON_NOT_REACHED")
        if manifest["replay_equal"] is not True:
            reasons.append("REPLAY_MISMATCH")
        code, runtime = _object(manifest["code"]), _object(manifest["python_runtime"])
        version = runtime["version"]
        if not (
            code["source_identity_version"] == SOURCE_IDENTITY_VERSION
            and _hex(code["git_commit"], 40)
            and _hex(code["git_source_tree"], 40)
            and type(code["working_tree_dirty"]) is bool
            and _hex(code["working_source_sha256"], 64)
            and _hex(code["source_sha256"], 64)
            and code["source_scope"] == "src/journeymap/**/*.py+pyproject.toml"
            and manifest["source_unchanged_during_trial"] is True
            and isinstance(runtime["implementation"], str)
            and bool(runtime["implementation"])
            and isinstance(version, list)
            and len(version) == 3
            and all(type(part) is int and part >= 0 for part in version)
        ):
            reasons.append("PROVENANCE_INCONSISTENCY")
        identity = _object(manifest["provider_identity"])
        if not (
            identity["name"] == manifest["provider"]
            and identity["version"] == manifest["provider_version"]
            and isinstance(identity["implementation"], str)
            and isinstance(identity["version"], str)
            and bool(identity["version"])
            and bool(identity["implementation"])
            and (
                (
                    identity["kind"] == "fixture"
                    and identity["name"] == "fake"
                    and identity["response_model_policy"] == "exact-requested-model-1"
                )
                or (
                    identity["kind"] == "live"
                    and identity["name"] == "openai"
                    and identity["response_model_policy"] == "openai-alias-dated-snapshot-1"
                    and identity["implementation"]
                    == "journeymap.adapters.openai_provider.OpenAIProvider"
                    and isinstance(identity["timeout_seconds"], (int, float))
                    and not isinstance(identity["timeout_seconds"], bool)
                    and math.isfinite(identity["timeout_seconds"])
                    and identity["timeout_seconds"] > 0
                )
            )
        ):
            reasons.append("PROVIDER_IDENTITY_INCONSISTENCY")
        decisions, traces = _rows(data["decisions"]), _rows(data["action_traces"])
        replay, report = _object(data["replay_input"]), _object(data["engine_report"])
        engine = _object(manifest["engine_manifest"])
        policy = _object(manifest["termination_policy"])
        if not (
            engine["engine_version"] == "0.1.0"
            and engine["run_id"] == manifest["run_id"]
            and manifest["run_id"] == str(manifest["trial_id"]) + ":run"
            and type(engine["seed"]) is int
            and manifest["actor_id"] == "stranger"
            and manifest["controller"] == "LLMController"
            and manifest["protocol_version"] == "alderwick-24h-2"
            and manifest["configuration_version"] == "responses-structured-2"
            and manifest["prompt_version"] == "alderwick-decision-2"
            and manifest["memory_policy"] == "NoMemory"
            and manifest["minutes_per_tick"] == 60
            and manifest["target_simulation_hours"] == 24
            and manifest["simulation_start"] == 0
            and manifest["simulation_hours"] == manifest["simulation_end"]
            and manifest["npc_activation_cycle"] == list(NPC_ACTIVATIONS)
            and manifest["parameters"] == parameters_v1(_object(manifest["parameters"]))
            and isinstance(manifest["model"], str)
            and bool(manifest["model"])
            and manifest["scenario_composition"]
            == engine["scenario_version"]
            == EXPERIMENT_SCENARIO_VERSION
            and engine["scenario_version"] == _object(replay["schedule"])["scenario_version"]
            and replay["schedule"] == json.loads(json.dumps(asdict(experiment_schedule())))
            and manifest["schedule_digest"] == state_digest(_object(replay["schedule"]))
            and manifest["initial_knowledge_digest"] == _hash(data["initial_knowledge"])
            and engine["initial_state_digest"] == state_digest(_object(data["initial_state"]))
            and manifest["final_state_digest"] == state_digest(_object(data["final_state"]))
            and report["final_state"] == data["final_state"]
            and report["final_state_digest"] == manifest["final_state_digest"]
            and report["final_simulation_time"]
            == replay["advance_to"]
            == manifest["simulation_end"]
            and all(
                report[key] == data[key]
                for key in ("action_results", "events", "system_event_outcomes")
            )
            and len(decisions) == manifest["decisions"]
            and sum(item["provider_called"] is True for item in decisions)
            == manifest["provider_calls"]
            and type(manifest["observation_budget_bytes"]) is int
            and manifest["observation_budget_bytes"] > 0
            and set(policy)
            == {
                "max_decisions",
                "max_provider_calls",
                "max_consecutive_failures",
                "wall_timeout_seconds",
            }
            and all(
                type(policy[key]) is int and cast(int, policy[key]) > 0
                for key in ("max_decisions", "max_provider_calls", "max_consecutive_failures")
            )
            and isinstance(policy["wall_timeout_seconds"], (int, float))
            and not isinstance(policy["wall_timeout_seconds"], bool)
            and math.isfinite(policy["wall_timeout_seconds"])
            and policy["wall_timeout_seconds"] > 0
            and len(decisions) <= cast(int, policy["max_decisions"])
            and cast(int, manifest["provider_calls"]) <= cast(int, policy["max_provider_calls"])
        ):
            reasons.append("CONFIGURATION_INTEGRITY")
        submitted = [trace for trace in traces if trace["engine_submitted"] is True]
        trace_by_seq = {trace["attempt_sequence"]: trace for trace in traces}
        observations = {item["observation_id"]: item for item in _rows(data["observations"])}
        if len(observations) != len(_rows(data["observations"])) or any(
            observation["run_id"] != manifest["run_id"]
            or observation["content_digest"] != state_digest(_object(observation["content"]))
            for observation in observations.values()
        ):
            reasons.append("ACTION_TRACE_INCOMPLETE")
        if not (
            [trace["attempt_sequence"] for trace in traces] == list(range(1, len(traces) + 1))
            and [trace["request"] for trace in submitted]
            == data["action_requests"]
            == replay["actions"]
            and [trace["result"] for trace in submitted] == data["action_results"]
            and all(trace["result"] is not None for trace in submitted)
            and all(
                _object(trace["result"])["action_request_id"]
                == _object(trace["request"])["action_request_id"]
                for trace in submitted
            )
            and all(
                _object(trace["request"])["based_on_observation_id"] in observations
                for trace in traces
            )
            and all(
                _object(trace["request"])["run_id"] == manifest["run_id"]
                and _object(trace["request"])["actor_id"]
                == observations[_object(trace["request"])["based_on_observation_id"]]["actor_id"]
                for trace in traces
            )
        ):
            reasons.append("ACTION_TRACE_INCOMPLETE")
        activations = _rows(data["activations"])
        if [item["sequence"] for item in activations] != list(range(1, len(activations) + 1)):
            reasons.append("ACTION_TRACE_INCOMPLETE")
        linked_sequences: list[int] = []
        returned_models = sorted(
            {
                cast(str, _object(d["provider_metadata"])["model"])
                for d in decisions
                if isinstance(_object(d["provider_metadata"]).get("model"), str)
            }
        )
        if manifest["response_models"] != returned_models:
            reasons.append("PROVIDER_IDENTITY_INCONSISTENCY")
        for index, decision in enumerate(decisions, 1):
            if not (
                decision["decision_sequence"] == index
                and decision["trial_id"] == manifest["trial_id"]
                and decision["run_id"] == manifest["run_id"]
                and decision["actor_id"] == manifest["actor_id"]
                and decision["model"] == manifest["model"]
                and decision["parameters"] == manifest["parameters"]
                and decision["memory_policy"] == "NoMemory"
                and decision["memory_observation_ids"] == []
                and {
                    "sequence": decision["activation_sequence"],
                    "actor_id": "stranger",
                    "tick": decision["simulation_time"],
                    "decision_sequence": index,
                }
                in activations
            ):
                reasons.append("CONFIGURATION_INTEGRITY")
            request = decision["provider_request"]
            if isinstance(request, dict) and not (
                request["model"] == decision["model"] == manifest["model"]
                and request["parameters"] == decision["parameters"] == manifest["parameters"]
                and request["configuration_version"] == manifest["configuration_version"]
                and request["prompt_version"]
                == decision["prompt_version"]
                == manifest["prompt_version"]
                and request["prompt"]
                == PROMPT
                + "\nINPUT_JSON\n"
                + canonical_json(
                    {
                        "observation": decision["observation"],
                        "memory": [],
                    }
                )
                and decision["prompt_sha256"]
                == sha256(request["prompt"].encode("utf-8")).hexdigest()
            ):
                reasons.append("CONFIGURATION_INTEGRITY")
            if decision["provider_called"] is True and not isinstance(request, dict):
                reasons.append("CONFIGURATION_INTEGRITY")
            if decision["raw_output"] is not None and decision.get(
                "provider_identity_consistent"
            ) is not response_identity_matches(
                identity, cast(str, manifest["model"]), _object(decision["provider_metadata"])
            ):
                reasons.append("PROVIDER_IDENTITY_INCONSISTENCY")
            if decision["provider_called"] is True and (
                decision.get("provider_identity") != identity
                or decision["provider"] != identity["name"]
                or decision["provider_version"] != identity["version"]
                or (
                    decision["raw_output"] is not None
                    and not response_identity_matches(
                        identity,
                        cast(str, manifest["model"]),
                        _object(decision["provider_metadata"]),
                    )
                )
            ):
                reasons.append("PROVIDER_IDENTITY_INCONSISTENCY")
            if decision.get("failure") in (
                "PROVIDER_ERROR",
                "PROVIDER_IDENTITY_MISMATCH",
                "MEMORY_ERROR",
                "PROMPT_ERROR",
                "OBSERVATION_UNAVAILABLE",
            ):
                reasons.append("INFRASTRUCTURE_FAILURE")
            observation = decision["observation"]
            if (
                isinstance(observation, dict)
                and observations.get(observation["observation_id"]) != observation
            ):
                reasons.append("ACTION_TRACE_INCOMPLETE")
            sequences = cast(list[int], decision["action_trace_sequences"])
            linked_sequences.extend(sequences)
            if (decision["receipt"] is not None and not sequences) or any(
                sequence not in trace_by_seq
                or trace_by_seq[sequence]["request"] != decision["action_request"]
                or trace_by_seq[sequence]["controller_result"] != decision["receipt"]
                for sequence in sequences
            ):
                reasons.append("ACTION_TRACE_INCOMPLETE")
            if decision.get("provider_identity_consistent") is False and (
                decision["action_request"] is not None
                or sequences
                or decision["receipt"] is not None
            ):
                reasons.append("PROVIDER_IDENTITY_INCONSISTENCY")
        if sorted(linked_sequences) != [
            trace["attempt_sequence"]
            for trace in traces
            if _object(trace["request"])["actor_id"] == manifest["actor_id"]
        ]:
            reasons.append("ACTION_TRACE_INCOMPLETE")
        if any(trace["error_type"] is not None for trace in traces):
            reasons.append("INFRASTRUCTURE_FAILURE")
    except (KeyError, TypeError, ValueError, UnicodeError):
        reasons.append("RECORD_INTEGRITY")
    return inclusion_result(reasons)
