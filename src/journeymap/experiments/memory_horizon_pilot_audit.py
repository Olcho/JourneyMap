"""Provider-free reconstruction of new mock pilot evidence and durable journal."""

import json
import math
from dataclasses import asdict
from pathlib import Path
from typing import cast

from journeymap.adapters.event_memory import EventTraceArchive, RecencyEventMemory
from journeymap.adapters.llm import (
    Record,
    event_memory_input,
    observation_json,
    parse_candidate,
    strict_json,
)
from journeymap.adapters.memory import NoMemory
from journeymap.adapters.memory_horizon_live import (
    CONFIGURATION_VERSION,
    MOCK_VERSION,
    WIRE_SCHEMA_VERSION,
    call_evidence,
    validate_candidate,
    wire_schema,
)
from journeymap.adapters.memory_horizon_prompt import PROFILE, semantic_input
from journeymap.adapters.provider import ProviderRequest
from journeymap.bootstrap import create_memory_horizon_application, create_memory_horizon_kernel
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.core.handlers import ValidationContext
from journeymap.core.replay import ReplayHarness, ReplayInput, ReplayReport
from journeymap.experiments.alderwick import BudgetMonitor, json_value
from journeymap.experiments.memory_horizon import END_TICK, protocol_precheck, summarize
from journeymap.experiments.memory_horizon_audit import obj, require, rows
from journeymap.experiments.memory_horizon_study import (
    AUDIT_VERSION,
    TRIAL_SCHEMA_VERSION,
    admission,
    derived_metrics,
    validate_cohort,
)
from journeymap.experiments.pilot_journal import digest, inspect_journal, read_journal
from journeymap.scenarios.alderwick.memory_horizon import (
    memory_horizon_schedule,
    memory_horizon_world,
)


def same(actual: JsonValue, expected: JsonValue, reason: str) -> None:
    require(
        Record.capture({"v": actual}).serialized == Record.capture({"v": expected}).serialized,
        reason,
    )


def reconstruct(data: JsonObject) -> list[JsonObject]:
    require(
        set(data)
        == {
            "manifest",
            "metrics",
            "derived_metrics",
            "decisions",
            "initial_state",
            "initial_knowledge",
            "observations",
            "observation_attempts",
            "action_traces",
            "event_traces",
            "replay_input",
            "engine_report",
            "record_sha256",
        },
        "trial inventory",
    )
    require(
        data["record_sha256"] == digest({k: v for k, v in data.items() if k != "record_sha256"}),
        "trial seal",
    )
    manifest = obj(data["manifest"])
    require(manifest["schema_version"] == TRIAL_SCHEMA_VERSION, "trial schema")
    cohort = obj(manifest["cohort"])
    config = validate_cohort(cohort, execution=False)
    allocation = obj(manifest["allocation"])
    require(
        allocation in rows(cohort["allocations"]) and allocation["execution_status"] == "PLANNED",
        "allocation binding",
    )
    same(
        manifest["provider_identity"],
        {"kind": "fixture", "name": "fake", "version": MOCK_VERSION},
        "mock identity",
    )
    run_id = f"{allocation['intended_trial_id']}:run"
    require(manifest["run_id"] == run_id, "run binding")
    seed = cast(int, cohort["engine_seed"])
    kernel = create_memory_horizon_kernel(run_id=run_id, seed=seed)
    kernel.boot()
    expected_journal: list[JsonObject] = []

    def entry(kind: str, content: JsonObject) -> None:
        expected_journal.append({"kind": kind, "data": content})

    try:
        same(manifest["engine_manifest"], json_value(kernel.manifest), "engine manifest")
        same(data["initial_state"], memory_horizon_world(), "initial world")
        same(data["initial_knowledge"], [], "initial knowledge")
        entry(
            "trial_started",
            {
                "manifest": {
                    **manifest,
                    "status": "RUNNING",
                    "stop_reason": None,
                    "simulation_end": 0,
                    "wall_seconds": 0.0,
                    "replay_equal": False,
                },
                "initial_state": data["initial_state"],
                "schedule": json_value(memory_horizon_schedule()),
            },
        )
        monitor = BudgetMonitor()
        app, research = create_memory_horizon_application(kernel, pipeline=monitor)
        game = app.game_for("stranger")
        archive = EventTraceArchive()
        condition = cast(str, allocation["condition"])
        memory = (
            NoMemory() if condition == "no-event-memory" else RecencyEventMemory(int(condition[-1]))
        )
        failures = calls = 0
        stop: str | None = None
        decisions = rows(data["decisions"])
        for index, d in enumerate(decisions, 1):
            require(
                stop is None
                and kernel.simulation_time < END_TICK
                and index <= config.max_decisions
                and calls < config.max_provider_calls,
                "decision after bound",
            )
            same(d["decision_sequence"], index, "decision sequence")
            same(d["simulation_time"], kernel.simulation_time, "decision tick")
            before, offset = kernel.simulation_time, len(research.action_traces)
            observation = game.observe()
            same(d["observation"], observation_json(observation), "observation")
            context = archive.begin(observation)
            inputs, provenance = event_memory_input(context, memory)
            inputs = semantic_input(inputs)
            expected: JsonObject = {
                **provenance,
                "model_visible_input": inputs,
                "model_visible_input_canonical": canonical_json(inputs),
                "opportunity_sequence": context.opportunity_sequence,
                "opportunity_attempt": context.attempt,
                "wire_schema_version": WIRE_SCHEMA_VERSION,
                "wire_schema": wire_schema(),
                "provider_called": True,
            }
            for key, value in expected.items():
                same(d[key], value, key)
            request = ProviderRequest(
                PROFILE.render(inputs),
                PROFILE.version,
                config.model,
                CONFIGURATION_VERSION,
                config.parameters,
            )
            same(d["provider_request"], cast(JsonObject, asdict(request)), "request")
            evidence = call_evidence(request)
            call = obj(d["call"])
            require(
                set(call)
                == set(evidence)
                | {"raw_output", "metadata", "transport_failure", "transport_body"},
                "call inventory",
            )
            for key, value in evidence.items():
                same(call[key], value, key)
            same(call["transport_body"], evidence["body"], "actual transport body")
            entry("provider_intent", evidence)
            result = {
                key: call[key]
                for key in ("raw_output", "metadata", "transport_failure", "transport_body")
            }
            entry("provider_result", result)
            same(d["raw_output"], call["raw_output"], "raw output")
            same(d["provider_metadata"], call["metadata"], "response metadata")
            calls += 1
            failure = None
            action = None
            if call["transport_failure"] is not None:
                same(
                    result,
                    {
                        "raw_output": None,
                        "metadata": {},
                        "transport_failure": "TRANSPORT_ERROR",
                        "transport_body": evidence["body"],
                    },
                    "infrastructure evidence",
                )
                failure = "INFRASTRUCTURE_FAILURE"
            else:
                metadata = obj(call["metadata"])
                same(metadata.get("provider"), "fake", "provider")
                same(metadata.get("adapter_version"), MOCK_VERSION, "adapter")
                same(metadata.get("model"), config.model, "returned model")
                same(metadata.get("response_id"), f"mock-response-{calls}", "response identity")
                if metadata.get("refusal"):
                    failure = "REFUSAL"
                elif metadata.get("status") != "completed":
                    failure = "INCOMPLETE_OUTPUT"
                else:
                    failure = "PARSER_FAILURE"
                    try:
                        candidate = strict_json(cast(str, d["raw_output"]))
                        failure = "SCHEMA_INVALID"
                        validate_candidate(candidate)
                        action = parse_candidate(candidate, observation)
                        failure = None
                    except ValueError:
                        pass
            same(d["failure"], failure, "failure classification")
            same(d["action_request"], json_value(action), "action binding")
            receipt = None
            closed_id = None
            refusal = None
            turn_failure = "CONTROLLER_ERROR" if failure else None
            if action is not None:
                refusal = protocol_precheck(
                    action, ValidationContext(run_id, before, kernel.state_snapshot)
                )
                if d["protocol_failure"] == "WALL_TIMEOUT":
                    refusal = "WALL_TIMEOUT"
                if refusal:
                    stop, turn_failure = refusal, "SUBMISSION_ERROR"
                else:
                    entry("engine_intent", cast(JsonObject, json_value(action)))
                    receipt = game.submit(action)
                    entry("engine_receipt", cast(JsonObject, json_value(receipt)))
                    closed_id = archive.close(
                        observation, action, receipt, engine_submitted=True
                    ).event_trace_id
            same(d["protocol_failure"], refusal, "protocol refusal")
            same(d["turn_failure"], turn_failure, "turn failure")
            same(d["receipt"], json_value(receipt), "receipt")
            same(d["closed_event_trace_id"], closed_id, "archive closure")
            same(
                d["action_trace_sequences"],
                [t.attempt_sequence for t in research.action_traces[offset:]],
                "trace linkage",
            )
            entry(
                "decision_finished",
                {
                    "decision": d,
                    "action_traces": json_value(research.action_traces[offset:]),
                    "state_digest": kernel.state_digest,
                    "simulation_time": kernel.simulation_time,
                },
            )
            failures = failures + 1 if kernel.simulation_time == before else 0
            if failure == "INFRASTRUCTURE_FAILURE":
                stop = "INFRASTRUCTURE_FAILURE"
            elif stop is None and failures >= config.max_consecutive_failures:
                stop = "CONSECUTIVE_FAILURES"
        stop = stop or (
            "HORIZON"
            if kernel.simulation_time == END_TICK
            else "MAX_DECISIONS"
            if len(decisions) >= config.max_decisions
            else "MAX_PROVIDER_CALLS"
            if calls >= config.max_provider_calls
            else "WALL_TIMEOUT"
        )
        elapsed = manifest["wall_seconds"]
        require(
            type(elapsed) in (int, float)
            and math.isfinite(cast(float, elapsed))
            and cast(float, elapsed) >= 0,
            "wall evidence",
        )
        if manifest["stop_reason"] == "WALL_TIMEOUT":
            require(cast(float, elapsed) >= config.wall_timeout_seconds, "timeout evidence")
            if stop in ("WALL_TIMEOUT", "MAX_DECISIONS", "MAX_PROVIDER_CALLS"):
                stop = "WALL_TIMEOUT"
        elif stop == "WALL_TIMEOUT":
            raise ValueError("missing wall termination")
        same(manifest["stop_reason"], stop, "stop reason")
        same(
            manifest["status"],
            "COMPLETED" if stop == "HORIZON" else "TERMINATED",
            "execution status",
        )
        same(manifest["simulation_end"], kernel.simulation_time, "end tick")
        same(
            data["observations"],
            [observation_json(o) for o in research.observations],
            "observation inventory",
        )
        same(
            data["observation_attempts"],
            cast(list[JsonValue], monitor.attempts),
            "budget inventory",
        )
        same(data["action_traces"], json_value(research.action_traces), "action traces")
        same(data["event_traces"], [t.to_json() for t in archive.traces], "event traces")
        replay_input = ReplayInput(
            memory_horizon_schedule(),
            tuple(t.request for t in research.action_traces if t.engine_submitted),
            kernel.simulation_time,
        )
        same(data["replay_input"], json_value(replay_input), "replay inputs")
        report = ReplayReport(
            research.action_results,
            research.system_event_outcomes,
            research.events,
            research.world_snapshot,
            research.state_digest,
            research.simulation_time,
            research.rng_snapshot.draw_count,
        )
        same(data["engine_report"], json_value(report), "engine report")
        replay = ReplayHarness(lambda: create_memory_horizon_kernel(run_id=run_id, seed=seed)).run(
            replay_input
        )
        require(replay == report and manifest["replay_equal"] is True, "engine replay")
        metrics = summarize(
            decisions, rows(data["action_traces"]), rows(obj(data["engine_report"])["events"])
        )
        same(data["metrics"], metrics, "existing metrics")
        same(data["derived_metrics"], derived_metrics(metrics), "derived metrics")
        entry("trial_finished", {"record_sha256": data["record_sha256"]})
        return expected_journal
    finally:
        kernel.close()


def audit_record(data: JsonObject) -> JsonObject:
    try:
        reconstruct(data)
        return {
            "audit_version": AUDIT_VERSION,
            **admission(rows(data["decisions"]), True),
            "reasons": [],
        }
    except (KeyError, TypeError, ValueError, RuntimeError, OverflowError):
        reported = data.get("decisions")
        claims = [d for d in reported if isinstance(d, dict)] if isinstance(reported, list) else []
        return {
            "audit_version": AUDIT_VERSION,
            **admission([], False),
            "infrastructure_failure": None,
            "reported_infrastructure_failure": admission(claims, False)["infrastructure_failure"],
            "reasons": ["PILOT_INTEGRITY"],
        }


def inspect_trial(directory: Path) -> JsonObject:
    """Diagnose a durable prefix without writing, resuming, or inventing outcomes.

    Journal kinds are the progress vocabulary. A missing result after an intent
    remains indeterminate, never a fabricated transport error or model failure.
    """
    result: JsonObject = {
        "journal_status": "MISSING",
        "verified_records": 0,
        "last_journal_kind": None,
        "trial_started": False,
        "trial_finished": False,
        "snapshot_status": "MISSING",
        "snapshot_linked": False,
        "interrupted": True,
        "infrastructure_failure": None,
        "integrity_status": "EXCLUDED",
        "behavioral_denominator": False,
    }
    snapshot_data: JsonObject | None = None
    snapshot = directory / "trial.json"
    if snapshot.exists():
        result["snapshot_status"] = "INVALID"
        try:
            candidate = obj(json.loads(snapshot.read_text(encoding="utf-8")))
            require(
                candidate["record_sha256"]
                == digest({k: v for k, v in candidate.items() if k != "record_sha256"}),
                "snapshot seal",
            )
            snapshot_data = candidate
            result["snapshot_status"] = "PRESENT"
        except (OSError, KeyError, TypeError, ValueError, RuntimeError, OverflowError):
            pass
    try:
        scan = inspect_journal(directory / "attempts.jsonl")
        result.update({"journal_status": scan.status, "verified_records": len(scan.rows)})
        allowed = {
            None: ("trial_started",),
            "trial_started": ("provider_intent", "observation_error", "trial_finished"),
            "provider_intent": ("provider_result",),
            "provider_result": ("engine_intent", "decision_finished"),
            "engine_intent": ("engine_receipt", "engine_error"),
            "engine_receipt": ("decision_finished",),
            "engine_error": ("decision_finished",),
            "observation_error": ("decision_finished",),
            "decision_finished": ("provider_intent", "observation_error", "trial_finished"),
            "trial_finished": (),
        }
        previous: str | None = None
        infrastructure = False
        for row in scan.rows:
            kind, content = cast(str, row["kind"]), obj(row["data"])
            require(kind in allowed[previous], "journal phase order")
            if kind == "trial_started":
                manifest = obj(content["manifest"])
                cohort = obj(manifest["cohort"])
                validate_cohort(cohort, execution=False)
                require(manifest["allocation"] in rows(cohort["allocations"]), "prefix allocation")
                same(
                    manifest["run_id"],
                    f"{obj(manifest['allocation'])['intended_trial_id']}:run",
                    "prefix run",
                )
                result["trial_started"] = True
            if kind in ("observation_error", "engine_error"):
                same(
                    content,
                    {
                        "reason": "OBSERVATION_UNAVAILABLE"
                        if kind == "observation_error"
                        else "ENGINE_ERROR"
                    },
                    "system error evidence",
                )
                infrastructure = True
                result["infrastructure_failure"] = True
            if kind == "provider_result" and content.get("transport_failure") == "TRANSPORT_ERROR":
                require(
                    content.get("raw_output") is None and content.get("metadata") == {},
                    "pre-response infrastructure evidence",
                )
                infrastructure = True
                result["infrastructure_failure"] = True
            previous = kind
        result.update(
            {
                "last_journal_kind": previous,
                "trial_finished": previous == "trial_finished",
                "infrastructure_failure": True if infrastructure else None,
            }
        )
        if snapshot_data is not None and previous == "trial_finished":
            same(
                obj(scan.rows[-1]["data"])["record_sha256"],
                snapshot_data["record_sha256"],
                "finished snapshot linkage",
            )
            result["snapshot_linked"] = True
            result["interrupted"] = scan.status != "COMPLETE"
        return result
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, OverflowError):
        # Keep any already established prefix/provenance, but never certify it.
        return result


def audit_export(directory: Path) -> JsonObject:
    try:
        data = cast(JsonObject, json.loads((directory / "trial.json").read_text(encoding="utf-8")))
        expected = reconstruct(data)
        journal = [
            {"kind": r["kind"], "data": r["data"]}
            for r in read_journal(directory / "attempts.jsonl")
        ]
        same(
            cast(list[JsonValue], journal),
            cast(list[JsonValue], expected),
            "journal/research mismatch",
        )
        return {
            "audit_version": AUDIT_VERSION,
            **admission(rows(data["decisions"]), True),
            "reasons": [],
        }
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, OverflowError):
        partial = inspect_trial(directory)
        return {
            "audit_version": AUDIT_VERSION,
            **admission([], False),
            "infrastructure_failure": partial["infrastructure_failure"],
            "partial_evidence": partial,
            "reasons": ["PILOT_EXPORT_INTEGRITY"],
        }


def inspect_cohort(directory: Path) -> JsonObject:
    """Read a partial batch's known allocations and last durable trial phases."""
    result: JsonObject = {
        "integrity_status": "EXCLUDED",
        "journal_status": "MISSING",
        "allocations": [],
    }
    try:
        plan = obj(json.loads((directory / "cohort-plan.json").read_text(encoding="utf-8")))
        validate_cohort(plan, execution=False)
        scan = inspect_journal(directory / "cohort-attempts.jsonl")
        result["journal_status"] = scan.status
        result["verified_records"] = len(scan.rows)
        if scan.rows:
            same(scan.rows[0]["kind"], "cohort_started", "cohort start")
            same(scan.rows[0]["data"], {"plan": plan}, "durable cohort plan")
        pending = [
            cast(str, a["allocation_id"])
            for a in rows(plan["allocations"])
            if a["execution_status"] == "PLANNED"
        ]
        started: set[str] = set()
        finished: set[str] = set()
        active: str | None = None
        for entry in scan.rows[1:]:
            content = obj(entry["data"])
            identity = cast(str, content["allocation_id"])
            if entry["kind"] == "allocation_started":
                require(
                    active is None and bool(pending) and identity == pending.pop(0),
                    "allocation start order",
                )
                same(content, {"allocation_id": identity}, "allocation start fields")
                active = identity
                started.add(identity)
            else:
                require(
                    entry["kind"] == "allocation_finished" and active == identity,
                    "allocation finish order",
                )
                finished.add(identity)
                active = None
        result["allocations"] = [
            {
                "allocation_id": a["allocation_id"],
                "execution_status": a["execution_status"],
                "allocation_started": a["allocation_id"] in started,
                "allocation_finished": a["allocation_id"] in finished,
                "trial": inspect_trial(directory / cast(str, a["allocation_id"])),
            }
            for a in rows(plan["allocations"])
        ]
        # Prefix validity is distinct from a completed cohort's integrity verdict.
        result["prefix_valid"] = scan.status in ("COMPLETE", "TORN_TAIL")
        return result
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, OverflowError):
        result["prefix_valid"] = False
        return result


def audit_cohort(
    cohort: JsonObject,
    records: dict[str, JsonObject],
    *,
    batches: tuple[Path, ...] = (),
) -> JsonObject:
    """Recompute from trial exports AND durable batch inventories, including originals.

    A bare mutable manifest is not evidence that an allocation was never started.
    Every batch, including prior replacement batches, must be supplied.
    """
    try:
        validate_cohort(cohort, execution=False)
        require(bool(batches), "durable cohort journals required")
        allocations = rows(cohort["allocations"])
        require(
            set(records)
            == {a["allocation_id"] for a in allocations if a["execution_status"] == "FINISHED"},
            "all-attempt inventory",
        )
        locations: dict[str, Path] = {}
        execution_plans: dict[str, JsonObject] = {}
        final_rows = {cast(str, a["allocation_id"]): a for a in allocations}
        for batch in batches:
            plan = obj(json.loads((batch / "cohort-plan.json").read_text(encoding="utf-8")))
            validate_cohort(plan, execution=False)
            for key in cohort.keys() - {"allocations"}:
                same(plan[key], cohort[key], "batch frozen plan")
            expected_journal: list[JsonObject] = [
                {"kind": "cohort_started", "data": {"plan": plan}}
            ]
            finished_plan = Record.capture(plan).data
            planned_ids: set[str] = set()
            for row in rows(finished_plan["allocations"]):
                identity = cast(str, row["allocation_id"])
                if row["execution_status"] == "FINISHED":
                    same(row, final_rows[identity], "prior result preservation")
                    continue
                require(identity not in locations, "duplicate execution across batches")
                planned_ids.add(identity)
                locations[identity] = batch / identity
                execution_plans[identity] = Record.capture(finished_plan).data
                require(final_rows[identity]["execution_status"] == "FINISHED", "state rollback")
                expected_journal.extend(
                    [
                        {"kind": "allocation_started", "data": {"allocation_id": identity}},
                        {"kind": "allocation_finished", "data": final_rows[identity]},
                    ]
                )
                row.update(final_rows[identity])
            same(
                obj(json.loads((batch / "cohort-final.json").read_text(encoding="utf-8"))),
                finished_plan,
                "batch final snapshot",
            )
            actual = [
                {"kind": r["kind"], "data": r["data"]}
                for r in read_journal(batch / "cohort-attempts.jsonl")
            ]
            same(
                cast(list[JsonValue], actual),
                cast(list[JsonValue], expected_journal),
                "cohort journal inventory",
            )
            require(
                {p.name for p in batch.iterdir() if p.is_dir()} == planned_ids,
                "batch trial directory inventory",
            )
        require(set(locations) == set(records), "all started attempts retained")
        denominator = 0
        for allocation in allocations:
            if allocation["execution_status"] != "FINISHED":
                continue
            data = records[cast(str, allocation["allocation_id"])]
            location = locations[cast(str, allocation["allocation_id"])]
            same(
                data,
                obj(json.loads((location / "trial.json").read_text(encoding="utf-8"))),
                "supplied/exported record equality",
            )
            same(allocation["trial_record_sha256"], data["record_sha256"], "trial seal linkage")
            recorded = obj(obj(data["manifest"])["allocation"])
            for key in (
                "allocation_id",
                "intended_trial_id",
                "condition",
                "block",
                "order",
                "replacement_of",
                "replacement_reason",
            ):
                same(allocation[key], recorded[key], "allocation linkage")
            recorded_cohort = obj(obj(data["manifest"])["cohort"])
            same(
                recorded_cohort,
                execution_plans[cast(str, allocation["allocation_id"])],
                "trial started from the durable batch plan",
            )
            for key in cohort.keys() - {"allocations"}:
                same(cohort[key], recorded_cohort[key], "common frozen configuration")
            assessed = audit_export(location)
            require(assessed["integrity_status"] == "VERIFIED", "trial evidence integrity")
            for key in ("integrity_status", "behavioral_denominator", "infrastructure_failure"):
                same(allocation[key], assessed[key], key)
            same(
                allocation["primary_success"],
                obj(data["derived_metrics"])["primary_success"],
                "primary linkage",
            )
            denominator += int(assessed["behavioral_denominator"] is True)
        return {
            "audit_version": AUDIT_VERSION,
            "integrity_status": "VERIFIED",
            "all_attempt_count": len(records),
            "behavioral_denominator_count": denominator,
            "planned_allocation_count": sum(
                a["execution_status"] == "PLANNED" for a in allocations
            ),
        }
    except (OSError, KeyError, TypeError, ValueError, RuntimeError, OverflowError):
        return {
            "audit_version": AUDIT_VERSION,
            "integrity_status": "EXCLUDED",
            "reasons": ["COHORT_INTEGRITY"],
        }
