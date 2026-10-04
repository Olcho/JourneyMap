"""Read-only, Provider-free replay, perception reconstruction and diagnostic audit."""

import json
from pathlib import Path
from typing import cast

from journeymap.adapters.event_memory import EventTraceArchive
from journeymap.adapters.llm import Record, observation_json, parse_candidate, strict_json
from journeymap.adapters.pickup_cue_prompt import INSTRUCTIONS_SHA256, MODEL, PROFILE
from journeymap.adapters.provider import RawModelResponse
from journeymap.bootstrap import create_pickup_cue_application, create_pickup_cue_kernel
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.core.handlers import ActionRequest
from journeymap.core.replay import ReplayHarness, ReplayInput, ReplayReport
from journeymap.experiments.alderwick import json_value
from journeymap.experiments.memory_horizon_audit import obj, require, rows
from journeymap.experiments.pickup_cue import (
    AUDIT_VERSION,
    CONDITIONS,
    EXPORT_VERSION,
    PROTOCOL_VERSION,
    engine_report,
    evaluate,
    evidence,
    memory_policy,
    parse_response,
    step_record,
    submit_step,
)
from journeymap.experiments.pilot_journal import digest
from journeymap.scenarios.alderwick.pickup_cue import (
    SCENARIO_VERSION,
    TARGETS,
    pickup_schedule,
    pickup_world,
    prefix_intents,
)


def same(actual: JsonValue, expected: JsonValue, reason: str) -> None:
    require(
        Record.capture({"v": actual}).serialized == Record.capture({"v": expected}).serialized,
        reason,
    )


def identity(data: JsonObject) -> JsonObject:
    fields = {
        "export_version",
        "protocol_version",
        "scenario_version",
        "audit_version",
        "prompt_version",
        "instructions_sha256",
        "instructions_bytes",
        "source",
        "runtime",
        "engine_manifest",
        "case",
        "fixture",
        "provider_identity",
        "initial_state",
        "initial_knowledge",
        "schedule",
        "prefix",
        "prefix_status",
        "checkpoint",
        "decision",
        "observations",
        "action_traces",
        "action_requests",
        "event_traces",
        "knowledge",
        "engine_report",
        "evaluation",
        "record_sha256",
    }
    require(set(data) in (fields, fields | {"integrity"}), "record inventory")
    fixture = obj(data["fixture"])
    require(
        set(fixture) == {"kind", "text"}
        and type(fixture["text"]) is str
        and fixture["kind"]
        in ("notice", "fixed", "refusal", "incomplete", "provider-error", "transport-error"),
        "inert fixture configuration",
    )
    require(
        data["record_sha256"] == digest({k: v for k, v in data.items() if k != "record_sha256"}),
        "record seal",
    )
    for key, expected in (
        ("export_version", EXPORT_VERSION),
        ("protocol_version", PROTOCOL_VERSION),
        ("scenario_version", SCENARIO_VERSION),
        ("audit_version", AUDIT_VERSION),
        ("prompt_version", PROFILE.version),
        ("instructions_sha256", INSTRUCTIONS_SHA256),
        ("instructions_bytes", len(PROFILE.instructions.encode("utf-8"))),
    ):
        same(data[key], expected, key)
    case = obj(data["case"])
    require(set(case) == {"case_id", "target", "d", "condition", "seed"}, "case fields")
    require(
        type(case["case_id"]) is str and bool(case["case_id"]) and type(case["seed"]) is int,
        "case identity",
    )
    prefix_intents(cast(int, case["d"]))
    memory_policy(cast(str, case["condition"]))
    same(data["initial_state"], pickup_world(cast(str, case["target"])), "initial scenario")
    same(data["initial_knowledge"], [], "initial Knowledge")
    same(data["knowledge"], [], "final Knowledge")
    same(data["schedule"], json_value(pickup_schedule()), "schedule")
    source, runtime = obj(data["source"]), obj(data["runtime"])
    require(
        bool(source.get("git_commit"))
        and bool(source.get("branch"))
        and bool(source.get("working_source_sha256"))
        and bool(runtime.get("version")),
        "provenance",
    )
    same(
        data["provider_identity"],
        {
            "name": "fake",
            "version": "pickup-cue-recording-fixture-1",
            "kind": "fixture",
            "implementation": "journeymap.adapters.pickup_cue_prompt.PickupFixtureProvider",
            "timeout_seconds": None,
            "response_model_policy": "exact-requested-model-1",
        },
        "offline implementation",
    )
    return case


def replay_record(data: JsonObject) -> ReplayReport:
    case = identity(data)
    kernel = create_pickup_cue_kernel(
        run_id=f"{case['case_id']}:run",
        target=cast(str, case["target"]),
        seed=cast(int, case["seed"]),
    )
    try:
        same(data["engine_manifest"], json_value(kernel.manifest), "engine identity")
    finally:
        kernel.close()
    actions = tuple(ActionRequest(**r) for r in rows(data["action_requests"]))  # type: ignore[arg-type]
    require(
        all(a.actor_id == "stranger" and a.action_type in ("MOVE", "WAIT") for a in actions),
        "action scope",
    )
    report = ReplayHarness(
        lambda: create_pickup_cue_kernel(
            run_id=f"{case['case_id']}:run",
            target=cast(str, case["target"]),
            seed=cast(int, case["seed"]),
        )
    ).run(
        ReplayInput(
            pickup_schedule(),
            actions,
            cast(int, obj(data["engine_report"])["final_simulation_time"]),
        )
    )
    same(data["engine_report"], json_value(report), "engine replay")
    return report


def reconstruct(data: JsonObject) -> None:
    """Generate actual Observations again; stored JSON is comparison data only."""
    case = obj(data["case"])
    kernel = create_pickup_cue_kernel(
        run_id=f"{case['case_id']}:run",
        target=cast(str, case["target"]),
        seed=cast(int, case["seed"]),
    )
    kernel.boot()
    try:
        app, research = create_pickup_cue_application(kernel)
        game, archive = app.game_for("stranger"), EventTraceArchive()
        expected_prefix: list[JsonValue] = []
        for intent in prefix_intents(cast(int, case["d"])):
            step = step_record("preparation")
            observation = game.observe()
            step["observation"] = observation_json(observation)
            archive.begin(observation)
            submit_step(
                step, observation, parse_candidate(intent, observation), game, archive, research
            )
            require(
                step["failure"] is None and obj(step["receipt"])["status"] == "SUCCEEDED",
                "prefix execution",
            )
            expected_prefix.append(step)
        same(data["prefix"], expected_prefix, "scripted prefix")
        same(data["prefix_status"], "READY", "prefix status")
        same(
            data["checkpoint"],
            {
                "state": kernel.state_snapshot,
                "digest": kernel.state_digest,
                "tick": kernel.simulation_time,
            },
            "checkpoint",
        )
        require(kernel.simulation_time == 7 and len(archive.traces) == 5, "prefix bounds")
        observation = game.observe()
        expected = step_record("evaluation")
        expected.update(
            {
                "observation": observation_json(observation),
                "input": evidence(observation, archive, cast(str, case["condition"])),
                "provider_called": True,
                "raw_response": None,
                "candidate": None,
                "parser_outcome": "NOT_RUN",
            }
        )
        decision = obj(data["decision"])
        require(
            decision["failure"] not in ("RECORDING_ERROR", "ENGINE_ERROR", "OBSERVATION_ERROR"),
            "incomplete evidence cannot certify research integrity",
        )
        raw = decision["raw_response"]
        if raw is None:
            require(
                decision["failure"] in ("PROVIDER_ERROR", "TRANSPORT_ERROR"), "missing response"
            )
            require(
                obj(data["fixture"])["kind"]
                == (
                    "provider-error"
                    if decision["failure"] == "PROVIDER_ERROR"
                    else "transport-error"
                ),
                "unverifiable provider exception",
            )
            expected["failure"] = decision["failure"]
        else:
            response = RawModelResponse(**obj(raw))  # type: ignore[arg-type]
            require(
                response.metadata.get("provider") == "fake"
                and response.metadata.get("adapter_version") == "pickup-cue-recording-fixture-1"
                and response.metadata.get("model") == MODEL,
                "response identity",
            )
            expected["raw_response"] = raw
            parsed, action = parse_response(response, observation)
            expected.update(parsed)
            if action is not None:
                submit_step(expected, observation, action, game, archive, research)
        same(decision, expected, "evaluation decision/input/prompt/receipt")
        same(
            data["observations"],
            [observation_json(o) for o in research.observations],
            "observations",
        )
        same(data["action_traces"], json_value(research.action_traces), "action traces")
        same(
            data["action_requests"],
            json_value(tuple(t.request for t in research.action_traces if t.engine_submitted)),
            "submitted stream",
        )
        same(data["event_traces"], [t.to_json() for t in archive.traces], "closed traces")
        same(data["engine_report"], json_value(engine_report(research)), "reconstructed execution")
        same(data["evaluation"], evaluate(data), "recomputed evaluation")
    finally:
        kernel.close()


def audit_record(data: JsonObject) -> JsonObject:
    replay_ok = inputs_ok = False
    try:
        replay_record(data)
        replay_ok = True
        reconstruct(data)
        inputs_ok = True
    except (KeyError, TypeError, ValueError, RuntimeError, OverflowError):
        pass
    result: JsonObject = {
        "version": AUDIT_VERSION,
        "status": "INCLUDED" if inputs_ok else "EXCLUDED",
        "engine_replay": replay_ok,
        "input_reconstruction": inputs_ok,
        "evaluation_recomputed": inputs_ok,
        "task_success": obj(data["evaluation"])["task_success"] if inputs_ok else None,
    }
    if (
        "integrity" in data
        and Record.capture({"v": data["integrity"]}).serialized
        != Record.capture({"v": result}).serialized
    ):
        result.update(
            {"status": "EXCLUDED", "task_success": None, "stored_integrity_matches": False}
        )
    return result


def read_export(directory: Path) -> JsonObject:
    # Raw invalid model text may contain escaped unpaired surrogates. Preserve it;
    # strict_json remains the decision parser, not the research-envelope reader.
    def pairs(items: list[tuple[str, JsonValue]]) -> JsonObject:
        value: JsonObject = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate export key")
            value[key] = item
        return value

    data = obj(
        json.loads((directory / "case.json").read_text(encoding="utf-8"), object_pairs_hook=pairs)
    )
    Record.capture(data)  # Reject non-finite JSON even inside raw research envelopes.
    require(data.get("export_version") == EXPORT_VERSION, "unsupported export format")
    return data


def audit_export(directory: Path) -> JsonObject:
    try:
        return audit_record(read_export(directory))
    except (OSError, ValueError, KeyError, TypeError):
        return {
            "version": AUDIT_VERSION,
            "status": "EXCLUDED",
            "task_success": None,
            "reason": "EXPORT_INTEGRITY",
        }


def audit_matrix(directory: Path) -> JsonObject:
    try:
        matrix = obj(strict_json((directory / "matrix.json").read_text(encoding="utf-8")))
        require(matrix["version"] == "pickup-cue-matrix-1", "matrix version")
        entries = rows(matrix["cases"])
        expected = {(t, d, c) for t in TARGETS for d in (1, 2, 3, 4) for c in CONDITIONS}
        actual = [(e["target"], e["d"], e["condition"]) for e in entries]
        require(
            len(entries) == 40 and set(actual) == expected, "matrix missing/duplicate combinations"
        )
        ids: set[str] = set()
        reports: list[JsonValue] = []
        responded = infrastructure = success = 0
        for entry in entries:
            case_id = cast(str, entry["case_id"])
            require(
                case_id == f"{entry['target']}-d{entry['d']}-{entry['condition']}"
                and case_id not in ids,
                "case path/identity",
            )
            ids.add(case_id)
            data = read_export(directory / case_id)
            case = obj(data["case"])
            require(
                all(case[k] == entry[k] for k in ("case_id", "target", "d", "condition"))
                and data["record_sha256"] == entry["record_sha256"],
                "result linkage",
            )
            audit = audit_record(data)
            reports.append({"case_id": case_id, **audit})
            evaluation = obj(data["evaluation"])
            if audit["status"] == "INCLUDED":
                responded += int(evaluation["behavioral_denominator"] is True)
                infrastructure += int(evaluation["infrastructure_before_response"] is True)
                success += int(evaluation["task_success"] is True)
        excluded = sum(obj(r)["status"] != "INCLUDED" for r in reports)
        return {
            "version": "pickup-cue-matrix-audit-1",
            "status": "EXCLUDED" if excluded else "INCLUDED",
            "case_count": 40,
            "responded_denominator": responded,
            "infrastructure_before_response": infrastructure,
            "integrity_exclusions": excluded,
            "task_success_count": success,
            "cases": reports,
        }
    except (OSError, KeyError, TypeError, ValueError):
        return {
            "version": "pickup-cue-matrix-audit-1",
            "status": "EXCLUDED",
            "reason": "MATRIX_INTEGRITY",
        }
