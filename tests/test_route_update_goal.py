"""E04 goal/evaluation alignment and exact v1/v2 recorded-data compatibility."""

import shutil
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
import test_route_update as support

from journeymap.adapters.route_update import (
    LEGACY_PROFILE,
    PROFILE,
    SCHEMA,
    FakeProvider,
    Script,
    obj,
    profile_for,
    request,
    rows,
    wire,
)
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.experiments.pickup_cue_live_audit import read_json
from journeymap.experiments.pilot_journal import digest
from journeymap.experiments.route_update import (
    EXPORT_VERSION,
    LEGACY_PROTOCOL,
    PROTOCOL,
    move,
    protocol_profile,
    seal_export,
)
from journeymap.experiments.route_update_audit import audit_directory, check_first_inputs
from journeymap.experiments.route_update_session import Session
from journeymap.scenarios.route_update import CONDITIONS

offline = support.offline
demo = support.demo


def test_complete_v2_objective_is_time_scoped_without_a_condition_answer() -> None:
    assert PROFILE.version == "experiment04-route-change-decision-2"
    assert LEGACY_PROFILE.version == "experiment04-route-change-decision-1"
    assert PROFILE.instructions.startswith(LEGACY_PROFILE.instructions)
    addition = PROFILE.instructions[len(LEGACY_PROFILE.instructions) :]
    assert "simulation time 0" in addition
    assert "does not apply to that initial choice" in addition
    assert "simulation time 3 or later" in addition
    assert "minimizes the remaining\nsimulated travel time" in addition
    assert "route information available to you" in addition
    assert "upper bounds" in addition
    assert "received updates and direct observations" in addition
    assert "Do not assume access to unobserved world state" in addition
    for answer in (*CONDITIONS, "A-to-D", "A-to-C", "C-to-G", "closure", "closed", "S-to-A"):
        assert answer not in addition
    assert protocol_profile(PROTOCOL) == (EXPORT_VERSION, PROFILE)
    assert protocol_profile(LEGACY_PROTOCOL) == ("experiment04-offline-export-1", LEGACY_PROFILE)


@pytest.mark.parametrize("position", ["A", "B"])
def test_v2_same_followup_objective_full_wire_and_unfixed_initial_choice(position: str) -> None:
    sessions = [Session("goal:" + condition) for condition in CONDITIONS]
    try:
        firsts: list[JsonObject] = []
        for session, condition in zip(sessions, CONDITIONS, strict=True):
            initial = session.prepare(initial=True)
            prompt = cast(str, obj(obj(initial["wire"])["body"])["input"])
            assert PROFILE.instructions in prompt and "simulation time 0" in prompt
            provider = FakeProvider(Script((move("S-to-" + position),)))
            completed = session.finish(
                provider.generate(session.provider_request), None, initial=True
            )
            assert completed["stop"] == "PREFIX_READY" and session.position == position
            session.install_branch(condition)
            prepared = session.prepare()
            prompt = cast(str, obj(obj(prepared["wire"])["body"])["input"])
            assert prompt.split("\nINPUT_JSON\n")[0] == PROFILE.instructions
            assert "simulation time 3 or later" in prompt
            assert obj(obj(obj(prepared["wire"])["body"])["text"])["format"] == {
                "type": "json_schema",
                "name": "experiment04_action",
                "strict": True,
                "schema": SCHEMA,
            }
            firsts.append(prepared)
        check_first_inputs(firsts[0], firsts[1], notified=False)
        check_first_inputs(firsts[0], firsts[2], notified=True)
        assert obj(firsts[0]["wire"])["canonical_utf8"] == obj(firsts[1]["wire"])["canonical_utf8"]
    finally:
        for session in sessions:
            session.close()


def test_open_map_longer_route_reaches_goal_but_first_move_is_not_time_optimal() -> None:
    session = support.branch("A", "unchanged")
    try:
        support.decisions(session, (move("A-to-D"), move("D-to-G")))
        assert session.stop == "ARRIVED"
        metrics = obj(session.snapshot()["metrics"])
        assert metrics["eventual_arrival"] is True
        assert metrics["first_appropriate"] is False
        assert metrics["completion_ticks"] == 6 and metrics["extra_ticks"] == 2
        assert session.baseline == {"minimum_cost": 4, "optimal_first_routes": ["A-to-C"]}
    finally:
        session.close()


@pytest.mark.parametrize(
    "name,status",
    [
        ("demo", "INCLUDED"),
        ("response", "INCLUDED"),
        ("wait", "INCLUDED"),
        ("time", "INCLUDED"),
        ("initial", "INCLUDED"),
        ("integrity", "INCLUDED"),
        ("recording", "INCOMPLETE"),
        ("tamper", "EXCLUDED"),
    ],
)
def test_original_v1_raw_audits_remain_exact_and_provider_free(
    name: str,
    status: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = (
        Path(__file__).resolve().parents[1] / "trials/experiment04-offline-verification-20261008"
    )
    if not original.is_dir():
        pytest.skip("Historical local v1 raw fixture not supplied")
    directory = original / (name + "-final")
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    monkeypatch.setattr(FakeProvider, "generate", support.forbidden)
    plan = read_json(directory / "plan.json")
    assert plan["protocol"] == LEGACY_PROTOCOL
    report = audit_directory(directory, expected_plan_sha256=cast(str, plan["plan_sha256"]))
    assert report["status"] == status
    assert report == read_json(original / (name + "-final-audit.json"))
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}


def test_v1_initial_and_followup_request_rendering_matches_original_bytes() -> None:
    old = Path(__file__).resolve().parents[1] / "trials/experiment04-offline-verification-20261008"
    if not old.is_dir():
        pytest.skip("Historical local v1 raw fixture not supplied")
    initial = obj(read_json(old / "demo-final/p001.json")["result"])
    followup = obj(read_json(old / "demo-final/p001-unchanged.json")["result"])
    for saved in (obj(initial["prefix"]), rows(followup["steps"])[0]):
        legacy = request(obj(saved["semantic_input"]), profile_version=LEGACY_PROFILE.version)
        assert wire(legacy) == saved["wire"]
        assert wire(request(obj(saved["semantic_input"]))) != saved["wire"]


@pytest.mark.parametrize(
    "version", ["experiment04-route-change-decision-0", "experiment04-route-change-decision-3", ""]
)
def test_unknown_profile_fails_before_a_provider_or_runtime(version: str) -> None:
    with pytest.raises(ValueError, match="unsupported E04 profile"):
        profile_for(version)
    with pytest.raises(ValueError, match="unsupported E04 profile"):
        Session("unsupported", profile_version=version)
    value = request({"observation": {}, "event_memory": []})
    with pytest.raises(ValueError):
        wire(replace(value, prompt_version=version))


@pytest.mark.parametrize(
    "field,value",
    [
        ("protocol", "experiment04-offline-0"),
        ("protocol", "experiment04-offline-3"),
        ("profile_version", LEGACY_PROFILE.version),
        ("profile_instructions", LEGACY_PROFILE.instructions),
        ("export_version", "experiment04-offline-export-1"),
    ],
)
def test_unknown_or_mixed_versions_never_included(
    tmp_path: Path,
    demo: tuple[Path, JsonObject],
    field: str,
    value: JsonValue,
) -> None:
    directory = tmp_path / "mixed"
    shutil.copytree(demo[0], directory)
    path = directory / "plan.json"
    plan = read_json(path)
    plan[field] = value
    plan["plan_sha256"] = digest({k: v for k, v in plan.items() if k != "plan_sha256"})
    path.write_text(canonical_json(plan), encoding="utf-8")
    result = audit_directory(directory)
    assert result["status"] == "EXCLUDED" and result.get("planned_initial_denominator") is None


def test_new_plan_and_export_are_bound_to_v2_and_unknown_export_is_rejected(
    demo: tuple[Path, JsonObject],
) -> None:
    directory, report = demo
    plan = read_json(directory / "plan.json")
    assert plan["protocol"] == "experiment04-offline-2"
    assert plan["export_version"] == "experiment04-offline-export-2"
    assert plan["profile_version"] == PROFILE.version
    assert plan["profile_instructions"] == PROFILE.instructions
    assert report["audit_version"] == "experiment04-offline-audit-2"
    for path in directory.glob("p0*.json"):
        assert read_json(path)["protocol"] == PROTOCOL
        assert read_json(path)["export_version"] == EXPORT_VERSION
    plan["export_version"] = "unknown"
    with pytest.raises(ValueError):
        seal_export(plan, {}, {})
