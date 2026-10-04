"""One-shot offline pickup diagnostic. Trusted orchestration owns all authority."""

import subprocess
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import cast

from journeymap.adapters.event_memory import (
    EventMemoryPolicy,
    EventTraceArchive,
    RecencyEventMemory,
)
from journeymap.adapters.llm import (
    Record,
    event_memory_input,
    observation_json,
    parse_candidate,
    strict_json,
)
from journeymap.adapters.memory import NoMemory
from journeymap.adapters.memory_horizon_live import validate_candidate
from journeymap.adapters.pickup_cue_prompt import (
    CONFIGURATION_VERSION,
    INSTRUCTIONS_SHA256,
    MODEL,
    PROFILE,
    Fixture,
    FullPrefixMemory,
    PickupFixtureProvider,
    delivered_cue,
    diagnostic_input,
)
from journeymap.adapters.provider import (
    ProviderFailure,
    ProviderRequest,
    RawModelResponse,
    provider_identity,
)
from journeymap.application.research import ResearchView
from journeymap.bootstrap import create_pickup_cue_application, create_pickup_cue_kernel
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.core.controller import GamePort
from journeymap.core.handlers import ActionRequest
from journeymap.core.observations import Observation
from journeymap.core.replay import ReplayReport
from journeymap.experiments.alderwick import json_value
from journeymap.experiments.memory_horizon_audit import obj, rows
from journeymap.experiments.pilot_journal import digest
from journeymap.experiments.provenance import code_identity, runtime_identity
from journeymap.scenarios.alderwick.pickup_cue import (
    SCENARIO_VERSION,
    TARGETS,
    pickup_schedule,
    prefix_intents,
)

PROTOCOL_VERSION = "alderwick-pickup-cue-offline-1"
EXPORT_VERSION = "pickup-cue-offline-record-1"
AUDIT_VERSION = "pickup-cue-offline-correctness-1"
CONDITIONS = ("no-event-memory", "recency-k1", "recency-k2", "recency-k3", "full-prefix")
DEFAULT_FIXTURE = Fixture()


def memory_policy(condition: str) -> EventMemoryPolicy:
    if condition not in CONDITIONS:
        raise ValueError("unknown diagnostic condition")
    if condition == "no-event-memory":
        return NoMemory()
    if condition == "full-prefix":
        return FullPrefixMemory()
    return RecencyEventMemory(int(condition[-1]))


def evidence(context: Observation, archive: EventTraceArchive, condition: str) -> JsonObject:
    policy = memory_policy(condition)
    raw, provenance = event_memory_input(archive.begin(context), policy)
    inputs = diagnostic_input(raw)
    prompt = PROFILE.render(inputs)
    request = ProviderRequest(prompt, PROFILE.version, MODEL, CONFIGURATION_VERSION, {})
    serialized = canonical_json(inputs)
    current = canonical_json(inputs["observation"])
    return {
        **provenance,
        "memory_policy": policy.policy_id,
        "memory_policy_version": policy.policy_version,
        "model_visible_input": inputs,
        "input_canonical": serialized,
        "input_bytes": len(serialized.encode("utf-8")),
        "input_sha256": sha256(serialized.encode()).hexdigest(),
        "current_canonical": current,
        "current_sha256": sha256(current.encode()).hexdigest(),
        "cue_present": delivered_cue(inputs) is not None,
        "provider_request": json_value(request),
        "prompt_sha256": sha256(prompt.encode()).hexdigest(),
        "prompt_bytes": len(prompt.encode("utf-8")),
    }


def parse_response(
    response: RawModelResponse, observation: Observation
) -> tuple[JsonObject, ActionRequest | None]:
    parsed: JsonObject = {"candidate": None, "parser_outcome": "NOT_RUN", "failure": None}
    if response.metadata.get("refusal"):
        parsed["failure"] = "REFUSAL"
    elif response.metadata.get("status") != "completed":
        parsed["failure"] = "INCOMPLETE"
    else:
        parsed["parser_outcome"] = "REJECTED"
        try:
            candidate = strict_json(response.text)
            parsed["candidate"] = candidate
            validate_candidate(candidate)
            action = parse_candidate(candidate, observation)
            parsed["parser_outcome"] = "ACCEPTED"
            return parsed, action
        except (ValueError, TypeError, RuntimeError):
            parsed["failure"] = "INVALID_OUTPUT"
    return parsed, None


def step_record(phase: str) -> JsonObject:
    return {
        "phase": phase,
        "observation": None,
        "request": None,
        "receipt": None,
        "closed_trace_id": None,
        "failure": None,
        "engine_submitted": False,
    }


def submit_step(
    step: JsonObject,
    observation: Observation,
    request: ActionRequest,
    game: GamePort,
    archive: EventTraceArchive,
    research: ResearchView,
) -> None:
    step["request"] = json_value(request)
    offset = len(research.action_traces)
    try:
        receipt = game.submit(request)
    except Exception:
        step["failure"] = "ENGINE_ERROR"
        return
    finally:
        step["engine_submitted"] = any(
            t.engine_submitted and t.request == request for t in research.action_traces[offset:]
        )
    step["receipt"] = json_value(receipt)
    try:
        step["closed_trace_id"] = archive.close(
            observation,
            request,
            receipt,
            engine_submitted=step["engine_submitted"] is True,
        ).event_trace_id
    except Exception:
        step["failure"] = "RECORDING_ERROR"


def engine_report(research: ResearchView) -> ReplayReport:
    return ReplayReport(
        research.action_results,
        research.system_event_outcomes,
        research.events,
        research.world_snapshot,
        research.state_digest,
        research.simulation_time,
        research.rng_snapshot.draw_count,
    )


def evaluate(data: JsonObject) -> JsonObject:
    """Execution and actual arrival are separate axes; no success from intent alone."""
    decision = obj(data["decision"])
    request = decision["request"]
    failure = decision["failure"]
    report = obj(data["engine_report"])
    route = destination = None
    action_type = None
    execution = "NOT_SUBMITTED"
    success: bool | None = False
    if isinstance(request, dict):
        action_type = request["action_type"]
        route = obj(request["payload"]).get("route_id")
        routes = obj(obj(data["initial_state"])["movement"])["routes"]
        if isinstance(route, str) and route in obj(routes):
            destination = obj(obj(routes)[route])["destination"]
        if decision["engine_submitted"]:
            matches = [
                r
                for r in rows(report["action_results"])
                if r["action_request_id"] == request["action_request_id"]
            ]
            execution = cast(str, matches[0]["status"]) if len(matches) == 1 else "UNKNOWN"
            final_place = obj(obj(obj(report["final_state"])["movement"])["positions"])
            success = (
                action_type == "MOVE"
                and execution == "SUCCEEDED"
                and obj(final_place["stranger"])["location_id"] == obj(data["case"])["target"]
                and any(
                    e["event_type"] == "ActorMoved"
                    and e["source_ref"] == request["action_request_id"]
                    and obj(e["payload"])["destination"] == obj(data["case"])["target"]
                    for e in rows(report["events"])
                )
            )
            if execution == "UNKNOWN":
                success = None
    if data["prefix_status"] != "READY" or failure in (
        "OBSERVATION_ERROR",
        "RECORDING_ERROR",
        "ENGINE_ERROR",
        "PROVIDER_ERROR",
        "TRANSPORT_ERROR",
    ):
        success = None
    responded = decision.get("raw_response") is not None
    return {
        "action_type": action_type,
        "selected_route": route,
        "selected_destination": destination,
        "execution": execution,
        "task_success": success,
        "failure": failure,
        "preparation_failure": (
            None
            if data["prefix_status"] == "READY"
            else rows(data["prefix"])[-1]["failure"] or "ENGINE_UNSUCCESSFUL"
        ),
        "response_obtained": responded,
        "behavioral_denominator": responded,
        "infrastructure_before_response": not responded,
        "choice_withheld": action_type == "WAIT",
    }


def seal(data: JsonObject) -> None:
    data["record_sha256"] = digest({k: v for k, v in data.items() if k != "record_sha256"})


def run_case(
    *,
    case_id: str,
    target: str,
    d: int,
    condition: str,
    fixture: Fixture = DEFAULT_FIXTURE,
    seed: int = 42,
) -> Record:
    """No Provider/transport injection: exact inert fixture data, fresh owned runtime."""
    if type(fixture) is not Fixture:
        raise TypeError("offline diagnostic accepts only concrete inert Fixture")
    if type(case_id) is not str or not case_id or type(seed) is not int:
        raise ValueError("case id and integer seed required")
    intents = prefix_intents(d)
    memory_policy(condition)
    kernel = create_pickup_cue_kernel(run_id=f"{case_id}:run", target=target, seed=seed)
    kernel.boot()
    try:
        app, research = create_pickup_cue_application(kernel)
        game, archive = app.game_for("stranger"), EventTraceArchive()
        provider = PickupFixtureProvider(fixture)
        source = code_identity()
        source["branch"] = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        decision = step_record("evaluation")
        decision.update(
            {
                "input": None,
                "raw_response": None,
                "provider_called": False,
                "candidate": None,
                "parser_outcome": "NOT_RUN",
            }
        )
        data: JsonObject = {
            "export_version": EXPORT_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "scenario_version": SCENARIO_VERSION,
            "audit_version": AUDIT_VERSION,
            "prompt_version": PROFILE.version,
            "instructions_sha256": INSTRUCTIONS_SHA256,
            "instructions_bytes": len(PROFILE.instructions.encode("utf-8")),
            "source": source,
            "runtime": runtime_identity(),
            "engine_manifest": json_value(kernel.manifest),
            "case": {
                "case_id": case_id,
                "target": target,
                "d": d,
                "condition": condition,
                "seed": seed,
            },
            "fixture": cast(JsonObject, asdict(fixture)),
            "provider_identity": provider_identity(provider),
            "initial_state": kernel.state_snapshot,
            "initial_knowledge": [],
            "schedule": json_value(pickup_schedule()),
            "prefix": [],
            "prefix_status": "READY",
            "checkpoint": None,
            "decision": decision,
        }
        for intent in intents:
            step = step_record("preparation")
            cast(list[JsonValue], data["prefix"]).append(step)
            try:
                observation = game.observe()
            except Exception:
                step["failure"] = "OBSERVATION_ERROR"
            else:
                step["observation"] = observation_json(observation)
                try:
                    archive.begin(observation)
                    request = parse_candidate(intent, observation)
                except Exception:
                    step["failure"] = "RECORDING_ERROR"
                else:
                    submit_step(step, observation, request, game, archive, research)
            if step["failure"] is not None or obj(step["receipt"])["status"] != "SUCCEEDED":
                data["prefix_status"] = "PREPARATION_FAILED"
                break
        if data["prefix_status"] == "READY":
            data["checkpoint"] = {
                "state": kernel.state_snapshot,
                "digest": kernel.state_digest,
                "tick": kernel.simulation_time,
            }
            try:
                observation = game.observe()
            except Exception:
                decision["failure"] = "OBSERVATION_ERROR"
            else:
                decision["observation"] = observation_json(observation)
                try:
                    inputs = evidence(observation, archive, condition)
                    decision["input"] = inputs
                    request_data = obj(inputs["provider_request"])
                    provider_request = ProviderRequest(**request_data)  # type: ignore[arg-type]
                except Exception:
                    decision["failure"] = "RECORDING_ERROR"
                else:
                    decision["provider_called"] = True
                    try:
                        response = provider.generate(provider_request)
                    except ProviderFailure:
                        decision["failure"] = "TRANSPORT_ERROR"
                    except Exception:
                        decision["failure"] = "PROVIDER_ERROR"
                    else:
                        decision["raw_response"] = json_value(response)
                        parsed, action = parse_response(response, observation)
                        decision.update(parsed)
                        if action is not None:
                            submit_step(decision, observation, action, game, archive, research)
        data.update(
            {
                "observations": [observation_json(o) for o in research.observations],
                "action_traces": json_value(research.action_traces),
                "action_requests": json_value(
                    tuple(t.request for t in research.action_traces if t.engine_submitted)
                ),
                "event_traces": [t.to_json() for t in archive.traces],
                "knowledge": json_value(research.knowledge_history("stranger")),
                "engine_report": json_value(engine_report(research)),
            }
        )
        data["evaluation"] = evaluate(data)
        seal(data)
        from journeymap.experiments.pickup_cue_audit import audit_record

        data["integrity"] = audit_record(data)
        seal(data)
        return Record.capture(data)
    finally:
        kernel.close()


def export_case(record: Record, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=False)
    try:
        with (directory / "case.json").open("x", encoding="utf-8", newline="\n") as output:
            output.write(record.serialized + "\n")
    except OSError:
        raise OSError(
            "RECORDING_ERROR: case export incomplete; original case retained in memory"
        ) from None


def run_matrix(directory: Path, *, fixture: Fixture = DEFAULT_FIXTURE) -> JsonObject:
    if type(fixture) is not Fixture:
        raise TypeError("inert Fixture required")
    directory.mkdir(parents=True, exist_ok=False)
    entries: list[JsonValue] = []
    for target in TARGETS:
        for d in (1, 2, 3, 4):
            for condition in CONDITIONS:
                case_id = f"{target}-d{d}-{condition}"
                trial = run_case(
                    case_id=case_id, target=target, d=d, condition=condition, fixture=fixture
                )
                export_case(trial, directory / case_id)
                entries.append(
                    {
                        "case_id": case_id,
                        "target": target,
                        "d": d,
                        "condition": condition,
                        "record_sha256": trial.data["record_sha256"],
                    }
                )
    matrix: JsonObject = {"version": "pickup-cue-matrix-1", "cases": entries}
    with (directory / "matrix.json").open("x", encoding="utf-8", newline="\n") as output:
        output.write(Record.capture(matrix).serialized + "\n")
    from journeymap.experiments.pickup_cue_audit import audit_matrix

    return audit_matrix(directory)
