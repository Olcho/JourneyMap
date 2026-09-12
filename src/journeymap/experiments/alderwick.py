"""Bounded single-LLM 24-hour protocol and ActionRequest-only replay proof."""

import json
import math
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import cast

from journeymap.adapters.llm import PROMPT_VERSION, LLMController, Record, observation_json
from journeymap.adapters.social_npc import SocialNpcController
from journeymap.application.observations import ObservationBudgetExceeded, ObservationPipeline
from journeymap.application.turns import run_controller_turn
from journeymap.bootstrap import create_alderwick_application, create_alderwick_kernel
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json, state_digest
from journeymap.core.controller import ControllerActionResult, GamePort, GameSubmissionError
from journeymap.core.handlers import ActionRequest
from journeymap.core.observations import PerceptionContext
from journeymap.core.replay import ReplayHarness, ReplayInput, ReplayReport
from journeymap.core.scheduler import ScenarioSchedule, ScheduledEventSpec
from journeymap.scenarios.alderwick.experiment import (
    EXPERIMENT_SCENARIO_VERSION,
    experiment_schedule,
)
from journeymap.scenarios.alderwick.fixture import ACTOR_LOCATIONS
from journeymap.scenarios.alderwick.social import NPC_ACTIVATIONS, social_initial_knowledge

PROTOCOL_VERSION = "alderwick-24h-2"
END_TICK = 24


def json_value(value: object) -> JsonValue:
    def default(item: object) -> object:
        if is_dataclass(item) and not isinstance(item, type):
            return asdict(item)
        raise TypeError("not an export value")

    return cast(JsonValue, json.loads(json.dumps(value, default=default, allow_nan=False)))


@dataclass(frozen=True, slots=True)
class TrialPolicy:
    max_decisions: int = 64
    max_provider_calls: int = 64
    max_consecutive_failures: int = 3
    wall_timeout_seconds: float = 300

    def __post_init__(self) -> None:
        for value in (self.max_decisions, self.max_provider_calls, self.max_consecutive_failures):
            if type(value) is not int or value <= 0:
                raise ValueError("trial bounds must be positive integers")
        if (
            isinstance(self.wall_timeout_seconds, bool)
            or not math.isfinite(self.wall_timeout_seconds)
            or self.wall_timeout_seconds <= 0
        ):
            raise ValueError("invalid wall timeout")


class BudgetMonitor(ObservationPipeline):
    def __init__(self, *, max_content_bytes: int = 65_536) -> None:
        super().__init__(max_content_bytes=max_content_bytes)
        self.attempts: list[JsonObject] = []

    def assemble(self, context: PerceptionContext) -> JsonObject:
        try:
            content = super().assemble(context)
        except ObservationBudgetExceeded as error:
            self.attempts.append(
                {
                    "actor_id": context.actor_id,
                    "tick": context.simulation_time,
                    "bytes": error.content_bytes,
                    "overflow": True,
                }
            )
            raise
        self.attempts.append(
            {
                "actor_id": context.actor_id,
                "tick": context.simulation_time,
                "bytes": len(canonical_json(content).encode("utf-8")),
                "overflow": False,
            }
        )
        return content


def code_identity() -> JsonObject:
    root = Path(__file__).resolve().parents[3]
    commit: str | None = None
    dirty: bool | None = None
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        pass
    digest = sha256()
    for path in sorted((root / "src" / "journeymap").rglob("*.py")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return {"git_commit": commit, "working_tree_dirty": dirty, "source_sha256": digest.hexdigest()}


def run_trial(
    controller: LLMController,
    *,
    trial_id: str,
    seed: int = 42,
    policy: TrialPolicy | None = None,
    monitor: BudgetMonitor | None = None,
    provider_name: str = "fake",
    provider_version: str | None = None,
) -> Record:
    if not trial_id or type(trial_id) is not str:
        raise ValueError("trial_id required")
    policy = policy if policy is not None else TrialPolicy()
    if not controller.uses_no_memory:
        raise ValueError("first protocol requires NoMemory")
    # One fresh controller per trial; no prior actor context or cross-trial state.
    if controller.last_decision is not None:
        raise ValueError("trial requires a fresh controller")
    started = datetime.now(UTC).isoformat()
    start_wall = monotonic()
    run_id = f"{trial_id}:run"
    kernel = create_alderwick_kernel(
        run_id=run_id, seed=seed, social=True, resources=True, experiment=True
    )
    pipeline = monitor if monitor is not None else BudgetMonitor()
    kernel.boot()
    try:
        schedule = experiment_schedule()
        for event in schedule.events:
            kernel.schedule(event)
        app, research = create_alderwick_application(
            kernel, social=True, resources=True, experiment=True, pipeline=pipeline
        )
        initial_state = kernel.state_snapshot
        decisions: list[JsonValue] = []
        activations: list[JsonValue] = []
        npc = SocialNpcController()
        npc_index = 0
        calls = failures = 0
        status = "COMPLETED"
        stop_reason = "HORIZON"

        def limited_port(actor: str) -> GamePort:
            game = app.game_for(actor)

            def submit(request: ActionRequest) -> ControllerActionResult:
                if monotonic() - start_wall >= policy.wall_timeout_seconds:
                    raise GameSubmissionError("WALL_TIMEOUT")
                # All M4 Alderwick routes cost two ticks; domain handler remains
                # authoritative about existence/passability and start/completion.
                duration = 2 if request.action_type == "MOVE" else 1
                if request.action_type in ("WAIT", "REST"):
                    duration = cast(int, request.payload["duration"])
                if kernel.simulation_time + duration > END_TICK:
                    raise GameSubmissionError("HORIZON_ACTION")
                return game.submit(request)

            return GamePort(game.observe, submit)

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
            before = kernel.simulation_time
            trace_offset = len(research.action_traces)
            turn = run_controller_turn(limited_port("stranger"), controller)
            decision: JsonObject = (
                controller.last_decision.data
                if turn.observation is not None and controller.last_decision is not None
                else {
                    "observation": None,
                    "model": controller.model,
                    "parameters": controller.parameters,
                    "prompt_version": PROMPT_VERSION,
                    "memory_policy": "NoMemory",
                    "memory_observation_ids": [],
                    "provider_request": None,
                    "raw_output": None,
                    "provider_metadata": {},
                    "parsed_wire_candidate": None,
                    "parsed_candidate": None,
                    "action_request": None,
                    "parser_outcome": "NOT_RUN",
                    "failure": "OBSERVATION_UNAVAILABLE",
                    "provider_failure": None,
                    "provider_called": False,
                    "latency_seconds": None,
                    "retry": {"attempt": 0, "repair": False, "automatic_retry": False},
                }
            )
            decision.update(
                {
                    "schema_version": 1,
                    "trial_id": trial_id,
                    "run_id": run_id,
                    "decision_sequence": len(decisions) + 1,
                    "activation_sequence": len(activations) + 1,
                    "actor_id": "stranger",
                    "simulation_time": before,
                    "provider": provider_name,
                    "provider_version": provider_version,
                    "turn_failure": turn.failure_code,
                    "receipt": json_value(turn.receipt),
                    "action_trace_sequences": [
                        trace.attempt_sequence for trace in research.action_traces[trace_offset:]
                    ],
                }
            )
            calls += int(decision.get("provider_called") is True)
            # Distinguish policy refusal from engine errors, without changing the
            # existing turn helper/public submission exception contract.
            if (
                turn.failure_code == "SUBMISSION_ERROR"
                and len(research.action_traces) == trace_offset
            ):
                decision["protocol_failure"] = (
                    "WALL_TIMEOUT"
                    if monotonic() - start_wall >= policy.wall_timeout_seconds
                    else "HORIZON_ACTION"
                )
            decisions.append(decision)
            activations.append(
                {
                    "sequence": len(activations) + 1,
                    "actor_id": "stranger",
                    "tick": before,
                    "decision_sequence": len(decisions),
                }
            )
            if turn.failure_code == "OBSERVATION_UNAVAILABLE":
                status, stop_reason = "TERMINATED", "OBSERVATION_UNAVAILABLE"
                break
            if turn.failure_code == "SUBMISSION_ERROR":
                status, stop_reason = (
                    "TERMINATED",
                    str(decision.get("protocol_failure", "ENGINE_ERROR")),
                )
                break
            if turn.receipt is None or kernel.simulation_time == before:
                failures += 1
                if failures >= policy.max_consecutive_failures:
                    status, stop_reason = "TERMINATED", "CONSECUTIVE_FAILURES"
                    break
                continue
            failures = 0
            if kernel.simulation_time >= END_TICK:
                break
            actor = NPC_ACTIVATIONS[npc_index % len(NPC_ACTIVATIONS)]
            npc_index += 1
            npc_start = kernel.simulation_time
            npc_turn = run_controller_turn(limited_port(actor), npc)
            activations.append(
                {
                    "sequence": len(activations) + 1,
                    "actor_id": actor,
                    "tick": npc_start,
                    "request": json_value(npc_turn.request),
                    "receipt": json_value(npc_turn.receipt),
                    "failure": npc_turn.failure_code,
                }
            )
            if npc_turn.failure_code:
                status, stop_reason = "TERMINATED", "NPC_TURN_FAILURE"
                break

        traces = research.action_traces
        actions = tuple(trace.request for trace in traces if trace.engine_submitted)
        replay_input = ReplayInput(schedule, actions, kernel.simulation_time)
        expected = ReplayReport(
            research.action_results,
            research.system_event_outcomes,
            research.events,
            research.world_snapshot,
            research.state_digest,
            research.simulation_time,
            research.rng_snapshot.draw_count,
        )
        try:
            replay = ReplayHarness(
                lambda: create_alderwick_kernel(
                    run_id=run_id, seed=seed, social=True, resources=True, experiment=True
                )
            ).run(replay_input)
            replay_equal = replay == expected
        except Exception:
            replay_equal = False
        observations = [observation_json(item) for item in research.observations]
        knowledge = [
            record.to_json()
            for actor, _ in ACTOR_LOCATIONS
            for record in research.knowledge_history(actor)
        ]
        metrics = summarize(decisions, pipeline.attempts, actions, expected, knowledge)
        manifest: JsonObject = {
            "schema_version": 1,
            "trial_id": trial_id,
            "run_id": run_id,
            "executed_at": started,
            "finished_at": datetime.now(UTC).isoformat(),
            "engine_manifest": json_value(kernel.manifest),
            "code": code_identity(),
            "scenario_composition": EXPERIMENT_SCENARIO_VERSION,
            "schedule_digest": state_digest(cast(JsonObject, json_value(schedule))),
            "initial_knowledge_digest": sha256(
                canonical_json(json_value(social_initial_knowledge(run_id))).encode("utf-8")
            ).hexdigest(),
            "actor_id": "stranger",
            "controller": "LLMController",
            "memory_policy": "NoMemory",
            "provider": provider_name,
            "provider_version": provider_version,
            "model": controller.model,
            "response_models": json_value(
                sorted(
                    {
                        str(metadata["model"])
                        for decision in decisions
                        if isinstance(decision, dict)
                        and isinstance(metadata := decision.get("provider_metadata"), dict)
                        and isinstance(metadata.get("model"), str)
                    }
                )
            ),
            "parameters": controller.parameters,
            "prompt_version": PROMPT_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "minutes_per_tick": 60,
            "target_simulation_hours": 24,
            "target_end_tick": END_TICK,
            "npc_activation_cycle": list(NPC_ACTIVATIONS),
            "termination_policy": json_value(policy),
            "decisions": len(decisions),
            "provider_calls": calls,
            "simulation_start": 0,
            "simulation_end": kernel.simulation_time,
            "simulation_hours": kernel.simulation_time,
            "final_state_digest": kernel.state_digest,
            "status": status,
            "stop_reason": stop_reason,
            "replay_equal": replay_equal,
            "wall_seconds": monotonic() - start_wall,
        }
        return Record.capture(
            {
                "manifest": manifest,
                "metrics": metrics,
                "decisions": decisions,
                "activations": activations,
                "observations": json_value(observations),
                "observation_attempts": cast(list[JsonValue], pipeline.attempts),
                "action_requests": json_value(actions),
                "action_traces": json_value(traces),
                "action_results": json_value(expected.action_results),
                "events": json_value(expected.events),
                "system_event_outcomes": json_value(expected.system_event_outcomes),
                "knowledge": json_value(knowledge),
                "initial_knowledge": json_value(social_initial_knowledge(run_id)),
                "initial_state": initial_state,
                "final_state": expected.final_state,
                "replay_input": json_value(replay_input),
                "engine_report": json_value(expected),
            }
        )
    finally:
        kernel.close()


def summarize(
    decisions: list[JsonValue],
    attempts: list[JsonObject],
    actions: tuple[ActionRequest, ...],
    report: ReplayReport,
    knowledge: list[JsonObject],
) -> JsonObject:
    sizes = [cast(int, item["bytes"]) for item in attempts if not item["overflow"]]
    records = [item for item in decisions if isinstance(item, dict)]
    llm = [action for action in actions if action.actor_id == "stranger"]
    llm_ids = {action.action_request_id for action in llm}
    knowledge_counts = Counter(cast(str, item["source_kind"]) for item in knowledge)
    usage: Counter[str] = Counter()
    latencies = []
    for record in records:
        latency = record.get("latency_seconds")
        if isinstance(latency, (int, float)):
            latencies.append(latency)
        metadata = record.get("provider_metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("usage"), dict):
            tokens = cast(JsonObject, metadata["usage"])
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                value = tokens.get(key)
                if type(value) is int:
                    usage[key] += value
    return {
        "engine_action_types": dict(Counter(action.action_type for action in actions)),
        "llm_action_types": dict(Counter(action.action_type for action in llm)),
        "engine_statuses": dict(Counter(result.status for result in report.action_results)),
        "llm_statuses": dict(
            Counter(
                result.status
                for result in report.action_results
                if result.action_request_id in llm_ids
            )
        ),
        "invalid_outputs": sum(item.get("failure") == "INVALID_OUTPUT" for item in records),
        "provider_failures": sum(item.get("failure") == "PROVIDER_ERROR" for item in records),
        "provider_calls": sum(item.get("provider_called") is True for item in records),
        "observation_bytes": {
            "max": max(sizes, default=0),
            "mean": sum(sizes) / len(sizes) if sizes else 0,
            "distribution": json_value(sizes),
            "overflow_count": sum(bool(item["overflow"]) for item in attempts),
        },
        "knowledge_count": len(knowledge),
        "knowledge_sources": dict(knowledge_counts),
        "movement": [
            json_value(event) for event in report.events if event.event_type == "ActorMoved"
        ],
        "final_resources": {
            key: report.final_state[key] for key in ("survival", "inventory", "trade")
        },
        "tokens": dict(usage),
        "latency_seconds_total": sum(latencies),
        "cost": None,
        "cost_note": "No cost returned; no inferred price or subjective quality score.",
    }


def export_trial(trial: Record, directory: Path) -> None:
    """Fresh directory; manifest written last is the completion marker.

    Export failure leaves the immutable in-memory trial and engine outcome intact.
    Existing experiments are never silently replaced. Partial directories can be
    inspected; retry into a different new directory.
    """
    directory.mkdir(parents=True, exist_ok=False)
    data = trial.data
    hashes: JsonObject = {}
    for name, value in sorted(data.items()):
        if name == "manifest":
            continue
        if isinstance(value, list):
            filename = f"{name}.jsonl"
            text = "".join(
                json.dumps(
                    row, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
                )
                + "\n"
                for row in value
            )
        else:
            filename = f"{name}.json"
            text = (
                json.dumps(
                    value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False
                )
                + "\n"
            )
        payload = text.encode("utf-8")
        (directory / filename).write_bytes(payload)
        hashes[filename] = sha256(payload).hexdigest()
    manifest = cast(JsonObject, data["manifest"])
    manifest["files_sha256"] = hashes
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def replay_export(directory: Path) -> ReplayReport:
    """Rehydrate the existing ReplayInput only; never instantiate a Controller."""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    for filename, digest in manifest["files_sha256"].items():
        if (
            Path(filename).name != filename
            or sha256((directory / filename).read_bytes()).hexdigest() != digest
        ):
            raise ValueError("export integrity failure")
    value = json.loads((directory / "replay_input.json").read_text(encoding="utf-8"))
    schedule = value["schedule"]
    replay_input = ReplayInput(
        ScenarioSchedule(
            schedule["scenario_id"],
            schedule["scenario_version"],
            tuple(ScheduledEventSpec(**item) for item in schedule["events"]),
        ),
        tuple(ActionRequest(**item) for item in value["actions"]),
        value["advance_to"],
    )
    engine = manifest["engine_manifest"]
    report = ReplayHarness(
        lambda: create_alderwick_kernel(
            run_id=engine["run_id"],
            seed=engine["seed"],
            social=True,
            resources=True,
            experiment=engine["scenario_version"] == EXPERIMENT_SCENARIO_VERSION,
        )
    ).run(replay_input)
    expected = json.loads((directory / "engine_report.json").read_text(encoding="utf-8"))
    if json_value(report) != expected:
        raise ValueError("replayed engine report differs")
    return report
