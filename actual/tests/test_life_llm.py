"""Offline Provider → candidate → bound port → actual engine and direct restore."""

import json
import subprocess
import sys
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from journeymap.adapters.decision_schema import decision_schema
from journeymap.adapters.life_llm import (
    LifeLLMConfig,
    life_body,
    life_candidate,
    life_request,
    offline_provider,
)
from journeymap.adapters.provider import ProviderFailure, ProviderRequest, RawModelResponse
from journeymap.experiments.checkpoint import checkpoint_digest, load_checkpoint, save_checkpoint
from journeymap.experiments.life import LifeSession
from journeymap.experiments.live import LiveLife
from journeymap.experiments.mixed_life import MixedLifeSession
from journeymap.scenarios.life import ACTORS, LifeConfig


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*args: object, **kwargs: object) -> None:
        raise AssertionError("keys/network forbidden")

    monkeypatch.setattr("socket.socket", deny)
    monkeypatch.setattr("socket.getaddrinfo", deny)
    import os

    original_get = os.environ.get

    def guarded_get(key: str, default: Any = None) -> Any:
        if key == "OPENAI_API_KEY":
            deny()
        return original_get(key, default)

    monkeypatch.setattr(os.environ, "get", guarded_get)


def response(action: str = "WAIT", payload: dict[str, Any] | None = None) -> RawModelResponse:
    return RawModelResponse(
        json.dumps({"action_type": action, "payload": payload or {"duration": 2}})
    )


class GateProvider:
    def __init__(self, result: RawModelResponse | Exception | None = None) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.returned = threading.Event()
        self.result = result or response()
        self.count = 0
        self.requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> RawModelResponse:
        self.count += 1
        self.requests.append(request)
        self.entered.set()
        assert self.release.wait(2), "test must release worker"
        self.returned.set()
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def ready(session: MixedLifeSession) -> None:
    session.decider.wait()
    session.decider.poll()


def test_mixed_actual_life_and_no_rule_substitution(monkeypatch: pytest.MonkeyPatch) -> None:
    session = MixedLifeSession(LifeConfig(horizon=24))

    def deny(*args: object) -> None:
        raise AssertionError("Ada rule policy must not decide behind the adapter")

    monkeypatch.setattr(session.controllers["ada"], "decide", deny)
    try:
        session.run_until(24)
        calls = session.decider.state.calls
        assert len(calls) == session.decider.state.requests_started == 12
        assert {c.action.action_type for c in calls if c.action} >= {
            "MOVE",
            "WORK",
            "BUY",
            "CONSUME",
            "REST",
        }
        assert all(c.status == "SUCCEEDED" and c.receipt and c.final_result for c in calls)
        assert {d.actor_id for d in session.decisions} == set(ACTORS) - {"ada"}
        assert session.replay_recorded()["provider_recalled"] is False
        assert session.kernel.simulation_time == 24
    finally:
        session.close()


@pytest.mark.parametrize(
    "action,payload,place",
    [
        ("MOVE", {"route_id": "home-square"}, "square"),
        ("WAIT", {"duration": 2}, "homes"),
    ],
)
def test_candidate_actually_changes_action(
    action: str, payload: dict[str, Any], place: str
) -> None:
    session = MixedLifeSession(
        LifeConfig(horizon=24), LifeLLMConfig(responses=(response(action, payload),))
    )
    try:
        session.run_until(1)
        chosen = session.decider.state.calls[0].action
        assert chosen is not None and chosen.action_type == action
        world: Any = session.kernel.state_snapshot
        assert world["movement"]["positions"]["ada"]["location_id"] == place
    finally:
        session.close()


@pytest.mark.parametrize(
    "text",
    [
        "not JSON",
        '{"action_type":"WAIT","payload":{"duration":true}}',
        '{"action_type":"WORK","payload":{"job_id":"bake","stock":99}}',
        '{"action_type":"WAIT","payload":{"duration":2},"actor_id":"bert"}',
        '{"action_type":"BUY","payload":{"offer_id":"bread-sale","quantity":false}}',
        '{"action_type":"ASK","payload":{"target_actor_id":"bert","predicate":"wallet"}}',
        '{"action_type":"WAIT","action_type":"MOVE","payload":{}}',
        '{"action_type":"WORK","payload":{"job_id":true}}',
        '{"action_type":"WAIT","payload":{"duration":0}}',
        '{"action_type":"WAIT","payload":{"duration":NaN}}',
    ],
)
def test_bad_wire_stops_without_fallback(text: str) -> None:
    session = MixedLifeSession(
        LifeConfig(horizon=24), LifeLLMConfig(responses=(RawModelResponse(text),))
    )
    try:
        with pytest.raises(ValueError, match="PAUSED_ERROR"):
            session.run_until(1)
        assert session.kernel.simulation_time == 0
        assert len(session.application.attempts) == 0
        assert session.decider.state.calls[0].error == "INVALID_CANDIDATE"
        assert session.decider.state.requests_started == 1
        assert not session.progress(1, 0)
        assert session.decider.state.requests_started == 1
    finally:
        session.close()


@pytest.mark.parametrize(
    "metadata,error", [({"refusal": True}, "REFUSAL"), ({"status": "incomplete"}, "INCOMPLETE")]
)
def test_provider_semantic_failures(metadata: dict[str, Any], error: str) -> None:
    session = MixedLifeSession(
        LifeConfig(horizon=24),
        LifeLLMConfig(responses=(RawModelResponse(response().text, metadata),)),
    )
    try:
        with pytest.raises(ValueError, match="PAUSED_ERROR"):
            session.run_until(1)
        assert session.decider.state.calls[0].error == error
        assert not session.application.attempts
    finally:
        session.close()


def test_delayed_response_pause_poll_speed_and_no_duplicate(tmp_path: Path) -> None:
    provider = GateProvider(response("MOVE", {"route_id": "home-square"}))
    session = MixedLifeSession(LifeConfig(horizon=24), provider=provider)
    live = LiveLife(session, tmp_path / "state.json")
    try:
        live.command({"command": "step"}, 0)
        assert provider.entered.wait(1)
        frozen = session.kernel.execution_snapshot
        observations = len(session.observations)
        for tick in range(10):
            live.view()
            live.view("ada")
            live.command({"command": "start"}, 0)
            live.command({"command": "speed", "value": 16}, 0)
            live.pump(tick / 100)
        assert provider.count == 1
        assert session.kernel.execution_snapshot == frozen
        assert len(session.observations) == observations
        assert live.view()["status"] == "WAITING_FOR_DECISION"
        with pytest.raises(ValueError, match="external request"):
            save_checkpoint(session, tmp_path / "unsafe.json")
        with pytest.raises(ValueError, match="external request"):
            live.command({"command": "restore"}, 0)
        live.command({"command": "pause"}, 0)
        provider.release.set()
        assert provider.returned.wait(1)
        ready(session)
        live.pump(0.5)
        assert live.view()["status"] == "DECISION_READY_PAUSED"
        assert session.kernel.execution_snapshot == frozen
        live.command({"command": "step"}, 0.5)
        assert session.kernel.simulation_time == 1
        assert provider.count == 1
        assert len([a for a in session.application.attempts if a.request.actor_id == "ada"]) == 1
    finally:
        provider.release.set()
        session.close()


def test_timeout_and_late_response_discard_without_sleep(tmp_path: Path) -> None:
    provider = GateProvider()
    clock = [0.0]
    session = MixedLifeSession(
        LifeConfig(horizon=24), provider=provider, decision_clock=lambda: clock[0]
    )
    try:
        assert not session.progress(1, 0)
        assert provider.entered.wait(1)
        clock[0] = 6.0
        session.decider.poll()
        assert session.decider.state.calls[0].error == "TIMEOUT"
        assert session.decider.state.status == "PAUSED_ERROR"
        with pytest.raises(ValueError):
            save_checkpoint(session, tmp_path / "uncertain.json")
        provider.release.set()
        assert provider.returned.wait(1)
        ready(session)
        assert session.decider.state.calls[0].discarded_late
        assert session.kernel.simulation_time == 0 and not session.application.attempts
    finally:
        provider.release.set()
        session.close()


def test_closed_session_discards_late_answer() -> None:
    provider = GateProvider()
    session = MixedLifeSession(LifeConfig(horizon=24), provider=provider)
    assert not session.progress(1, 0)
    assert provider.entered.wait(1)
    session.close()
    provider.release.set()
    assert provider.returned.wait(1)
    session.decider.poll()
    assert not session.application.attempts
    assert session.kernel.simulation_time == 0


@pytest.mark.parametrize("kind", ["TRANSPORT_ERROR", "HTTP_ERROR"])
def test_transport_failure_is_distinct(kind: str) -> None:
    provider = GateProvider(ProviderFailure(kind))
    provider.release.set()
    session = MixedLifeSession(LifeConfig(horizon=24), provider=provider)
    try:
        with pytest.raises(ValueError, match="PAUSED_ERROR"):
            session.run_until(1)
        assert session.decider.state.calls[0].error == kind
        assert session.decider.state.requests_started == provider.count == 1
        assert not session.application.attempts
    finally:
        session.close()


@pytest.mark.parametrize(
    "action,payload", [("MOVE", {"route_id": "missing"}), ("WORK", {"job_id": "missing"})]
)
def test_valid_wire_engine_rejections_stop_after_bounded_ticks(
    action: str, payload: dict[str, Any]
) -> None:
    session = MixedLifeSession(
        LifeConfig(horizon=24), LifeLLMConfig(responses=(response(action, payload),) * 10)
    )
    try:
        with pytest.raises(ValueError, match="LIMIT_REACHED"):
            session.run_until(24)
        assert session.decider.state.requests_started == 3
        assert session.kernel.simulation_time == 2
        assert all(
            c.candidate is not None and c.error is None and c.status == "REJECTED"
            for c in session.decider.state.calls
        )
    finally:
        session.close()


def test_engine_completion_failure_is_not_provider_failure() -> None:
    session = MixedLifeSession(LifeConfig(horizon=24, closure_tick=2))
    try:
        session.run_until(3)
        call = session.decider.state.calls[1]
        assert call.error is None and call.status == "FAILED"
        assert call.final_result and call.final_result.reason_code == "ROUTE_IMPASSABLE"
    finally:
        session.close()


def test_request_limit_preserved_by_restore(tmp_path: Path) -> None:
    session = MixedLifeSession(LifeConfig(horizon=24), LifeLLMConfig(request_limit=1))
    path = tmp_path / "limited.json"
    try:
        with pytest.raises(ValueError, match="LIMIT_REACHED"):
            session.run_until(24)
        save_checkpoint(session, path)
        restored = load_checkpoint(path)
        assert isinstance(restored, MixedLifeSession)
        try:
            assert restored.decider.state.requests_started == 1
            assert not restored.progress(24, 0)
            assert restored.decider.state.requests_started == 1
            assert checkpoint_digest(restored) == checkpoint_digest(session)
        finally:
            restored.close()
    finally:
        session.close()


@pytest.mark.parametrize("tick,kind", [(2, "MOVE"), (5, "WORK"), (13, "REST")])
def test_fresh_process_mixed_checkpoint(tmp_path: Path, tick: int, kind: str) -> None:
    session = MixedLifeSession(LifeConfig(horizon=24))
    continuous = MixedLifeSession(LifeConfig(horizon=24))
    path, resumed = tmp_path / "saved.json", tmp_path / "resumed.json"
    try:
        session.run_until(tick)
        assert any(
            f.request.actor_id == "ada" and f.request.action_type == kind
            for f in session.kernel.in_flight
        )
        save_checkpoint(session, path)
        session.close()
        code = """
import sys,runpy

def deny(event,args):
    if event.startswith('socket.'):
        raise RuntimeError('offline')
sys.addaudithook(deny)
runpy.run_module('journeymap.examples.life_mixed',run_name='__main__')
"""
        subprocess.run(
            [sys.executable, "-c", code, "--resume", str(path), "--save", str(resumed)],
            capture_output=True,
            text=True,
            check=True,
        )
        restored = load_checkpoint(resumed)
        try:
            continuous.run_until(24)
            assert checkpoint_digest(restored) == checkpoint_digest(continuous)
            assert restored.inputs == continuous.inputs
        finally:
            restored.close()
    finally:
        session.close()
        continuous.close()


def test_received_unsubmitted_checkpoint_does_not_recall_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MixedLifeSession(LifeConfig(horizon=24))
    path = tmp_path / "ready.json"
    try:
        assert not session.progress(1, 0)
        ready(session)
        assert session.decider.state.status == "READY"
        save_checkpoint(session, path)
        restored = load_checkpoint(path)
        assert isinstance(restored, MixedLifeSession)

        def deny(*args: object, **kwargs: object) -> None:
            raise AssertionError("received decision must not be requested twice")

        monkeypatch.setattr(restored.decider, "begin", deny)
        try:
            assert restored.progress(1, 0)
            assert restored.decider.state.requests_started == 1
            assert len(restored.application.attempts) == 5
            assert restored.replay_recorded()["live_inputs_equal"] is True
        finally:
            restored.close()
    finally:
        session.close()


def test_actual_body_schema_privacy_and_live_guard() -> None:
    normal = LifeSession(LifeConfig())
    hidden = LifeSession(LifeConfig(employer_money=0, flour=0, closure_tick=2))
    try:
        a = normal.application.game_for("ada").observe()
        b = hidden.application.game_for("ada").observe()
        request = life_request(a)
        provider = offline_provider(response())
        provider.generate(request)
        body = provider.transport.bodies[0]
        assert body == life_body(life_request(b))
        assert "WORK" in str(body["text"]) and "WORK" not in str(decision_schema())
        assert set(body) == {"model", "input", "store", "text", "max_output_tokens", "reasoning"}
        assert "known_blocked" not in str(body) and "request_limit" not in str(body)
        assert "previous_response_id" not in body and "metadata" not in body
        with pytest.raises(PermissionError):
            offline_provider(response(), kind="live")
    finally:
        normal.close()
        hidden.close()


@pytest.mark.parametrize(
    "changes,error,count",
    [
        ({"max_input_bytes": 1}, "INPUT_TOO_LARGE", 0),
        ({"max_output_bytes": 1}, "OUTPUT_TOO_LARGE", 1),
    ],
)
def test_size_limits(changes: dict[str, Any], error: str, count: int) -> None:
    session = MixedLifeSession(LifeConfig(horizon=24), replace(LifeLLMConfig(), **changes))
    try:
        with pytest.raises(ValueError, match="PAUSED_ERROR"):
            session.run_until(1)
        assert session.decider.state.calls[0].error == error
        assert session.decider.state.requests_started == count
        assert not session.application.attempts
    finally:
        session.close()


def test_parser_binds_authority_from_observation() -> None:
    session = LifeSession(LifeConfig())
    try:
        obs = session.application.game_for("ada").observe()
        candidate, request = life_candidate(response("WORK", {"job_id": "bake"}).text, obs)
        assert set(candidate) == {"action_type", "payload"}
        assert request.actor_id == "ada" and request.run_id == obs.run_id
        assert (
            request.submitted_at == obs.simulation_time
            and request.based_on_observation_id == obs.observation_id
        )
    finally:
        session.close()


def test_manual_offline_ui_gate_and_safe_received_save(tmp_path: Path) -> None:
    session = MixedLifeSession(LifeConfig(horizon=24), LifeLLMConfig(hold_responses=True))
    live = LiveLife(session, tmp_path / "ready.json")
    try:
        live.command({"command": "step"}, 0)
        assert live.view()["status"] == "WAITING_FOR_DECISION"
        live.command({"command": "pause"}, 0)
        live.command({"command": "release_response"}, 0)
        ready(session)
        assert live.view()["status"] == "DECISION_READY_PAUSED"
        live.command({"command": "save"}, 0)
        live.command({"command": "step"}, 0)
        assert session.kernel.simulation_time == 1
        live.command({"command": "restore"}, 0)
        assert live.session.kernel.simulation_time == 0
        live.command({"command": "step"}, 0)
        assert live.session.kernel.simulation_time == 1
        assert isinstance(live.session, MixedLifeSession)
        assert live.session.decider.state.requests_started == 1
    finally:
        live.session.close()


def test_recorded_raw_and_receipt_tampering_rejected() -> None:
    from journeymap.adapters.llm import Record

    session = MixedLifeSession(
        LifeConfig(horizon=24), LifeLLMConfig(responses=(RawModelResponse("bad JSON"),))
    )
    try:
        with pytest.raises(ValueError):
            session.run_until(1)
        assert session.replay_recorded()["provider_recalled"] is False
        session.decider.state.calls[0] = replace(
            session.decider.state.calls[0],
            raw=Record.capture({"text": response().text, "metadata": {}}),
        )
        with pytest.raises(ValueError, match="classification"):
            session.replay_recorded()
    finally:
        session.close()
