"""Offline Distinct Places orchestration; metrics never enter the actor path."""

from datetime import UTC, datetime
from hashlib import sha256
from time import monotonic
from typing import cast

from journeymap.adapters.event_memory import EventMemoryPolicy, RecencyEventMemory
from journeymap.adapters.llm import CONFIGURATION_VERSION, LLMController, Record, observation_json
from journeymap.adapters.memory import NoMemory
from journeymap.adapters.memory_horizon_prompt import PROFILE
from journeymap.application.turns import run_controller_turn
from journeymap.bootstrap import create_memory_horizon_application, create_memory_horizon_kernel
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json, state_digest
from journeymap.core.controller import ControllerActionResult, GamePort, GameSubmissionError
from journeymap.core.handlers import ActionRequest, ValidationContext
from journeymap.core.replay import ReplayHarness, ReplayInput, ReplayReport
from journeymap.experiments.alderwick import BudgetMonitor, TrialPolicy, action_duration, json_value
from journeymap.experiments.inclusion import record_digest
from journeymap.experiments.provenance import code_identity, runtime_identity
from journeymap.scenarios.alderwick.memory_horizon import (
    SCENARIO_VERSION,
    SQUARE,
    TARGETS,
    memory_horizon_schedule,
)

PROTOCOL_VERSION = "alderwick-memory-horizon-offline-1"
END_TICK = 24


def memory_policy(policy_id: str, version: str) -> EventMemoryPolicy:
    policies: tuple[EventMemoryPolicy, ...] = (
        NoMemory(),
        *(RecencyEventMemory(k) for k in (1, 2, 3)),
    )
    for policy in policies:
        if (policy.policy_id, policy.policy_version) == (policy_id, version):
            return policy
    raise ValueError("unsupported Memory Horizon condition")


def protocol_precheck(request: ActionRequest, context: ValidationContext) -> str | None:
    if request.action_type not in ("MOVE", "WAIT"):
        return "UNSUPPORTED_ACTION"
    if context.simulation_time + action_duration(request, context) > END_TICK:
        return "HORIZON_ACTION"
    return None


def summarize(
    decisions: list[JsonObject], traces: list[JsonObject], events: list[JsonObject]
) -> JsonObject:
    """Fold committed target arrivals/cycle returns, joined to decision attempts.

    WAIT does not complete a cycle. Only a successful ActorMoved to Square does.
    A repeat does not erase first completion; strict success examines all arrivals.
    """
    request_decision = {
        cast(JsonObject, decision["action_request"])["action_request_id"]: decision[
            "decision_sequence"
        ]
        for decision in decisions
        if isinstance(decision.get("action_request"), dict)
    }
    counts = dict.fromkeys(TARGETS, 0)
    completed: set[str] = set()
    pending: str | None = None
    completion: JsonValue = None
    post_repeats = 0
    for event in events:
        payload = cast(JsonObject, event["payload"])
        if event["event_type"] != "ActorMoved" or payload.get("actor_id") != "stranger":
            continue
        destination = payload["destination"]
        if destination in TARGETS:
            target = destination
            if counts[target] and completion is not None:
                post_repeats += 1
            counts[target] += 1
            pending = target
        elif destination == SQUARE and pending is not None:
            completed.add(pending)
            pending = None
            if len(completed) == 3 and completion is None:
                completion = request_decision[event["source_ref"]]
    repeats = sum(max(0, count - 1) for count in counts.values())
    return {
        "unique_destination_coverage": sum(count > 0 for count in counts.values()),
        "repeat_destination_count": repeats,
        "task_completed": completion is not None,
        "decisions_to_completion": completion,
        "decision_attempt_count": len(decisions),
        "engine_submission_count": sum(trace["engine_submitted"] is True for trace in traces),
        "strict_no_repeat_success": completion is not None and repeats == 0,
        "post_completion_repeat_count": post_repeats,
    }


def run_trial(
    controller: LLMController,
    *,
    trial_id: str,
    seed: int = 42,
    policy: TrialPolicy | None = None,
    monitor: BudgetMonitor | None = None,
) -> Record:
    """One fresh fixture Controller. No live adapter is admitted by this protocol."""
    if type(trial_id) is not str or not trial_id:
        raise ValueError("trial_id required")
    memory = controller.event_memory_policy
    if (
        controller.prompt_profile != PROFILE
        or type(memory) not in (NoMemory, RecencyEventMemory)
        or controller.last_decision is not None
        or controller.event_traces
    ):
        raise ValueError("requires a fresh Memory Horizon controller and supported memory")
    assert memory is not None
    memory_policy(memory.policy_id, memory.policy_version)
    identity = controller.provider_identity
    if identity["kind"] != "fixture" or identity["name"] != "fake":
        raise ValueError("Memory Horizon preparation is offline only")
    policy = policy if policy is not None else TrialPolicy()
    pipeline = monitor if monitor is not None else BudgetMonitor()
    source = code_identity()
    start_wall, started = monotonic(), datetime.now(UTC).isoformat()
    run_id = f"{trial_id}:run"
    kernel = create_memory_horizon_kernel(run_id=run_id, seed=seed)
    kernel.boot()
    try:
        app, research = create_memory_horizon_application(kernel, pipeline=pipeline)
        initial_state = kernel.state_snapshot
        schedule = memory_horizon_schedule()
        decisions: list[JsonObject] = []
        calls = failures = 0
        status, stop_reason = "COMPLETED", "HORIZON"
        precheck_failure: str | None = None
        game = app.game_for("stranger")

        def submit(request: ActionRequest) -> ControllerActionResult:
            nonlocal precheck_failure
            precheck_failure = None
            if monotonic() - start_wall >= policy.wall_timeout_seconds:
                precheck_failure = "WALL_TIMEOUT"
            else:
                try:
                    precheck_failure = protocol_precheck(
                        request,
                        ValidationContext(run_id, kernel.simulation_time, kernel.state_snapshot),
                    )
                except Exception:
                    precheck_failure = "TIMING_PRECHECK_ERROR"
            if precheck_failure:
                raise GameSubmissionError(precheck_failure)
            return game.submit(request)

        port = GamePort(game.observe, submit)
        while kernel.simulation_time < END_TICK:
            bound = (
                "WALL_TIMEOUT"
                if monotonic() - start_wall >= policy.wall_timeout_seconds
                else "MAX_DECISIONS"
                if len(decisions) >= policy.max_decisions
                else "MAX_PROVIDER_CALLS"
                if calls >= policy.max_provider_calls
                else None
            )
            if bound:
                status, stop_reason = "TERMINATED", bound
                break
            before, offset = kernel.simulation_time, len(research.action_traces)
            turn = run_controller_turn(port, controller)
            closed_id = None
            if (
                turn.observation is not None
                and turn.request is not None
                and turn.receipt is not None
                and any(
                    trace.engine_submitted and trace.request == turn.request
                    for trace in research.action_traces[offset:]
                )
            ):
                closed_id = controller.close_event_trace(
                    turn.observation, turn.request, turn.receipt, engine_submitted=True
                ).event_trace_id
            decision: JsonObject = (
                controller.last_decision.data
                if turn.observation is not None and controller.last_decision is not None
                else {
                    "observation": None,
                    "action_request": None,
                    "provider_request": None,
                    "provider_called": False,
                    "raw_output": None,
                    "failure": "OBSERVATION_UNAVAILABLE",
                    "prompt_version": PROFILE.version,
                    "parser_outcome": "NOT_RUN",
                }
            )
            decision.update(
                {
                    "decision_sequence": len(decisions) + 1,
                    "activation_sequence": len(decisions) + 1,
                    "trial_id": trial_id,
                    "run_id": run_id,
                    "actor_id": "stranger",
                    "simulation_time": before,
                    "turn_failure": turn.failure_code,
                    "receipt": json_value(turn.receipt),
                    "closed_event_trace_id": closed_id,
                    "action_trace_sequences": [
                        trace.attempt_sequence for trace in research.action_traces[offset:]
                    ],
                }
            )
            if turn.failure_code == "SUBMISSION_ERROR" and precheck_failure is not None:
                decision["protocol_failure"] = precheck_failure
            decisions.append(decision)
            calls += int(decision.get("provider_called") is True)
            if turn.failure_code in ("OBSERVATION_UNAVAILABLE", "SUBMISSION_ERROR"):
                status = "TERMINATED"
                stop_reason = (
                    "OBSERVATION_UNAVAILABLE"
                    if turn.observation is None
                    else precheck_failure or "ENGINE_ERROR"
                )
                break
            if turn.receipt is None or kernel.simulation_time == before:
                failures += 1
                if failures >= policy.max_consecutive_failures:
                    status, stop_reason = "TERMINATED", "CONSECUTIVE_FAILURES"
                    break
            else:
                failures = 0

        traces = research.action_traces
        actions = tuple(trace.request for trace in traces if trace.engine_submitted)
        replay_input = ReplayInput(schedule, actions, kernel.simulation_time)
        report = ReplayReport(
            research.action_results,
            research.system_event_outcomes,
            research.events,
            research.world_snapshot,
            research.state_digest,
            research.simulation_time,
            research.rng_snapshot.draw_count,
        )
        try:
            replay_equal = (
                ReplayHarness(lambda: create_memory_horizon_kernel(run_id=run_id, seed=seed)).run(
                    replay_input
                )
                == report
            )
        except Exception:
            replay_equal = False
        manifest: JsonObject = {
            "schema_version": 3,
            "trial_id": trial_id,
            "run_id": run_id,
            "executed_at": started,
            "finished_at": datetime.now(UTC).isoformat(),
            "engine_manifest": json_value(kernel.manifest),
            "code": source,
            "source_unchanged_during_trial": source == code_identity(),
            "python_runtime": runtime_identity(),
            "provider_identity": identity,
            "configuration_version": CONFIGURATION_VERSION,
            "observation_budget_bytes": pipeline.max_content_bytes,
            "scenario_composition": SCENARIO_VERSION,
            "schedule_digest": state_digest(cast(JsonObject, json_value(schedule))),
            "initial_knowledge_digest": sha256(canonical_json([]).encode("utf-8")).hexdigest(),
            "actor_id": "stranger",
            "controller": "LLMController",
            "memory_policy": memory.policy_id,
            "memory_policy_version": memory.policy_version,
            "model": controller.model,
            "parameters": controller.parameters,
            "prompt_version": PROFILE.version,
            "protocol_version": PROTOCOL_VERSION,
            "minutes_per_tick": 60,
            "target_simulation_hours": 24,
            "target_end_tick": END_TICK,
            "npc_activation_cycle": [],
            "termination_policy": json_value(policy),
            "decisions": len(decisions),
            "provider_calls": calls,
            "simulation_start": 0,
            "simulation_end": kernel.simulation_time,
            "status": status,
            "stop_reason": stop_reason,
            "replay_equal": replay_equal,
            "final_state_digest": kernel.state_digest,
            "wall_seconds": monotonic() - start_wall,
        }
        data: JsonObject = {
            "manifest": manifest,
            "metrics": summarize(
                decisions,
                cast(list[JsonObject], json_value(traces)),
                cast(list[JsonObject], json_value(research.events)),
            ),
            "decisions": cast(list[JsonValue], decisions),
            "activations": [
                {
                    "sequence": index,
                    "actor_id": "stranger",
                    "tick": d["simulation_time"],
                    "decision_sequence": index,
                }
                for index, d in enumerate(decisions, 1)
            ],
            "observations": [observation_json(o) for o in research.observations],
            "observation_attempts": cast(list[JsonValue], pipeline.attempts),
            "action_requests": json_value(actions),
            "action_traces": json_value(traces),
            "action_results": json_value(report.action_results),
            "events": json_value(report.events),
            "system_event_outcomes": json_value(report.system_event_outcomes),
            "knowledge": [],
            "initial_knowledge": [],
            "initial_state": initial_state,
            "final_state": report.final_state,
            "replay_input": json_value(replay_input),
            "engine_report": json_value(report),
            "event_traces": [trace.to_json() for trace in controller.event_traces],
        }
        from journeymap.experiments.memory_horizon_audit import assess_trial

        manifest["record_sha256"] = record_digest(data)
        manifest["research_inclusion"] = assess_trial(data)
        return Record.capture(data)
    finally:
        kernel.close()
