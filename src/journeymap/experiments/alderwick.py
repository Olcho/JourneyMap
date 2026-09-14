"""Bounded single-LLM 24-hour protocol and ActionRequest-only replay proof."""

import json
import math
from collections import Counter
from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import cast

from journeymap.adapters.event_memory import EVENT_MEMORY_PROTOCOL_VERSION, RecencyEventMemory
from journeymap.adapters.llm import (
    CONFIGURATION_VERSION,
    PROMPT_VERSION,
    LLMController,
    Record,
    observation_json,
)
from journeymap.adapters.memory import NoMemory
from journeymap.adapters.social_npc import SocialNpcController
from journeymap.application.observations import ObservationBudgetExceeded, ObservationPipeline
from journeymap.application.turns import run_controller_turn
from journeymap.bootstrap import create_alderwick_application, create_alderwick_kernel
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json, state_digest
from journeymap.core.controller import ControllerActionResult, GamePort, GameSubmissionError
from journeymap.core.handlers import ActionRequest, ActionValidationError, ValidationContext
from journeymap.core.kernel import SimulationKernel
from journeymap.core.observations import PerceptionContext
from journeymap.core.replay import ReplayHarness, ReplayInput, ReplayReport
from journeymap.core.scheduler import ScenarioSchedule, ScheduledEventSpec
from journeymap.experiments.inclusion import (
    TRIAL_FIELDS,
    assess_inclusion,
    inclusion_result,
    record_digest,
)
from journeymap.experiments.provenance import code_identity, runtime_identity
from journeymap.modules.movement.handlers import MoveHandler
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

    @property
    def max_content_bytes(self) -> int:
        return self._max_content_bytes

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


def action_duration(request: ActionRequest, context: ValidationContext) -> int:
    """Trusted protocol precheck; MOVE timing comes from its domain handler.

    Start-invalid MOVE still reaches Game/engine and is recorded as REJECTED.
    No Ground Truth capability is passed to a Controller or Provider.
    """
    if request.action_type == "MOVE":
        handler = MoveHandler()
        try:
            handler.validate(request, context)
            return handler.prepare(request, context).duration
        except ActionValidationError:
            return 0
    if request.action_type in ("WAIT", "REST"):
        return cast(int, request.payload["duration"])
    return 1


def run_trial(
    controller: LLMController,
    *,
    trial_id: str,
    seed: int = 42,
    policy: TrialPolicy | None = None,
    monitor: BudgetMonitor | None = None,
    provider_name: str | None = None,
    provider_version: str | None = None,
    protocol_version: str = PROTOCOL_VERSION,
) -> Record:
    if not trial_id or type(trial_id) is not str:
        raise ValueError("trial_id required")
    policy = policy if policy is not None else TrialPolicy()
    event_mode = protocol_version == EVENT_MEMORY_PROTOCOL_VERSION
    if protocol_version not in (PROTOCOL_VERSION, EVENT_MEMORY_PROTOCOL_VERSION):
        raise ValueError("unsupported experiment protocol")
    if event_mode != controller.event_memory_enabled:
        raise ValueError("protocol and Event Memory context must agree")
    if event_mode and type(controller.event_memory_policy) not in (NoMemory, RecencyEventMemory):
        raise ValueError("Phase 1 permits only No Event Memory or Recency k=1")
    if not event_mode and not controller.uses_no_memory:
        raise ValueError("first protocol requires NoMemory")
    # One fresh controller per trial; no prior actor context or cross-trial state.
    if controller.last_decision is not None:
        raise ValueError("trial requires a fresh controller")
    identity = controller.provider_identity
    if (provider_name is not None and provider_name != identity["name"]) or (
        provider_version is not None and provider_version != identity["version"]
    ):
        raise ValueError("caller provider identity differs from adapter")
    provider_name = cast(str, identity["name"])
    provider_version = cast(str, identity["version"])
    source = code_identity()
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
        precheck_failure: str | None = None

        def limited_port(actor: str) -> GamePort:
            game = app.game_for(actor)

            def submit(request: ActionRequest) -> ControllerActionResult:
                nonlocal precheck_failure
                precheck_failure = None
                if monotonic() - start_wall >= policy.wall_timeout_seconds:
                    precheck_failure = "WALL_TIMEOUT"
                    raise GameSubmissionError("WALL_TIMEOUT")
                try:
                    duration = action_duration(
                        request,
                        ValidationContext(run_id, kernel.simulation_time, kernel.state_snapshot),
                    )
                except Exception:
                    precheck_failure = "TIMING_PRECHECK_ERROR"
                    raise GameSubmissionError("TIMING_PRECHECK_ERROR") from None
                if kernel.simulation_time + duration > END_TICK:
                    precheck_failure = "HORIZON_ACTION"
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
            closed_trace_id: str | None = None
            if (
                event_mode
                and turn.observation is not None
                and turn.request is not None
                and turn.receipt is not None
                and any(
                    trace.engine_submitted and trace.request == turn.request
                    for trace in research.action_traces[trace_offset:]
                )
            ):
                closed_trace_id = controller.close_event_trace(
                    turn.observation, turn.request, turn.receipt, engine_submitted=True
                ).event_trace_id
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
            if event_mode:
                decision["closed_event_trace_id"] = closed_trace_id
            # Distinguish policy refusal from engine errors, without changing the
            # existing turn helper/public submission exception contract.
            if turn.failure_code == "SUBMISSION_ERROR" and precheck_failure is not None:
                decision["protocol_failure"] = precheck_failure
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
            "schema_version": 3 if event_mode else 2,
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
            "scenario_composition": EXPERIMENT_SCENARIO_VERSION,
            "schedule_digest": state_digest(cast(JsonObject, json_value(schedule))),
            "initial_knowledge_digest": sha256(
                canonical_json(json_value(social_initial_knowledge(run_id))).encode("utf-8")
            ).hexdigest(),
            "actor_id": "stranger",
            "controller": "LLMController",
            "memory_policy": (
                controller.event_memory_policy.policy_id
                if controller.event_memory_policy is not None
                else "NoMemory"
            ),
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
            "prompt_version": controller.prompt_version,
            "protocol_version": protocol_version,
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
        data: JsonObject = {
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
        if event_mode:
            from journeymap.experiments.event_memory import assess_event_memory

            assert controller.event_memory_policy is not None
            manifest["memory_policy_version"] = controller.event_memory_policy.policy_version
            data["event_traces"] = [trace.to_json() for trace in controller.event_traces]
            manifest["record_sha256"] = record_digest(data)
            manifest["research_inclusion"] = assess_event_memory(data)
        else:
            manifest["record_sha256"] = record_digest(data)
            manifest["research_inclusion"] = assess_inclusion(data)
        return Record.capture(data)
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
    if manifest.get("schema_version") not in (1, 2, 3):
        raise ValueError("unsupported export schema")
    for filename, digest in manifest["files_sha256"].items():
        if (
            Path(filename).name != filename
            or sha256((directory / filename).read_bytes()).hexdigest() != digest
        ):
            raise ValueError("export integrity failure")
    data = (
        _read_new_export(directory, manifest) if manifest.get("schema_version") in (2, 3) else None
    )
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

    def factory() -> SimulationKernel:
        kernel = create_alderwick_kernel(
            run_id=engine["run_id"],
            seed=engine["seed"],
            social=True,
            resources=True,
            experiment=engine["scenario_version"] == EXPERIMENT_SCENARIO_VERSION,
        )
        if data is not None and (
            json_value(kernel.manifest) != engine
            or kernel.state_snapshot != data["initial_state"]
            or json_value(social_initial_knowledge(engine["run_id"])) != data["initial_knowledge"]
        ):
            kernel.close()
            raise ValueError("replay configuration differs from recorded initial inputs")
        return kernel

    report = ReplayHarness(factory).run(replay_input)
    expected = json.loads((directory / "engine_report.json").read_text(encoding="utf-8"))
    if json_value(report) != expected:
        raise ValueError("replayed engine report differs")
    return report


def _read_new_export(directory: Path, manifest: JsonObject) -> JsonObject:
    """New records require the complete inventory and config+artifact seal."""
    data: JsonObject = {"manifest": manifest}
    hashes = cast(JsonObject, manifest["files_sha256"])
    object_fields = {"metrics", "initial_state", "final_state", "replay_input", "engine_report"}
    fields = (
        TRIAL_FIELDS | {"event_traces"} if manifest.get("schema_version") == 3 else TRIAL_FIELDS
    )
    filenames = {
        name: f"{name}.json" if name in object_fields else f"{name}.jsonl"
        for name in fields - {"manifest"}
    }
    if set(hashes) != set(filenames.values()):
        raise ValueError("export integrity failure: incomplete inventory")
    for name, filename in filenames.items():
        payload = (directory / filename).read_bytes()
        if sha256(payload).hexdigest() != hashes[filename]:
            raise ValueError("export integrity failure")
        text = payload.decode("utf-8")
        data[name] = (
            json.loads(text)
            if name in object_fields
            else [json.loads(line) for line in text.splitlines()]
        )
    if manifest.get("record_sha256") != record_digest(data):
        raise ValueError("export record integrity failure")
    return data


def audit_export(directory: Path) -> JsonObject:
    """Research admission rechecks files/config and engine replay, provider-free.

    Historical v1 remains replayable but is not silently admitted under v2 rules.
    Audit failures never modify or discard the original artifact.
    """
    manifest: JsonObject = {}
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("invalid manifest")
        if manifest.get("schema_version") not in (2, 3):
            return inclusion_result(["NEW_PROVENANCE_REQUIRED"])
        data = _read_new_export(directory, manifest)
        if manifest.get("schema_version") == 3:
            from journeymap.experiments.event_memory import assess_event_memory

            result = assess_event_memory(data)
        else:
            result = assess_inclusion(data)
        try:
            replay_export(directory)
        except Exception:
            result = inclusion_result([*cast(list[str], result["reasons"]), "REPLAY_MISMATCH"])
    except (OSError, KeyError, TypeError, ValueError):
        result = inclusion_result(["EXPORT_INTEGRITY"])
    stored = manifest.get("research_inclusion") if isinstance(manifest, dict) else None
    matches = stored == result
    audit = inclusion_result(
        [
            *cast(list[str], result["reasons"]),
            *([] if matches else ["STORED_INCLUSION_MISMATCH"]),
        ]
    )
    audit.update(
        {
            "stored_inclusion": stored,
            "recomputed_inclusion": result,
            "stored_inclusion_matches": matches,
        }
    )
    if isinstance(manifest, dict) and manifest.get("schema_version") == 3:
        from journeymap.experiments.event_memory import CORRECTNESS_VERSION

        audit["policy_version"] = CORRECTNESS_VERSION
    return audit
