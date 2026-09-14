"""Offline Phase 1 archive/retrieval audit. No provider or policy decisions run."""

from dataclasses import asdict
from hashlib import sha256
from typing import cast

from journeymap.adapters.event_memory import (
    EVENT_MEMORY_PROMPT_VERSION,
    EVENT_MEMORY_PROTOCOL_VERSION,
    EventMemoryPolicy,
    EventTraceArchive,
    RecencyEventMemory,
    observation_snapshot,
)
from journeymap.adapters.llm import event_memory_input, event_memory_prompt
from journeymap.adapters.memory import NoMemory
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.core.controller import ControllerActionResult, GameSubmissionError
from journeymap.core.handlers import ActionRequest
from journeymap.core.observations import Observation
from journeymap.experiments.inclusion import TRIAL_FIELDS, record_digest

CORRECTNESS_VERSION = "event-memory-phase1-correctness-1"


def _object(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return value


def _rows(value: JsonValue) -> list[JsonObject]:
    if not isinstance(value, list):
        raise ValueError("expected rows")
    return [_object(row) for row in value]


def _observation(data: JsonObject) -> Observation:
    observation = Observation(
        cast(str, data["run_id"]),
        cast(str, data["actor_id"]),
        cast(int, data["observation_sequence"]),
        cast(int, data["simulation_time"]),
        _object(data["content"]),
        cast(int, data["schema_version"]),
    )
    if observation_snapshot(observation) != data:
        raise ValueError("invalid Observation envelope")
    return observation


def assess_event_memory(data: JsonObject) -> JsonObject:
    """Reconstruct opportunity/closure order and every delivered memory snapshot.

    INCLUDED means Phase 1 record correctness only, not performance admission.
    Failed/incomplete trials can be correct. File audit additionally runs engine
    replay. Like the historical seal this is not cryptographic authentication.
    """
    reasons: list[str] = []
    try:
        manifest = _object(data["manifest"])
        if (
            set(data) != TRIAL_FIELDS | {"event_traces"}
            or manifest["schema_version"] != 3
            or manifest["protocol_version"] != EVENT_MEMORY_PROTOCOL_VERSION
            or manifest["prompt_version"] != EVENT_MEMORY_PROMPT_VERSION
            or manifest["record_sha256"] != record_digest(data)
        ):
            raise ValueError("invalid Phase 1 record identity")
        policy: EventMemoryPolicy
        if manifest["memory_policy"] == NoMemory.policy_id:
            policy = NoMemory()
        elif manifest["memory_policy"] == RecencyEventMemory.policy_id:
            policy = RecencyEventMemory()
        else:
            raise ValueError("unsupported memory condition")
        if manifest["memory_policy_version"] != policy.policy_version:
            raise ValueError("unsupported memory version")
        archive = EventTraceArchive()
        observations = _rows(data["observations"])
        actions = _rows(data["action_requests"])
        traces = _rows(data["action_traces"])
        decisions = _rows(data["decisions"])
        if (
            len(decisions) != manifest["decisions"]
            or sum(decision["provider_called"] is True for decision in decisions)
            != manifest["provider_calls"]
        ):
            raise ValueError("decision inventory mismatch")
        for sequence, decision in enumerate(decisions, 1):
            if (
                decision["decision_sequence"] != sequence
                or decision["run_id"] != manifest["run_id"]
                or decision["actor_id"] != manifest["actor_id"]
            ):
                raise ValueError("decision scope/order mismatch")
            if decision["observation"] is None:
                if decision["closed_event_trace_id"] is not None or decision["receipt"] is not None:
                    raise ValueError("closure without Observation")
                continue
            observation_data = _object(decision["observation"])
            observation = _observation(observation_data)
            if (
                observation_data not in observations
                or observation.run_id != manifest["run_id"]
                or observation.actor_id != manifest["actor_id"]
            ):
                raise ValueError("Observation scope/provenance mismatch")
            context = archive.begin(observation)
            expected_input, provenance = event_memory_input(context, policy)
            expected: JsonObject = {
                **provenance,
                "decision_opportunity_id": context.decision_opportunity_id,
                "opportunity_sequence": context.opportunity_sequence,
                "opportunity_attempt": context.attempt,
                "memory_policy": policy.policy_id,
                "memory_policy_version": policy.policy_version,
                "memory_observation_ids": [],
            }
            if any(decision.get(key) != value for key, value in expected.items()):
                raise ValueError("retrieval/attempt provenance mismatch")
            prompt = event_memory_prompt(expected_input)
            provider_request = _object(decision["provider_request"])
            if (
                provider_request["prompt"] != prompt
                or provider_request["prompt_version"] != EVENT_MEMORY_PROMPT_VERSION
                or decision["prompt_sha256"] != sha256(prompt.encode("utf-8")).hexdigest()
            ):
                raise ValueError("prompt provenance mismatch")
            linked = [
                trace
                for trace in traces
                if trace["attempt_sequence"]
                in cast(list[JsonValue], decision["action_trace_sequences"])
            ]
            if len(linked) != len(cast(list[JsonValue], decision["action_trace_sequences"])) or any(
                trace["request"] != decision["action_request"]
                or trace["controller_result"] != decision["receipt"]
                for trace in linked
            ):
                raise ValueError("decision does not match its live attempt")
            submitted = [trace for trace in linked if trace["engine_submitted"] is True]
            closed_id = None
            if decision["receipt"] is not None and submitted:
                request_data, receipt_data = (
                    _object(decision["action_request"]),
                    _object(decision["receipt"]),
                )
                if (
                    len(submitted) != 1
                    or submitted[0]["request"] != request_data
                    or submitted[0]["controller_result"] != receipt_data
                    or request_data not in actions
                    or decision["failure"] is not None
                    or decision["turn_failure"] is not None
                    or decision["parser_outcome"] != "ACCEPTED"
                ):
                    raise ValueError("receipt/engine submission mismatch")
                request = ActionRequest(**request_data)  # type: ignore[arg-type]
                receipt = ControllerActionResult(**receipt_data)  # type: ignore[arg-type]
                if asdict(receipt) != receipt_data:
                    raise ValueError("invalid receipt")
                closed_id = archive.close(
                    observation, request, receipt, engine_submitted=True
                ).event_trace_id
            if decision["closed_event_trace_id"] != closed_id:
                raise ValueError("incorrect closure eligibility")
        if data["event_traces"] != [trace.to_json() for trace in archive.traces]:
            raise ValueError("archive does not match closed experiences")
    except (KeyError, TypeError, ValueError, GameSubmissionError):
        reasons.append("EVENT_MEMORY_INTEGRITY")
    return {
        "policy_version": CORRECTNESS_VERSION,
        "status": "EXCLUDED" if reasons else "INCLUDED",
        "reasons": cast(list[JsonValue], reasons),
    }
