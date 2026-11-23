"""Independent E04 reconstruction without Provider, key, network or source lookup."""

from pathlib import Path
from typing import cast

from journeymap.adapters.provider import RawModelResponse
from journeymap.adapters.route_update import SCHEMA, obj, rows, shape
from journeymap.adapters.route_update_policy import first_policy
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.experiments.pickup_cue_live_audit import read_json
from journeymap.experiments.pilot_journal import digest, inspect_journal
from journeymap.experiments.route_update import LEGACY_PROTOCOL, protocol_profile, seal_export
from journeymap.experiments.route_update_session import Session
from journeymap.scenarios.route_update import CONDITIONS, VERSION, authored_map


def same(actual: JsonValue, expected: JsonValue) -> None:
    if canonical_json(actual) != canonical_json(expected):
        raise ValueError("E04 independent reconstruction mismatch")


def check_first_inputs(base: JsonObject, other: JsonObject, *, notified: bool) -> None:
    if not notified:
        same(base["wire"], other["wire"])
        return
    original = obj(base["semantic_input"])
    changed = obj(other["semantic_input"])
    same(original["event_memory"], changed["event_memory"])
    old, new = obj(original["observation"]), obj(changed["observation"])
    same(
        {k: v for k, v in old.items() if k != "knowledge"},
        {k: v for k, v in new.items() if k != "knowledge"},
    )
    entries = rows(new["knowledge"])
    notices = [r for r in entries if r["source_kind"] == "OFFICIAL_NOTICE"]
    same(
        cast(list[JsonValue], [r for r in entries if r["source_kind"] != "OFFICIAL_NOTICE"]),
        old["knowledge"],
    )
    same(
        cast(list[JsonValue], notices),
        [
            {
                "source_kind": "OFFICIAL_NOTICE",
                "learned_at": 3,
                "claim": {
                    "kind": "route-status",
                    "route_ids": ["C-to-G", "G-to-C"],
                    "passable": False,
                },
            }
        ],
    )
    # Full wire reconstruction below checks profile, settings and schema too.
    same(
        {k: v for k, v in obj(obj(base["wire"])["body"]).items() if k != "input"},
        {k: v for k, v in obj(obj(other["wire"])["body"]).items() if k != "input"},
    )


def summarize(prefixes: list[JsonObject], branches: list[JsonObject]) -> JsonObject:
    valid = [r for r in branches if isinstance(r.get("metrics"), dict)]
    metrics = [obj(r["metrics"]) for r in valid]
    appropriate = sum(m["first_appropriate"] is True for m in metrics)
    eligible = sum(type(m["first_appropriate"]) is bool for m in metrics)
    generated = sum(r["generated"] is True for r in branches)
    completed = sum(m["eventual_arrival"] is True for m in metrics)
    return {
        "initial_attempts": sum(cast(int, r["provider_attempts"]) for r in prefixes),
        "normal_prefixes": sum(r["status"] == "PREFIX_READY" for r in prefixes),
        "A_prefixes": sum(r["position"] == "A" and r["status"] == "PREFIX_READY" for r in prefixes),
        "B_prefixes": sum(r["position"] == "B" and r["status"] == "PREFIX_READY" for r in prefixes),
        "generated_branches": generated,
        "expected_branches_of_ready_prefixes": len(branches),
        "first_response_status_counts": {
            kind: sum(r["first_response_status"] == kind for r in branches)
            for kind in (
                "VALID",
                "REFUSAL",
                "INCOMPLETE",
                "MALFORMED",
                "INFRASTRUCTURE",
                "INTEGRITY",
                "ABSENT",
            )
        },
        "judgeable_first_action_denominator": eligible,
        "appropriate_first_MOVE": appropriate,
        "appropriateness_ratio": appropriate / eligible if eligible else None,
        "operational_success_denominator": generated,
        "eventual_arrivals": completed,
        "operational_arrival_ratio": completed / generated if generated else None,
        "verified_eventual_denominator": sum(type(m["eventual_arrival"]) is bool for m in metrics),
        "recovered_arrivals": sum(m["recovered_arrival"] is True for m in metrics),
        "recovery_eligible_denominator": sum(
            m["first_appropriate"] is False and type(m["eventual_arrival"]) is bool for m in metrics
        ),
        "decisions": sum(cast(int, m["decisions"]) for m in metrics),
        "submitted_actions": sum(cast(int, m["submitted_actions"]) for m in metrics),
        "elapsed_ticks": sum(cast(int, m["elapsed_ticks"]) for m in metrics),
        "completion_cost_denominator": sum(m["eventual_arrival"] is True for m in metrics),
        "completion_ticks_sum": sum(
            cast(int, m["completion_ticks"]) for m in metrics if m["eventual_arrival"] is True
        ),
        "extra_ticks_sum": sum(
            cast(int, m["extra_ticks"]) for m in metrics if m["eventual_arrival"] is True
        ),
        "termination_counts": {
            reason: sum(r["status"] == reason for r in branches)
            for reason in sorted({cast(str, r["status"]) for r in branches})
        },
        "incomplete_pairs": [
            {
                "prefix_id": r["prefix_id"],
                "branch_statuses": [
                    {
                        "condition": b["condition"],
                        "status": b["status"],
                        "generated": b["generated"],
                    }
                    for b in branches
                    if b["prefix_id"] == r["prefix_id"]
                ],
            }
            for r in prefixes
            if r["status"] == "PREFIX_READY"
            and sum(
                b["prefix_id"] == r["prefix_id"]
                and b["generated"] is True
                and b["status"] not in ("INTEGRITY", "UNKNOWN", "NOT_ATTEMPTED", "INFRASTRUCTURE")
                for b in branches
            )
            != 3
        ],
    }


class Incomplete(Exception):
    pass


def audit_directory(directory: Path, *, expected_plan_sha256: str | None = None) -> JsonObject:
    prefixes: list[JsonObject] = []
    branches: list[JsonObject] = []
    result: JsonObject = {
        "audit_version": "experiment04-offline-audit-1",
        "status": "EXCLUDED",
        "mode": "fake-only",
        "api_usage": None,
        "usage_applicability": "NOT_APPLICABLE_FAKE",
    }
    session: Session | None = None
    affected: JsonObject | None = None
    files = {"plan.json", "attempts.jsonl"}
    cursor, calls = 0, 0
    try:
        plan = read_json(directory / "plan.json")
        if expected_plan_sha256 is not None:
            same(plan["plan_sha256"], expected_plan_sha256)
        same(plan["plan_sha256"], digest({k: v for k, v in plan.items() if k != "plan_sha256"}))
        shape(
            plan,
            "protocol export_version scenario_version mode state prefix_count conditions "
            "seed max_decisions max_elapsed_ticks initial_map profile_version profile_instructions "
            "schema source runtime experiment_model experiment_parameters api_budget "
            "mirror_mapping "
            "main_experiment_order api_usage provider_usage_applicability retry fallback "
            "sample_until_A plan_sha256",
        )
        export_version, profile = protocol_profile(plan["protocol"])
        if plan["protocol"] != LEGACY_PROTOCOL:
            result["audit_version"] = "experiment04-offline-audit-2"
        expected_values: JsonObject = {
            "export_version": export_version,
            "scenario_version": VERSION,
            "mode": "fake-only",
            "state": "PROTOCOL_DRAFT",
            "conditions": list(CONDITIONS),
            "initial_map": authored_map(),
            "profile_version": profile.version,
            "profile_instructions": profile.instructions,
            "schema": SCHEMA,
            "retry": False,
            "fallback": False,
            "sample_until_A": False,
            "api_usage": None,
            "experiment_model": None,
            "experiment_parameters": None,
            "api_budget": None,
            "mirror_mapping": None,
            "main_experiment_order": None,
            "provider_usage_applicability": "NOT_APPLICABLE_FAKE",
        }
        for key, expected in expected_values.items():
            same(plan[key], expected)
        if (
            type(plan["prefix_count"]) is not int
            or plan["prefix_count"] <= 0
            or type(plan["seed"]) is not int
            or type(plan["max_decisions"]) is not int
            or plan["max_decisions"] != 6
            or type(plan["max_elapsed_ticks"]) is not int
            or plan["max_elapsed_ticks"] != 24
        ):
            raise ValueError("invalid plan bounds")
        journal = inspect_journal(directory / "attempts.jsonl")
        stream = journal.rows
        result["journal_status"] = journal.status
        prefixes.extend(
            {
                "prefix_id": f"p{number:03d}",
                "status": "NOT_ATTEMPTED",
                "position": None,
                "provider_attempts": 0,
                "initial_response_status": "ABSENT",
            }
            for number in range(1, plan["prefix_count"] + 1)
        )
        result["planned_initial_denominator"] = plan["prefix_count"]

        def pop(kind: str) -> JsonObject:
            nonlocal cursor
            if cursor == len(stream):
                raise Incomplete
            row = stream[cursor]
            same(row["kind"], kind)
            cursor += 1
            return obj(row["data"])

        def exported(kind: str, record: JsonObject) -> None:
            linkage = pop(kind)
            shape(linkage, "file record_sha256")
            filename = cast(str, linkage["file"])
            if (
                Path(filename).name != filename
                or not filename.endswith(".json")
                or filename in files
            ):
                raise ValueError("unsafe or duplicate export")
            actual = read_json(directory / filename)
            same(actual, record)
            same(linkage["record_sha256"], record["record_sha256"])
            files.add(filename)

        def decision(prefix_id: str, branch_id: str | None, *, initial: bool = False) -> JsonObject:
            nonlocal calls
            if session is None:
                raise ValueError("missing audit runtime")
            prepared = session.prepare(initial=initial)
            same(
                pop("INTENT"),
                {
                    "prefix_id": prefix_id,
                    "branch_id": branch_id,
                    "number": 0 if initial else len(session.steps) + 1,
                    "prepared": prepared,
                },
            )
            calls += 1
            if initial and affected is not None:
                affected["provider_attempts"] = 1
            raw = pop("RESPONSE")
            shape(raw, "raw_response failure")
            response = raw["raw_response"]
            if isinstance(response, dict):
                shape(response, "text metadata schema_version")
                decoded = RawModelResponse(
                    cast(str, response["text"]),
                    obj(response["metadata"]),
                    cast(int, response["schema_version"]),
                )
            else:
                decoded = None
            failure = obj(raw["failure"]) if isinstance(raw["failure"], dict) else None
            if failure is not None:
                shape(failure, "kind")
                if failure["kind"] != "TRANSPORT_ERROR":
                    raise ValueError("unsupported offline fixture failure")
            evaluated = session.finish(decoded, failure, initial=initial)
            same(pop("RESULT"), evaluated)
            return evaluated

        same(pop("PLAN"), {"plan_sha256": plan["plan_sha256"], "mode": "fake-only"})
        for number in range(1, plan["prefix_count"] + 1):
            prefix_id = f"p{number:03d}"
            affected = prefixes[number - 1]
            if cursor < len(stream) and stream[cursor]["kind"] == "END":
                continue
            same(pop("INITIAL_START"), {"prefix_id": prefix_id})
            affected["status"] = "UNKNOWN"
            session = Session(
                prefix_id + ":initial",
                plan["seed"],
                plan["max_decisions"],
                plan["max_elapsed_ticks"],
                profile_version=profile.version,
            )
            snapshot = decision(prefix_id, None, initial=True)
            record = seal_export(plan, {"prefix_id": prefix_id, "branch_id": None}, snapshot)
            exported("INITIAL_END", record)
            affected.update(
                {
                    "status": snapshot["stop"],
                    "position": session.position,
                    "initial_response_status": obj(snapshot["prefix"])["response_status"],
                }
            )
            session.close()
            session = None
            if snapshot["stop"] != "PREFIX_READY":
                continue
            parent: JsonObject = {
                "record_sha256": record["record_sha256"],
                "parent_run_id": snapshot["run_id"],
                "original_response_sha256": digest(obj(obj(snapshot["prefix"])["raw_response"])),
            }
            candidate = obj(obj(snapshot["prefix"])["candidate"])
            comparisons: JsonObject | None = None
            siblings: list[JsonObject] = [
                {
                    "prefix_id": prefix_id,
                    "branch_id": prefix_id + ":" + condition,
                    "condition": condition,
                    "prefix_position": obj(snapshot["checkpoint"])["position"],
                    "generated": False,
                    "status": "NOT_ATTEMPTED",
                    "metrics": None,
                    "first_response_status": "ABSENT",
                }
                for condition in CONDITIONS
            ]
            branches.extend(siblings)
            for condition, affected in zip(CONDITIONS, siblings, strict=True):
                branch_id = prefix_id + ":" + condition
                if cursor < len(stream) and stream[cursor]["kind"] == "END":
                    continue
                same(
                    pop("BRANCH_START"),
                    {
                        "prefix_id": prefix_id,
                        "branch_id": branch_id,
                        "condition": condition,
                        "parent": parent,
                    },
                )
                affected.update({"generated": True, "status": "UNKNOWN"})
                session = Session(
                    branch_id,
                    plan["seed"],
                    plan["max_decisions"],
                    plan["max_elapsed_ticks"],
                    profile_version=profile.version,
                )
                same(pop("PREFIX_REPLAY"), session.reconstruct_prefix(candidate, parent))
                same(session.checkpoint, snapshot["checkpoint"])
                same(pop("INTERVENTION"), session.install_branch(condition))
                while session.stop == "RUNNING":
                    if len(session.steps) == 0:
                        # Peek the recorded first stage; decision() reconstructs it fully.
                        if cursor == len(stream):
                            raise Incomplete
                        prepared = obj(obj(stream[cursor]["data"])["prepared"])
                        if comparisons is None:
                            comparisons = prepared
                        else:
                            check_first_inputs(
                                comparisons, prepared, notified=condition == "closed-notified"
                            )
                    decision(prefix_id, branch_id)
                    affected["metrics"] = session.snapshot()["metrics"]
                    affected["first_response_status"] = obj(session.steps[0])["response_status"]
                branch = seal_export(
                    plan,
                    {"prefix_id": prefix_id, "branch_id": branch_id, "condition": condition},
                    session.snapshot(),
                )
                inputs = obj(session.steps[0]["semantic_input"])
                branch["first_move_policies"] = {
                    policy: first_policy(inputs, policy)
                    for policy in ("OLD-MAP", "RECEIVED-UPDATE")
                }
                branch["record_sha256"] = digest(
                    {k: v for k, v in branch.items() if k != "record_sha256"}
                )
                exported("BRANCH_END", branch)
                affected.update({"status": session.stop, "metrics": session.snapshot()["metrics"]})
                session.close()
                session = None
            affected = None
        end = pop("END")
        expected_stop = (
            "INTEGRITY"
            if any(r["status"] == "INTEGRITY" for r in (*prefixes, *branches))
            else "COMPLETE"
        )
        same(end, {"fake_provider_calls": calls, "stop": expected_stop})
        if (
            cursor != len(stream)
            or journal.status != "COMPLETE"
            or {p.name for p in directory.iterdir()} != files
        ):
            raise ValueError("incomplete, corrupt or extra output")
        result["status"] = "INCLUDED"
    except Incomplete:
        result["status"] = (
            "INCOMPLETE"
            if result.get("journal_status") in ("COMPLETE", "TORN_TAIL")
            else "EXCLUDED"
        )
        if result["status"] == "EXCLUDED":
            result["reason"] = "E04_CORRUPT_JOURNAL_VALID_PREFIX_ONLY"
        marker = directory / "recording-failure.json"
        if marker.is_file():
            try:
                recording = read_json(marker)
                shape(recording, "phase prefix_id branch_id calls_attempted")
                result["recording_failure"] = recording
                result["reason"] = "E04_RECORDING_FAILURE_VALID_PREFIX_ONLY"
                if affected is not None:
                    affected["status"] = "INTEGRITY_RECORDING"
            except (OSError, ValueError, TypeError, KeyError):
                result["status"] = "EXCLUDED"
                result["reason"] = "E04_INVALID_RECORDING_FAILURE_MARKER"
        if affected is not None and isinstance(affected.get("metrics"), dict):
            assessment = obj(affected["metrics"])
            for key in ("eventual_arrival", "recovered_arrival", "completion_ticks", "extra_ticks"):
                assessment[key] = None
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, OverflowError):
        result["reason"] = "E04_PLAN_JOURNAL_EXPORT_OR_RECONSTRUCTION_INTEGRITY"
        if affected is not None:
            affected["status"] = "INTEGRITY"
            if isinstance(affected.get("metrics"), dict):
                assessment = obj(affected["metrics"])
                for key in (
                    "eventual_arrival",
                    "recovered_arrival",
                    "completion_ticks",
                    "extra_ticks",
                ):
                    assessment[key] = None
    finally:
        if session is not None:
            session.close()
    result.update(
        {
            "prefixes": cast(list[JsonValue], prefixes),
            "branches": cast(list[JsonValue], branches),
            "fake_provider_decision_intents": calls,
            "summary": summarize(prefixes, branches),
        }
    )
    return result
