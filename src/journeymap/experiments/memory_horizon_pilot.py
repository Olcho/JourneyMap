"""Concrete mock-only pilot composition. No injectable live provider or live CLI."""

from pathlib import Path
from time import monotonic
from typing import cast

from journeymap.adapters.event_memory import RecencyEventMemory
from journeymap.adapters.llm import Record, observation_json
from journeymap.adapters.memory import NoMemory
from journeymap.adapters.memory_horizon_live import (
    MOCK_VERSION,
    MockHorizonProvider,
    MockOutcome,
    PilotController,
    RecordingMockTransport,
)
from journeymap.application.turns import run_controller_turn
from journeymap.bootstrap import create_memory_horizon_application, create_memory_horizon_kernel
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.core.controller import ControllerActionResult, GamePort, GameSubmissionError
from journeymap.core.handlers import ActionRequest, ValidationContext
from journeymap.core.replay import ReplayHarness, ReplayInput, ReplayReport
from journeymap.experiments.alderwick import BudgetMonitor, json_value
from journeymap.experiments.memory_horizon import END_TICK, protocol_precheck, summarize
from journeymap.experiments.memory_horizon_study import (
    TRIAL_SCHEMA_VERSION,
    derived_metrics,
    validate_cohort,
)
from journeymap.experiments.pilot_journal import AttemptJournal, digest, write_snapshot
from journeymap.scenarios.alderwick.memory_horizon import memory_horizon_schedule


def run_mock_trial(
    cohort: JsonObject,
    allocation_id: str,
    directory: Path,
    *,
    outcomes: tuple[MockOutcome, ...] = (),
) -> Record:
    """Own all lifetimes. Outcomes are inert data; no provider factory parameter."""
    config = validate_cohort(cohort)
    allocation = next(
        a
        for a in cast(list[JsonObject], cohort["allocations"])
        if a["allocation_id"] == allocation_id
    )
    if allocation["execution_status"] != "PLANNED":
        raise ValueError("allocation already executed")
    directory.mkdir(parents=True, exist_ok=False)
    journal = AttemptJournal(directory / "attempts.jsonl")
    run_id = f"{allocation['intended_trial_id']}:run"
    seed = cast(int, cohort["engine_seed"])
    kernel = create_memory_horizon_kernel(run_id=run_id, seed=seed)
    try:
        kernel.boot()
        monitor = BudgetMonitor()
        app, research = create_memory_horizon_application(kernel, pipeline=monitor)
        transport = RecordingMockTransport(outcomes)
        provider = MockHorizonProvider(transport, journal.append)
        condition = cast(str, allocation["condition"])
        memory = (
            NoMemory() if condition == "no-event-memory" else RecencyEventMemory(int(condition[-1]))
        )
        controller = PilotController(provider, memory, config.model, config.parameters)
        manifest: JsonObject = {
            "schema_version": TRIAL_SCHEMA_VERSION,
            "cohort": Record.capture(cohort).data,
            "allocation": Record.capture(allocation).data,
            "provider_identity": {"kind": "fixture", "name": "fake", "version": MOCK_VERSION},
            "engine_manifest": json_value(kernel.manifest),
            "run_id": run_id,
            "status": "RUNNING",
            "stop_reason": None,
            "simulation_end": 0,
            "wall_seconds": 0.0,
            "replay_equal": False,
        }
        initial = kernel.state_snapshot
        journal.append(
            "trial_started",
            {
                "manifest": manifest,
                "initial_state": initial,
                "schedule": json_value(memory_horizon_schedule()),
            },
        )
        game = app.game_for("stranger")
        started = monotonic()
        refusal: str | None = None

        def submit(request: ActionRequest) -> ControllerActionResult:
            nonlocal refusal
            refusal = (
                "WALL_TIMEOUT"
                if monotonic() - started >= config.wall_timeout_seconds
                else protocol_precheck(
                    request,
                    ValidationContext(run_id, kernel.simulation_time, kernel.state_snapshot),
                )
            )
            if refusal:
                raise GameSubmissionError(refusal)
            journal.append("engine_intent", cast(JsonObject, json_value(request)))
            try:
                receipt = game.submit(request)
            except GameSubmissionError:
                journal.append("engine_error", {"reason": "ENGINE_ERROR"})
                raise
            journal.append("engine_receipt", cast(JsonObject, json_value(receipt)))
            return receipt

        port = GamePort(game.observe, submit)
        decisions: list[JsonObject] = []
        failures = calls = 0
        stop = "HORIZON"
        while kernel.simulation_time < END_TICK:
            bound = (
                "WALL_TIMEOUT"
                if monotonic() - started >= config.wall_timeout_seconds
                else "MAX_DECISIONS"
                if len(decisions) >= config.max_decisions
                else "MAX_PROVIDER_CALLS"
                if calls >= config.max_provider_calls
                else None
            )
            if bound:
                stop = bound
                break
            before = kernel.simulation_time
            offset = len(research.action_traces)
            turn = run_controller_turn(port, controller)
            if journal.failed:
                # The turn helper sanitizes exceptions; persistence loss must still
                # stop the experiment before another call or canonical mutation.
                raise OSError("journal persistence failed; inspect durable prefix")
            if turn.observation is None:
                journal.append("observation_error", {"reason": "OBSERVATION_UNAVAILABLE"})
            closed_id = None
            if (
                turn.observation is not None
                and turn.request is not None
                and turn.receipt is not None
                and any(t.engine_submitted for t in research.action_traces[offset:])
            ):
                closed_id = controller.archive.close(
                    turn.observation, turn.request, turn.receipt, engine_submitted=True
                ).event_trace_id
            decision: JsonObject = (
                controller.last_decision.data
                if turn.observation is not None and controller.last_decision is not None
                else {
                    "failure": "OBSERVATION_UNAVAILABLE",
                    "provider_called": False,
                    "observation": None,
                    "action_request": None,
                    "raw_output": None,
                }
            )
            decision.update(
                {
                    "decision_sequence": len(decisions) + 1,
                    "simulation_time": before,
                    "turn_failure": turn.failure_code,
                    "protocol_failure": refusal,
                    "receipt": json_value(turn.receipt),
                    "closed_event_trace_id": closed_id,
                    "action_trace_sequences": [
                        t.attempt_sequence for t in research.action_traces[offset:]
                    ],
                }
            )
            if turn.failure_code == "SUBMISSION_ERROR" and refusal is None:
                decision["failure"] = "ENGINE_ERROR"
            decisions.append(decision)
            journal.append(
                "decision_finished",
                {
                    "decision": decision,
                    "action_traces": json_value(research.action_traces[offset:]),
                    "state_digest": kernel.state_digest,
                    "simulation_time": kernel.simulation_time,
                },
            )
            calls += int(decision.get("provider_called") is True)
            if turn.failure_code in ("OBSERVATION_UNAVAILABLE", "SUBMISSION_ERROR"):
                stop = (
                    "OBSERVATION_UNAVAILABLE"
                    if turn.observation is None
                    else refusal or "ENGINE_ERROR"
                )
                break
            if decision["failure"] == "INFRASTRUCTURE_FAILURE":
                stop = "INFRASTRUCTURE_FAILURE"
                break
            failures = failures + 1 if kernel.simulation_time == before else 0
            if failures >= config.max_consecutive_failures:
                stop = "CONSECUTIVE_FAILURES"
                break
        traces = research.action_traces
        actions = tuple(t.request for t in traces if t.engine_submitted)
        replay_input = ReplayInput(memory_horizon_schedule(), actions, kernel.simulation_time)
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
            equal = (
                ReplayHarness(lambda: create_memory_horizon_kernel(run_id=run_id, seed=seed)).run(
                    replay_input
                )
                == report
            )
        except Exception:
            equal = False
        manifest.update(
            {
                "status": "COMPLETED" if stop == "HORIZON" else "TERMINATED",
                "stop_reason": stop,
                "simulation_end": kernel.simulation_time,
                "wall_seconds": monotonic() - started,
                "replay_equal": equal,
            }
        )
        metrics = summarize(
            decisions,
            cast(list[JsonObject], json_value(traces)),
            cast(list[JsonObject], json_value(research.events)),
        )
        data: JsonObject = {
            "manifest": manifest,
            "metrics": metrics,
            "derived_metrics": derived_metrics(metrics),
            "decisions": cast(list[JsonValue], decisions),
            "initial_state": initial,
            "initial_knowledge": [],
            "observations": [observation_json(o) for o in research.observations],
            "observation_attempts": cast(list[JsonValue], monitor.attempts),
            "action_traces": json_value(traces),
            "event_traces": [t.to_json() for t in controller.archive.traces],
            "replay_input": json_value(replay_input),
            "engine_report": json_value(report),
        }
        data["record_sha256"] = digest(data)
        journal.append("trial_finished", {"record_sha256": data["record_sha256"]})
        write_snapshot(directory / "trial.json", data)
        return Record.capture(data)
    finally:
        kernel.close()
        journal.close()


def run_preflight(
    cohort: JsonObject,
    directory: Path,
    *,
    outcomes: dict[str, tuple[MockOutcome, ...]] | None = None,
) -> JsonObject:
    """A new batch directory retains its initial plan and every allocated attempt."""
    from journeymap.experiments.memory_horizon_pilot_audit import audit_export

    validate_cohort(cohort)
    directory.mkdir(parents=True, exist_ok=False)
    current = Record.capture(cohort).data
    write_snapshot(directory / "cohort-plan.json", current)
    journal = AttemptJournal(directory / "cohort-attempts.jsonl")
    try:
        journal.append("cohort_started", {"plan": current})
        for allocation in cast(list[JsonObject], current["allocations"]):
            if allocation["execution_status"] != "PLANNED":
                continue
            identity = cast(str, allocation["allocation_id"])
            journal.append("allocation_started", {"allocation_id": identity})
            trial = run_mock_trial(
                current, identity, directory / identity, outcomes=(outcomes or {}).get(identity, ())
            )
            assessment = audit_export(directory / identity)
            allocation.update(
                {
                    "execution_status": "FINISHED",
                    "integrity_status": assessment["integrity_status"],
                    "behavioral_denominator": assessment["behavioral_denominator"],
                    "infrastructure_failure": assessment["infrastructure_failure"],
                    "primary_success": cast(JsonObject, trial.data["derived_metrics"])[
                        "primary_success"
                    ],
                    "trial_record_sha256": trial.data["record_sha256"],
                }
            )
            journal.append("allocation_finished", allocation)
        write_snapshot(directory / "cohort-final.json", current)
        return current
    finally:
        journal.close()
