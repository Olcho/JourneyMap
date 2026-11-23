"""Phase 4.1: bounded failure states and poll-independent completion deadlines."""

import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from journeymap.adapters.life_llm import LifeLLMConfig
from journeymap.adapters.provider import RawModelResponse
from journeymap.experiments.checkpoint import checkpoint_digest, load_checkpoint, save_checkpoint
from journeymap.experiments.live import LiveLife
from journeymap.experiments.mixed_life import MixedLifeSession
from journeymap.scenarios.life import LifeConfig


class ManualClock:
    def __init__(self, value: float = 100.0) -> None:
        self._value = value
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._value

    def advance_to(self, value: float) -> None:
        with self._lock:
            assert value >= self._value
            self._value = value


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    original_get = os.environ.get

    def deny(*args: object, **kwargs: object) -> None:
        raise AssertionError("no real keys or network")

    def guarded_get(key: str, default: Any = None) -> Any:
        if key == "OPENAI_API_KEY":
            deny()
        return original_get(key, default)

    monkeypatch.setattr("socket.socket", deny)
    monkeypatch.setattr("socket.getaddrinfo", deny)
    monkeypatch.setattr(os.environ, "get", guarded_get)


def settle(session: MixedLifeSession) -> None:
    assert session.decider._signal.wait(1), "fixture must publish its result"


def test_deep_json_error_is_consistent_queryable_restorable_and_replayable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from journeymap.adapters.life_llm import OfflineLifeProvider

    text = "[" * 1500 + "0" + "]" * 1500
    assert len(text.encode()) == 3001
    session = MixedLifeSession(
        LifeConfig(horizon=24), LifeLLMConfig(responses=(RawModelResponse(text),))
    )
    live = LiveLife(session, tmp_path / "deep.json")
    try:
        live.command({"command": "start"}, 0)
        live.pump(1)
        settle(session)
        live.pump(2)
        state = session.decider.state
        call = state.calls[-1]
        assert state.status == "PAUSED_ERROR" and call.status == "ERROR"
        assert call.error == "JSON_DEPTH_EXCEEDED"
        assert call.raw and call.raw.data["text"] == text
        assert state.ready is None and state.observation is None
        assert session.kernel.simulation_time == 0 and not session.application.attempts
        assert state.requests_started == 1
        assert live.view()["status"] == "PAUSED_ERROR"
        live.view("ada")
        live.command({"command": "pause"}, 2)
        before = checkpoint_digest(session)
        live.command({"command": "save"}, 2)
        live.command({"command": "restore"}, 2)
        assert checkpoint_digest(live.session) == before
        assert isinstance(live.session, MixedLifeSession)
        assert not live.session.progress(24, 3)

        def deny(*args: object, **kwargs: object) -> None:
            raise AssertionError("failure replay must not call the Provider")

        monkeypatch.setattr(OfflineLifeProvider, "generate", deny)
        assert live.session.replay_recorded()["provider_recalled"] is False
        assert live.session.decider.state.requests_started == 1
        assert live.session.kernel.simulation_time == 0
    finally:
        live.session.close()


@pytest.mark.parametrize(
    "completed_at,expected", [(104.999, "READY"), (105.0, "READY"), (105.001, "PAUSED_ERROR")]
)
def test_completion_deadline_independent_of_late_owner_poll(
    tmp_path: Path,
    completed_at: float,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from journeymap.adapters.life_llm import OfflineLifeProvider

    clock = ManualClock()
    session = MixedLifeSession(
        LifeConfig(horizon=24), LifeLLMConfig(hold_responses=True), decision_clock=clock
    )
    try:
        assert not session.progress(1, 0)
        assert session.decider._started_at == 100
        frozen = session.kernel.execution_snapshot
        clock.advance_to(completed_at)
        session.decider.release_response()
        settle(session)  # No intermediate poll, no real sleep.
        clock.advance_to(110)
        session.decider.poll()
        assert session.decider._completed_at == completed_at
        assert session.decider.state.status == expected
        assert session.kernel.execution_snapshot == frozen
        assert session.decider.state.requests_started == 1
        assert not session.decider.in_flight
        if expected == "READY":
            assert session.progress(1, 0)
            assert session.kernel.simulation_time == 1
            assert session.decider.state.calls[0].status == "SUCCEEDED"
        else:
            call = session.decider.state.calls[0]
            assert call.status == "ERROR" and call.error == "TIMEOUT" and call.discarded_late
            assert call.raw is not None and call.action is None and call.receipt is None
            assert session.decider.state.ready is None and session.decider.state.observation is None
            assert not session.progress(1, 0) and not session.application.attempts
            path = tmp_path / "late.json"
            save_checkpoint(session, path)
            restored = load_checkpoint(path)
            assert isinstance(restored, MixedLifeSession)
            try:
                assert checkpoint_digest(restored) == checkpoint_digest(session)

                def deny(*args: object, **kwargs: object) -> None:
                    raise AssertionError("late response replay must not request again")

                monkeypatch.setattr(OfflineLifeProvider, "generate", deny)
                assert restored.replay_recorded()["provider_recalled"] is False
                assert not restored.progress(1, 0)
            finally:
                restored.close()
    finally:
        session.close()


def test_empty_poll_at_deadline_allows_exact_deadline_completion() -> None:
    clock = ManualClock()
    session = MixedLifeSession(
        LifeConfig(horizon=24), LifeLLMConfig(hold_responses=True), decision_clock=clock
    )
    try:
        assert not session.progress(1, 0)
        clock.advance_to(105)
        session.decider.poll()
        assert session.decider.state.status == "WAITING_FOR_DECISION"
        session.decider.release_response()
        settle(session)
        session.decider.poll()
        assert session.decider.state.status == "READY"
        assert session.progress(1, 0)
    finally:
        session.close()


def test_timeout_before_late_completion_preserves_raw_and_safe_boundary(tmp_path: Path) -> None:
    clock = ManualClock()
    session = MixedLifeSession(
        LifeConfig(horizon=24), LifeLLMConfig(hold_responses=True), decision_clock=clock
    )
    try:
        assert not session.progress(1, 0)
        clock.advance_to(105.001)
        session.decider.poll()
        assert session.decider.state.status == "PAUSED_ERROR"
        with pytest.raises(ValueError, match="external request"):
            save_checkpoint(session, tmp_path / "unsafe.json")
        clock.advance_to(106)
        session.decider.release_response()
        settle(session)
        session.decider.poll()
        call = session.decider.state.calls[-1]
        assert call.error == "TIMEOUT" and call.discarded_late and call.raw
        assert session.decider.state.ready is None and session.decider.state.observation is None
        save_checkpoint(session, tmp_path / "safe.json")
        assert session.replay_recorded()["provider_recalled"] is False
        session.decider.state.calls[-1] = replace(
            call, action=session.controllers["ada"].decide(session.observations[0])
        )
        with pytest.raises(ValueError):
            session.replay_recorded()
    finally:
        session.close()


def test_runtime_wallclock_metadata_does_not_enter_checkpoint(tmp_path: Path) -> None:
    sessions = [
        MixedLifeSession(LifeConfig(horizon=24), decision_clock=ManualClock(origin))
        for origin in (100.0, 1_000_000.0)
    ]
    try:
        for session in sessions:
            session.run_until(24)
        assert checkpoint_digest(sessions[0]) == checkpoint_digest(sessions[1])
        path = tmp_path / "no-wallclock.json"
        save_checkpoint(sessions[0], path)
        text = path.read_text(encoding="utf-8")
        assert "_started_at" not in text and "_completed_at" not in text and "_clock" not in text
    finally:
        for session in sessions:
            session.close()
