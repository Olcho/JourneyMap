"""Full offline protocol, immutable exports, bounded failures, replay proof."""

import json
from pathlib import Path
from typing import cast

import pytest
from test_m8_llm import FakeProvider

from journeymap.adapters.llm import LLMController, Record
from journeymap.adapters.provider import ProviderRequest, RawModelResponse
from journeymap.core.canonical import JsonObject
from journeymap.examples.alderwick_llm import ProtocolFakeProvider
from journeymap.experiments.alderwick import (
    BudgetMonitor,
    TrialPolicy,
    export_trial,
    replay_export,
    run_trial,
)


@pytest.fixture(scope="module")
def full_trial() -> Record:
    return run_trial(LLMController(ProtocolFakeProvider(), model="fixture-1"), trial_id="test-24h")


def test_full_24h_mixed_trial_and_engine_equality(full_trial: Record) -> None:
    data = full_trial.data
    manifest = cast(JsonObject, data["manifest"])
    assert manifest["simulation_end"] == 24 and manifest["simulation_hours"] == 24
    assert manifest["status"] == "COMPLETED" and manifest["replay_equal"] is True
    assert manifest["decisions"] == manifest["provider_calls"] == 12
    metrics = cast(JsonObject, data["metrics"])
    assert set(cast(JsonObject, metrics["engine_action_types"])) == {
        "MOVE",
        "WAIT",
        "ASK",
        "INFORM",
        "REQUEST",
        "BUY",
        "CONSUME",
        "REST",
    }
    assert metrics["engine_statuses"] == {"SUCCEEDED": 20, "REJECTED": 2}
    assert metrics["invalid_outputs"] == metrics["provider_failures"] == 0
    budget = cast(JsonObject, metrics["observation_bytes"])
    assert 0 < cast(int, budget["max"]) < 65_536 and budget["overflow_count"] == 0
    # Full knowledge/social history survives; Thomas retains conflicting claims.
    knowledge = cast(list[JsonObject], data["knowledge"])
    assert {
        item["value"]
        for item in knowledge
        if item["actor_id"] == "thomas" and item["predicate"] == "condition"
    } == {"intact", "collapsed"}
    for decision in cast(list[JsonObject], data["decisions"]):
        assert decision["memory_observation_ids"] == []
        assert decision["raw_output"] is not None and decision["parsed_candidate"] is not None
        assert decision["action_trace_sequences"]


def test_export_roundtrip_is_byte_deterministic_and_provider_free(
    full_trial: Record, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    one, two = tmp_path / "one", tmp_path / "two"
    export_trial(full_trial, one)
    export_trial(full_trial, two)
    assert {p.name: p.read_bytes() for p in one.iterdir()} == {
        p.name: p.read_bytes() for p in two.iterdir()
    }

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("replay must not run a controller/provider")

    monkeypatch.setattr(LLMController, "decide", forbidden)
    monkeypatch.setattr(ProtocolFakeProvider, "generate", forbidden)
    assert replay_export(one).final_simulation_time == 24
    with pytest.raises(FileExistsError):
        export_trial(full_trial, one)
    (one / "action_requests.jsonl").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        replay_export(one)


def test_export_failure_does_not_alter_record(
    full_trial: Record, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = full_trial.serialized

    def fail(*args: object) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", fail)
    with pytest.raises(OSError):
        export_trial(full_trial, tmp_path / "partial")
    assert full_trial.serialized == before
    assert not (tmp_path / "partial" / "manifest.json").exists()


@pytest.mark.parametrize("error", [False, True])
def test_repeated_invalid_or_provider_failures_terminate_without_mutation(error: bool) -> None:
    trial = run_trial(LLMController(FakeProvider("not JSON", error=error)), trial_id="failed")
    data = trial.data
    manifest = cast(JsonObject, data["manifest"])
    assert manifest["simulation_end"] == 0 and manifest["decisions"] == 3
    assert manifest["stop_reason"] == "CONSECUTIVE_FAILURES" and manifest["replay_equal"] is True
    assert data["initial_state"] == data["final_state"] and data["events"] == []
    assert data["action_requests"] == [] and len(cast(list[object], data["decisions"])) == 3


@pytest.mark.parametrize(
    ("policy", "reason"),
    [
        (TrialPolicy(max_decisions=1), "MAX_DECISIONS"),
        (TrialPolicy(max_provider_calls=1), "MAX_PROVIDER_CALLS"),
    ],
)
def test_explicit_call_and_decision_bounds(policy: TrialPolicy, reason: str) -> None:
    trial = run_trial(LLMController(FakeProvider()), trial_id="bounded", policy=policy)
    manifest = cast(JsonObject, trial.data["manifest"])
    assert manifest["provider_calls"] == 1 and manifest["stop_reason"] == reason


def test_observation_overflow_is_recorded_without_truncation_or_provider_call() -> None:
    trial = run_trial(
        LLMController(FakeProvider()),
        trial_id="overflow",
        monitor=BudgetMonitor(max_content_bytes=100),
    )
    data = trial.data
    manifest = cast(JsonObject, data["manifest"])
    assert manifest["provider_calls"] == 0 and manifest["simulation_end"] == 0
    assert data["observations"] == [] and data["action_requests"] == []
    attempt = cast(list[JsonObject], data["observation_attempts"])[0]
    assert attempt["overflow"] is True and cast(int, attempt["bytes"]) > 100
    assert len(cast(list[object], data["knowledge"])) == 6


def test_horizon_action_not_clipped_or_submitted() -> None:
    class LongWait:
        def generate(self, request: ProviderRequest) -> RawModelResponse:
            return RawModelResponse(
                json.dumps(
                    {
                        "action_type": "REST",
                        "payload": {"duration": 25},
                    }
                )
            )

    trial = run_trial(LLMController(LongWait()), trial_id="long")
    assert cast(JsonObject, trial.data["manifest"])["stop_reason"] == "HORIZON_ACTION"
    assert (
        trial.data["action_requests"] == []
        and trial.data["initial_state"] == trial.data["final_state"]
    )


def test_wall_timeout_discards_late_model_output_before_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    monkeypatch.setattr("journeymap.experiments.alderwick.monotonic", lambda: clock[0])

    class SlowProvider(FakeProvider):
        def generate(self, request: ProviderRequest) -> RawModelResponse:
            clock[0] = 100
            return super().generate(request)

    trial = run_trial(
        LLMController(SlowProvider()), trial_id="slow", policy=TrialPolicy(wall_timeout_seconds=1)
    )
    manifest = cast(JsonObject, trial.data["manifest"])
    assert manifest["stop_reason"] == "WALL_TIMEOUT" and manifest["simulation_end"] == 0
    assert manifest["provider_calls"] == 1 and trial.data["action_requests"] == []


def test_full_trial_repeat_engine_artifacts_are_deterministic(full_trial: Record) -> None:
    second = run_trial(
        LLMController(ProtocolFakeProvider(), model="fixture-1"), trial_id="test-24h"
    )
    for key in ("engine_report", "observations", "knowledge", "action_traces", "replay_input"):
        assert full_trial.data[key] == second.data[key]


@pytest.mark.parametrize("value", [0, -1, True])
def test_invalid_policy_bounds(value: int) -> None:
    with pytest.raises(ValueError):
        TrialPolicy(max_provider_calls=value)
