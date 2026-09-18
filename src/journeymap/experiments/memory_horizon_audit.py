"""Provider-free reconstruction for the offline Memory Horizon protocol only."""

import json
import math
from hashlib import sha256
from pathlib import Path
from typing import cast

from journeymap.adapters.event_memory import EventTraceArchive
from journeymap.adapters.llm import (
    CONFIGURATION_VERSION,
    decode_candidate,
    event_memory_input,
    observation_json,
    parameters_v1,
    parse_candidate,
    strict_json,
)
from journeymap.adapters.provider import ProviderRequest, response_identity_matches
from journeymap.bootstrap import create_memory_horizon_application, create_memory_horizon_kernel
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json, state_digest
from journeymap.core.controller import GameSubmissionError
from journeymap.core.handlers import ActionRequest, ValidationContext
from journeymap.core.kernel import SimulationKernel
from journeymap.core.replay import ReplayHarness, ReplayInput, ReplayReport
from journeymap.experiments.alderwick import (
    BudgetMonitor,
    TrialPolicy,
    _read_new_export,
    json_value,
)
from journeymap.experiments.inclusion import TRIAL_FIELDS, record_digest
from journeymap.experiments.memory_horizon import (
    END_TICK,
    PROTOCOL_VERSION,
    PROTOCOL_VERSION_V1,
    memory_policy,
    profile_for_protocol,
    protocol_precheck,
    summarize,
)
from journeymap.scenarios.alderwick.memory_horizon import (
    SCENARIO_VERSION,
    memory_horizon_schedule,
    memory_horizon_world,
)

AUDIT_VERSION_V1 = "memory-horizon-offline-correctness-1"
AUDIT_VERSION = "memory-horizon-offline-correctness-2"


def obj(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return value


def rows(value: JsonValue) -> list[JsonObject]:
    if not isinstance(value, list):
        raise ValueError("expected rows")
    return [obj(row) for row in value]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _identity(data: JsonObject) -> JsonObject:
    manifest = obj(data["manifest"])
    require(set(data) == TRIAL_FIELDS | {"event_traces"}, "record inventory")
    require(
        type(manifest["schema_version"]) is int and manifest["schema_version"] == 3,
        "export version",
    )
    profile = profile_for_protocol(cast(str, manifest["protocol_version"]))
    require(manifest["scenario_composition"] == SCENARIO_VERSION, "scenario version")
    require(manifest["prompt_version"] == profile.version, "prompt version")
    require(manifest["configuration_version"] == CONFIGURATION_VERSION, "configuration version")
    require(manifest["record_sha256"] == record_digest(data), "record seal")
    require(
        manifest["actor_id"] == "stranger" and manifest["controller"] == "LLMController",
        "controller scope",
    )
    require(manifest["run_id"] == f"{manifest['trial_id']}:run", "run identity")
    require(manifest["npc_activation_cycle"] == [], "NPC activations")
    require(data["knowledge"] == data["initial_knowledge"] == [], "Knowledge")
    require(data["initial_state"] == memory_horizon_world(), "initial world")
    require(manifest["initial_knowledge_digest"] == sha256(b"[]").hexdigest(), "Knowledge digest")
    require(
        manifest["schedule_digest"] == state_digest(obj(json_value(memory_horizon_schedule()))),
        "schedule digest",
    )
    require(
        manifest["simulation_start"] == 0
        and manifest["target_end_tick"] == END_TICK
        and manifest["target_simulation_hours"] == 24
        and manifest["minutes_per_tick"] == 60,
        "horizon configuration",
    )
    require(
        type(manifest["simulation_end"]) is int and 0 <= manifest["simulation_end"] <= END_TICK,
        "end tick",
    )
    identity = obj(manifest["provider_identity"])
    require(identity["kind"] == "fixture" and identity["name"] == "fake", "offline identity")
    memory_policy(
        cast(str, manifest["memory_policy"]), cast(str, manifest["memory_policy_version"])
    )
    parameters_v1(obj(manifest["parameters"]))
    return manifest


def replay_record(data: JsonObject) -> ReplayReport:
    manifest = _identity(data)
    engine = obj(manifest["engine_manifest"])
    require(engine["run_id"] == manifest["run_id"] and type(engine["seed"]) is int, "engine scope")
    value = obj(data["replay_input"])
    require(value["schedule"] == json_value(memory_horizon_schedule()), "schedule reconstruction")
    require(value["actions"] == data["action_requests"], "replay actions")
    require(value["advance_to"] == manifest["simulation_end"], "replay end tick")
    actions = tuple(ActionRequest(**row) for row in rows(value["actions"]))  # type: ignore[arg-type]
    require(
        all(a.actor_id == "stranger" and a.action_type in ("MOVE", "WAIT") for a in actions),
        "engine action scope",
    )

    def factory() -> SimulationKernel:
        kernel = create_memory_horizon_kernel(
            run_id=cast(str, engine["run_id"]), seed=cast(int, engine["seed"])
        )
        if json_value(kernel.manifest) != engine:
            kernel.close()
            raise ValueError("engine manifest reconstruction")
        return kernel

    report = ReplayHarness(factory).run(
        ReplayInput(memory_horizon_schedule(), actions, cast(int, value["advance_to"]))
    )
    require(json_value(report) == data["engine_report"], "engine report")
    for name, expected in (
        ("events", report.events),
        ("action_results", report.action_results),
        ("system_event_outcomes", report.system_event_outcomes),
        ("final_state", report.final_state),
    ):
        require(data[name] == json_value(expected), name)
    require(
        manifest["final_state_digest"] == report.final_state_digest
        and manifest["replay_equal"] is True,
        "replay equality",
    )
    return report


def _attempts(data: JsonObject, manifest: JsonObject) -> None:
    """Re-observe and resubmit recorded intents; never run a Controller/Provider.

    Reconstructing actual Observations also checks nested memory for visit leaks.
    Public receipts must match live Game output, not researcher-invented results.
    """
    protocol = cast(str, manifest["protocol_version"])
    profile = profile_for_protocol(protocol)
    engine = obj(manifest["engine_manifest"])
    kernel = create_memory_horizon_kernel(
        run_id=cast(str, manifest["run_id"]), seed=cast(int, engine["seed"])
    )
    kernel.boot()
    try:
        monitor = BudgetMonitor(max_content_bytes=cast(int, manifest["observation_budget_bytes"]))
        app, research = create_memory_horizon_application(kernel, pipeline=monitor)
        game = app.game_for("stranger")
        archive = EventTraceArchive()
        memory = memory_policy(
            cast(str, manifest["memory_policy"]), cast(str, manifest["memory_policy_version"])
        )
        decisions = rows(data["decisions"])
        policy = TrialPolicy(**obj(manifest["termination_policy"]))  # type: ignore[arg-type]
        calls = failures = 0
        ending: str | None = None
        response_ids: set[str] = set()
        for index, decision in enumerate(decisions, 1):
            require(
                ending is None
                and kernel.simulation_time < END_TICK
                and index <= policy.max_decisions
                and calls < policy.max_provider_calls,
                "decision after termination",
            )
            require(
                decision["decision_sequence"] == decision["activation_sequence"] == index
                and decision["run_id"] == manifest["run_id"]
                and decision["trial_id"] == manifest["trial_id"]
                and decision["actor_id"] == "stranger"
                and decision["simulation_time"] == kernel.simulation_time,
                "decision scope",
            )
            before, offset = kernel.simulation_time, len(research.action_traces)
            try:
                observation = game.observe()
            except GameSubmissionError:
                require(
                    decision["observation"] is None
                    and decision["receipt"] is None
                    and decision["closed_event_trace_id"] is None
                    and decision["action_trace_sequences"] == []
                    and decision["provider_called"] is False
                    and decision["turn_failure"] == "OBSERVATION_UNAVAILABLE",
                    "Observation failure",
                )
                ending = "OBSERVATION_UNAVAILABLE"
                continue
            require(observation_json(observation) == decision["observation"], "Observation content")
            context = archive.begin(observation)
            inputs, provenance = event_memory_input(context, memory)
            expected: JsonObject = {
                **provenance,
                "decision_opportunity_id": context.decision_opportunity_id,
                "opportunity_sequence": context.opportunity_sequence,
                "opportunity_attempt": context.attempt,
                "memory_policy": memory.policy_id,
                "memory_policy_version": memory.policy_version,
                "memory_observation_ids": [],
                "prompt_version": profile.version,
                "model": manifest["model"],
                "parameters": manifest["parameters"],
                "provider_identity": manifest["provider_identity"],
            }
            require(all(decision.get(k) == v for k, v in expected.items()), "memory provenance")
            if profile.input_projection is not None:
                inputs = profile.input_projection(inputs)
                serialized = canonical_json(inputs)
                require(
                    canonical_json(decision["model_visible_input"]) == serialized
                    and decision["model_visible_input_canonical"] == serialized
                    and decision["model_visible_input_bytes"] == len(serialized.encode("utf-8")),
                    "model-visible input reconstruction",
                )
            prompt = profile.render(inputs)
            request = ProviderRequest(
                prompt,
                profile.version,
                cast(str, manifest["model"]),
                CONFIGURATION_VERSION,
                obj(manifest["parameters"]),
            )
            require(
                decision["provider_request"] == json_value(request)
                and decision["prompt_sha256"] == sha256(prompt.encode("utf-8")).hexdigest()
                and decision["provider_called"] is True,
                "prompt reconstruction",
            )
            calls += 1
            action = None
            if decision["raw_output"] is not None:
                metadata = obj(decision["provider_metadata"])
                identity_ok = response_identity_matches(
                    obj(manifest["provider_identity"]), cast(str, manifest["model"]), metadata
                )
                require(
                    decision["provider_identity_consistent"] == identity_ok, "response identity"
                )
                duplicate = metadata.get("response_id") in response_ids
                if isinstance(metadata.get("response_id"), str):
                    response_ids.add(cast(str, metadata["response_id"]))
                if (
                    identity_ok
                    and not duplicate
                    and not metadata.get("refusal")
                    and metadata.get("status") not in ("incomplete", "failed", "cancelled")
                ):
                    try:
                        wire = strict_json(cast(str, decision["raw_output"]))
                        candidate = decode_candidate(wire)
                        action = parse_candidate(candidate, observation)
                    except (ValueError, TypeError, GameSubmissionError):
                        pass
                    if action is not None:
                        require(
                            decision["parsed_wire_candidate"] == wire
                            and decision["parsed_candidate"] == candidate,
                            "parsed candidate",
                        )
            require(decision["action_request"] == json_value(action), "action binding")
            receipt = None
            closed_id = None
            if action is not None:
                require(
                    decision["failure"] is None and decision["parser_outcome"] == "ACCEPTED",
                    "accepted decision",
                )
                refusal = protocol_precheck(
                    action,
                    ValidationContext(kernel.manifest.run_id, before, kernel.state_snapshot),
                    protocol_version=protocol,
                )
                if decision.get("protocol_failure") == "WALL_TIMEOUT":
                    # Wall time is recorded provenance, not deterministic simulation input.
                    refusal = "WALL_TIMEOUT"
                if refusal:
                    require(
                        decision.get("protocol_failure") == refusal
                        and decision["turn_failure"] == "SUBMISSION_ERROR",
                        "pre-submit refusal",
                    )
                    ending = refusal
                else:
                    receipt = game.submit(action)
                    require(
                        decision["turn_failure"] is None
                        and decision.get("protocol_failure") is None,
                        "submitted turn",
                    )
                    closed_id = archive.close(
                        observation, action, receipt, engine_submitted=True
                    ).event_trace_id
            else:
                require(
                    decision["failure"]
                    in ("PROVIDER_ERROR", "PROVIDER_IDENTITY_MISMATCH", "INVALID_OUTPUT")
                    and decision["turn_failure"] == "CONTROLLER_ERROR",
                    "failed decision",
                )
            require(
                decision["receipt"] == json_value(receipt)
                and decision["closed_event_trace_id"] == closed_id
                and decision["action_trace_sequences"]
                == [t.attempt_sequence for t in research.action_traces[offset:]],
                "receipt linkage",
            )
            failures = failures + 1 if kernel.simulation_time == before else 0
            if failures >= policy.max_consecutive_failures and ending is None:
                ending = "CONSECUTIVE_FAILURES"
        require(
            manifest["decisions"] == len(decisions) and manifest["provider_calls"] == calls,
            "attempt counts",
        )
        if ending is None:
            ending = (
                "HORIZON"
                if kernel.simulation_time == END_TICK
                else "MAX_DECISIONS"
                if len(decisions) >= policy.max_decisions
                else "MAX_PROVIDER_CALLS"
                if calls >= policy.max_provider_calls
                else "WALL_TIMEOUT"
            )
        if manifest["stop_reason"] == "WALL_TIMEOUT":
            elapsed = manifest["wall_seconds"]
            require(
                type(elapsed) in (int, float)
                and math.isfinite(cast(float, elapsed))
                and cast(float, elapsed) >= policy.wall_timeout_seconds,
                "wall timeout evidence",
            )
            # Wall deadline has precedence over decision/call bounds in the runner.
            if ending in ("WALL_TIMEOUT", "MAX_DECISIONS", "MAX_PROVIDER_CALLS"):
                ending = "WALL_TIMEOUT"
        require(
            manifest["stop_reason"] == ending
            and manifest["status"] == ("COMPLETED" if ending == "HORIZON" else "TERMINATED"),
            "termination",
        )
        require(kernel.simulation_time == manifest["simulation_end"], "attempt end tick")
        require(data["action_traces"] == json_value(research.action_traces), "live action traces")
        require(
            data["action_requests"]
            == json_value(tuple(t.request for t in research.action_traces if t.engine_submitted)),
            "submitted requests",
        )
        require(
            data["observations"] == [observation_json(o) for o in research.observations],
            "Observation inventory",
        )
        require(data["observation_attempts"] == monitor.attempts, "Observation budget attempts")
        require(data["event_traces"] == [t.to_json() for t in archive.traces], "closed archive")
        require(
            data["activations"]
            == [
                {
                    "sequence": i,
                    "actor_id": "stranger",
                    "tick": d["simulation_time"],
                    "decision_sequence": i,
                }
                for i, d in enumerate(decisions, 1)
            ],
            "activation inventory",
        )
        require(
            canonical_json(data["metrics"])
            == canonical_json(
                summarize(decisions, rows(data["action_traces"]), rows(data["events"]))
            ),
            "research metrics",
        )
    finally:
        kernel.close()


def result(reasons: list[str], protocol: str = PROTOCOL_VERSION) -> JsonObject:
    return {
        "policy_version": AUDIT_VERSION_V1 if protocol == PROTOCOL_VERSION_V1 else AUDIT_VERSION,
        "status": "EXCLUDED" if reasons else "INCLUDED",
        "reasons": cast(list[JsonValue], reasons),
    }


def assess_trial(data: JsonObject) -> JsonObject:
    """INCLUDED certifies offline record correctness, including valid failed trials."""
    protocol = PROTOCOL_VERSION
    try:
        protocol = cast(str, obj(data["manifest"])["protocol_version"])
        manifest = _identity(data)
        replay_record(data)
        _attempts(data, manifest)
    except (KeyError, TypeError, ValueError, GameSubmissionError, OverflowError):
        return result(["MEMORY_HORIZON_INTEGRITY"], protocol)
    return result([], protocol)


def read_export(directory: Path) -> JsonObject:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    require(isinstance(manifest, dict) and manifest.get("schema_version") == 3, "export schema")
    return _read_new_export(directory, manifest)


def replay_export(directory: Path) -> ReplayReport:
    return replay_record(read_export(directory))


def audit_export(directory: Path) -> JsonObject:
    try:
        data = read_export(directory)
        recomputed = assess_trial(data)
        stored = obj(data["manifest"]).get("research_inclusion")
        matches = stored == recomputed
        audit = result(
            [
                *cast(list[str], recomputed["reasons"]),
                *([] if matches else ["STORED_INCLUSION_MISMATCH"]),
            ],
            cast(str, obj(data["manifest"])["protocol_version"]),
        )
        audit.update(
            {
                "stored_inclusion": stored,
                "recomputed_inclusion": recomputed,
                "stored_inclusion_matches": matches,
            }
        )
        return audit
    except (OSError, KeyError, TypeError, ValueError):
        return result(["EXPORT_INTEGRITY"])
