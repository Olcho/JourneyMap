"""Offline-only E04 runner: actual initial provider action and three fresh branches."""

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from journeymap.adapters.llm import Record
from journeymap.adapters.prompt_profile import PromptProfile
from journeymap.adapters.provider import ProviderFailure, RawModelResponse
from journeymap.adapters.route_update import (
    LEGACY_PROFILE,
    PROFILE,
    SCHEMA,
    FakeProvider,
    Script,
    obj,
)
from journeymap.adapters.route_update_policy import first_policy
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.experiments.alderwick import json_value
from journeymap.experiments.pickup_cue_live_plan import source_identity
from journeymap.experiments.pilot_journal import AttemptJournal, digest, write_snapshot
from journeymap.experiments.provenance import runtime_identity
from journeymap.experiments.route_update_session import Session
from journeymap.scenarios.route_update import CONDITIONS, VERSION, authored_map

LEGACY_PROTOCOL = "experiment04-offline-1"
PROTOCOL = "experiment04-offline-2"
EXPORT_VERSION = "experiment04-offline-export-2"


def protocol_profile(protocol: JsonValue) -> tuple[str, PromptProfile]:
    """Only two exact contracts; legacy is retained for recorded-data audit."""
    if protocol == PROTOCOL:
        return EXPORT_VERSION, PROFILE
    if protocol == LEGACY_PROTOCOL:
        return "experiment04-offline-export-1", LEGACY_PROFILE
    raise ValueError("unsupported E04 protocol")


def move(route: str) -> str:
    return canonical_json({"action_type": "MOVE", "payload": {"route_id": route}})


@dataclass(frozen=True)
class Fixture:
    initial: Script
    branches: tuple[Script, Script, Script]

    def __post_init__(self) -> None:
        if (
            type(self.initial) is not Script
            or len(self.initial.outputs) != 1
            or type(self.branches) is not tuple
            or len(self.branches) != 3
            or any(type(s) is not Script or len(s.outputs) > 6 for s in self.branches)
        ):
            raise ValueError("one initial and three independent inert branch scripts required")


def demo_fixtures() -> tuple[Fixture, ...]:
    return (
        Fixture(
            Script((move("S-to-A"),)),
            (
                Script((move("A-to-C"), move("C-to-G"))),
                Script((move("A-to-C"), move("C-to-G"), move("C-to-D"), move("D-to-G"))),
                Script((move("A-to-D"), move("D-to-G"))),
            ),
        ),
        Fixture(Script((move("S-to-B"),)), (Script((move("B-to-D"), move("D-to-G"))),) * 3),
    )


def make_plan(
    count: int, seed: int = 42, max_decisions: int = 6, max_ticks: int = 24
) -> JsonObject:
    if (
        type(count) is not int
        or count <= 0
        or type(seed) is not int
        or type(max_decisions) is not int
        or max_decisions != 6
        or type(max_ticks) is not int
        or max_ticks != 24
    ):
        raise ValueError("invalid E04 offline plan")
    plan: JsonObject = {
        "protocol": PROTOCOL,
        "export_version": EXPORT_VERSION,
        "scenario_version": VERSION,
        "mode": "fake-only",
        "state": "PROTOCOL_DRAFT",
        "prefix_count": count,
        "conditions": list(CONDITIONS),
        "seed": seed,
        "max_decisions": max_decisions,
        "max_elapsed_ticks": max_ticks,
        "initial_map": authored_map(),
        "profile_version": PROFILE.version,
        "profile_instructions": PROFILE.instructions,
        "schema": SCHEMA,
        "source": source_identity(),
        "runtime": runtime_identity(),
        "experiment_model": None,
        "experiment_parameters": None,
        "api_budget": None,
        "mirror_mapping": None,
        "main_experiment_order": None,
        "api_usage": None,
        "provider_usage_applicability": "NOT_APPLICABLE_FAKE",
        "retry": False,
        "fallback": False,
        "sample_until_A": False,
    }
    plan["plan_sha256"] = digest(plan)
    return Record.capture(plan).data


def seal_export(plan: JsonObject, identity: JsonObject, result: JsonObject) -> JsonObject:
    export_version, _ = protocol_profile(plan["protocol"])
    if plan["export_version"] != export_version:
        raise ValueError("E04 protocol/export version mismatch")
    record: JsonObject = {
        "export_version": export_version,
        "protocol": plan["protocol"],
        "plan_sha256": plan["plan_sha256"],
        "identity": identity,
        "result": result,
    }
    record["record_sha256"] = digest(record)
    return Record.capture(record).data


def execute(
    fixtures: tuple[Fixture, ...],
    directory: Path,
    *,
    seed: int = 42,
    max_decisions: int = 6,
    max_ticks: int = 24,
) -> JsonObject:
    if type(fixtures) is not tuple or any(type(f) is not Fixture for f in fixtures) or not fixtures:
        raise TypeError("offline concrete fixture tuple required")
    if directory.exists() or not directory.parent.is_dir():
        raise ValueError("new output directory required")
    plan = make_plan(len(fixtures), seed, max_decisions, max_ticks)
    directory.mkdir()
    write_snapshot(directory / "plan.json", plan)
    journal = AttemptJournal(directory / "attempts.jsonl")
    session: Session | None = None
    calls = 0
    phase, prefix_id, branch_id = "plan", None, None
    stop = "COMPLETE"
    first_prepared: JsonObject | None = None

    def invoke(provider: FakeProvider, *, initial: bool = False) -> JsonObject:
        nonlocal calls, phase, first_prepared
        if session is None:
            raise ValueError("missing trusted session")
        prepared = session.prepare(initial=initial)
        if not initial and not session.steps:
            from journeymap.experiments.route_update_audit import check_first_inputs

            if first_prepared is None:
                first_prepared = prepared
            else:
                check_first_inputs(
                    first_prepared, prepared, notified=session.condition == "closed-notified"
                )
        phase = "intent"
        journal.append(
            "INTENT",
            {
                "prefix_id": prefix_id,
                "branch_id": branch_id,
                "number": 0 if initial else len(session.steps) + 1,
                "prepared": prepared,
            },
        )
        response: RawModelResponse | None = None
        failure: JsonObject | None = None
        calls += 1
        try:
            response = provider.generate(session.provider_request)
        except ProviderFailure as error:
            failure = {"kind": error.kind}
        phase = "response"
        journal.append(
            "RESPONSE",
            {"raw_response": json_value(response) if response else None, "failure": failure},
        )
        phase = "engine-result"
        result = session.finish(response, failure, initial=initial)
        journal.append("RESULT", result)
        return result

    try:
        journal.append("PLAN", {"plan_sha256": plan["plan_sha256"], "mode": "fake-only"})
        for number, fixture in enumerate(fixtures, 1):
            first_prepared = None
            prefix_id, branch_id = f"p{number:03d}", None
            phase = "initial-start"
            journal.append("INITIAL_START", {"prefix_id": prefix_id})
            session = Session(prefix_id + ":initial", seed, max_decisions, max_ticks)
            initial_provider = FakeProvider(fixture.initial)
            result = invoke(initial_provider, initial=True)
            record = seal_export(plan, {"prefix_id": prefix_id, "branch_id": None}, result)
            filename = prefix_id + ".json"
            write_snapshot(directory / filename, record)
            journal.append(
                "INITIAL_END", {"file": filename, "record_sha256": record["record_sha256"]}
            )
            session.close()
            session = None
            if result["stop"] == "INTEGRITY":
                stop = "INTEGRITY"
                break
            if result["stop"] != "PREFIX_READY":
                continue
            parent: JsonObject = {
                "record_sha256": record["record_sha256"],
                "parent_run_id": result["run_id"],
                "original_response_sha256": digest(obj(obj(result["prefix"])["raw_response"])),
            }
            candidate = obj(obj(result["prefix"])["candidate"])
            for condition, script in zip(CONDITIONS, fixture.branches, strict=True):
                branch_id = prefix_id + ":" + condition
                journal.append(
                    "BRANCH_START",
                    {
                        "prefix_id": prefix_id,
                        "branch_id": branch_id,
                        "condition": condition,
                        "parent": parent,
                    },
                )
                session = Session(branch_id, seed, max_decisions, max_ticks)
                replay = session.reconstruct_prefix(candidate, parent)
                if (
                    obj(replay["checkpoint"])["state_digest"]
                    != obj(result["checkpoint"])["state_digest"]
                ):
                    raise ValueError("common checkpoint mismatch")
                journal.append("PREFIX_REPLAY", replay)
                journal.append("INTERVENTION", session.install_branch(condition))
                provider = FakeProvider(script)
                while session.stop == "RUNNING":
                    invoke(provider)
                branch = seal_export(
                    plan,
                    {"prefix_id": prefix_id, "branch_id": branch_id, "condition": condition},
                    session.snapshot(),
                )
                first = obj(session.steps[0])["semantic_input"]
                branch["first_move_policies"] = {
                    policy: first_policy(obj(first), policy)
                    for policy in ("OLD-MAP", "RECEIVED-UPDATE")
                }
                branch["record_sha256"] = digest(
                    {k: v for k, v in branch.items() if k != "record_sha256"}
                )
                filename = f"{prefix_id}-{condition}.json"
                write_snapshot(directory / filename, branch)
                journal.append(
                    "BRANCH_END", {"file": filename, "record_sha256": branch["record_sha256"]}
                )
                terminal = session.stop
                session.close()
                session = None
                if terminal == "INTEGRITY":
                    stop = "INTEGRITY"
                    break
            if stop == "INTEGRITY":
                break
        phase = "end"
        journal.append("END", {"fake_provider_calls": calls, "stop": stop})
    except Exception:
        stop = "INTEGRITY_RECORDING"
        write_snapshot(
            directory / "recording-failure.json",
            {
                "phase": phase,
                "prefix_id": prefix_id,
                "branch_id": branch_id,
                "calls_attempted": calls,
            },
        )
    finally:
        if session is not None:
            session.close()
        journal.close()
    from journeymap.experiments.route_update_audit import audit_directory

    return audit_directory(directory, expected_plan_sha256=cast(str, plan["plan_sha256"]))
