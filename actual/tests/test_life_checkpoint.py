"""Actual direct restore, fresh-process continuation and local live controls."""

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from journeymap.core.actions import WaitHandler
from journeymap.core.concurrent import ConcurrentKernel
from journeymap.core.scheduler import DeterministicScheduler
from journeymap.experiments.checkpoint import checkpoint_digest, load_checkpoint, save_checkpoint
from journeymap.experiments.life import LifeSession
from journeymap.experiments.live import LiveLife
from journeymap.scenarios.life import ACTORS, LifeConfig


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network forbidden")

    monkeypatch.setattr("socket.socket", forbidden)
    monkeypatch.setattr("socket.getaddrinfo", forbidden)


@pytest.mark.parametrize("tick,kind", [(2, "MOVE"), (8, "WORK"), (30, "REST")])
def test_fresh_process_checkpoint(tmp_path: Path, tick: int, kind: str) -> None:
    continuous = LifeSession(LifeConfig())
    interrupted = LifeSession(LifeConfig())
    source, destination = tmp_path / "saved.json", tmp_path / "continued.json"
    try:
        interrupted.run_until(tick)
        assert kind in {f.request.action_type for f in interrupted.kernel.in_flight}
        save_checkpoint(interrupted, source)
        interrupted.kernel.close()  # Original process state is no longer live.
        code = """
import runpy,sys
def deny(event,args):
    if event.startswith('socket.'):
        raise RuntimeError('offline resume: network denied')
sys.addaudithook(deny)
runpy.run_module('journeymap.examples.life_checkpoint',run_name='__main__')
"""
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                "--resume",
                str(source),
                "--until",
                "168",
                "--save",
                str(destination),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        continuous.run_until(168)
        restored = load_checkpoint(destination)
        try:
            assert json.loads(child.stdout)["tick"] == 168
            assert checkpoint_digest(restored) == checkpoint_digest(continuous)
            assert restored.kernel.execution_snapshot == continuous.kernel.execution_snapshot
            assert restored.inputs == continuous.inputs
            assert restored.decisions == continuous.decisions
            assert restored.report() == continuous.report()
            ids = [e.event_id for e in restored.kernel.events]
            assert len(ids) == len(set(ids))
        finally:
            restored.kernel.close()
    finally:
        continuous.kernel.close()
        interrupted.kernel.close()


@pytest.mark.parametrize(
    "changes,tick",
    [({}, 73), ({"closure_tick": 2}, 4), ({"flour": 0}, 20), ({"employer_money": 0}, 20)],
)
def test_policy_queue_rng_and_recording_continuation(
    tmp_path: Path,
    changes: dict[str, int],
    tick: int,
) -> None:
    original = LifeSession(replace(LifeConfig(), **changes))
    path = tmp_path / "state.json"
    try:
        original.run_until(tick)
        original.kernel._run.rng.next_u64()
        save_checkpoint(original, path)
        restored = load_checkpoint(path)
        try:
            assert checkpoint_digest(original) == checkpoint_digest(restored)
            assert original.kernel._run.rng.next_u64() == restored.kernel._run.rng.next_u64()
            for actor in ACTORS:
                assert (
                    original.controllers[actor].policy_state
                    == restored.controllers[actor].policy_state
                )
            original.run_until(168)
            restored.run_until(168)
            assert checkpoint_digest(original) == checkpoint_digest(restored)
        finally:
            restored.kernel.close()
    finally:
        original.kernel.close()


def test_restore_calls_no_replay_prepare_resolve_or_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = LifeSession(LifeConfig())
    path = tmp_path / "checkpoint.json"
    session.run_until(8)
    save_checkpoint(session, path)
    expected = checkpoint_digest(session)
    session.kernel.close()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("restore must hydrate, not execute inputs")

    monkeypatch.setattr(LifeSession, "run_until", forbidden)
    monkeypatch.setattr(WaitHandler, "prepare", forbidden)
    monkeypatch.setattr(WaitHandler, "resolve", forbidden)
    monkeypatch.setattr(ConcurrentKernel, "accept_action", forbidden)
    monkeypatch.setattr(ConcurrentKernel, "advance_to", forbidden)
    monkeypatch.setattr(DeterministicScheduler, "schedule", forbidden)
    restored = load_checkpoint(path)
    try:
        assert checkpoint_digest(restored) == expected
    finally:
        restored.kernel.close()


def test_request_retries_after_restore_have_no_duplicate_effect(tmp_path: Path) -> None:
    session = LifeSession(LifeConfig())
    path = tmp_path / "checkpoint.json"
    try:
        session.run_until(8)
        save_checkpoint(session, path)
        restored = load_checkpoint(path)
        try:
            for target in (session, restored):
                request = target.kernel.in_flight[0].request
                game = target.application.game_for(request.actor_id)
                state = target.kernel.execution_snapshot
                first = game.submit(request)
                assert game.submit(request) == first
                assert target.kernel.execution_snapshot == state
                assert (
                    game.submit(replace(request, payload={"bad": True})).reason_code
                    == "REQUEST_ID_CONFLICT"
                )
                target.run_until(20)
                state = target.kernel.execution_snapshot
                assert game.submit(request) == first
                result = game.result(request.action_request_id)
                assert result is not None and result.status == "SUCCEEDED"
                assert target.kernel.execution_snapshot == state
            assert checkpoint_digest(session) == checkpoint_digest(restored)
        finally:
            restored.kernel.close()
    finally:
        session.kernel.close()


def test_speed_and_view_polling_preserve_entire_stream(tmp_path: Path) -> None:
    slow = LiveLife(LifeSession(LifeConfig()), tmp_path / "slow.json")
    fast = LiveLife(LifeSession(LifeConfig()), tmp_path / "fast.json")
    direct = LifeSession(LifeConfig())
    try:
        fast.command({"command": "speed", "value": 16}, 0)
        for live in (slow, fast):
            live.command({"command": "start"}, 0)
            for tick in range(1, 41):
                live.pump(tick / live.speed + 0.001 * tick)
                before = checkpoint_digest(live.session)
                for actor in (None, *ACTORS):
                    live.view(actor)
                assert checkpoint_digest(live.session) == before
            live.command({"command": "pause"}, 1000)
            live.pump(2000)
            assert live.session.kernel.simulation_time == 40
        direct.run_until(40)
        assert (
            checkpoint_digest(slow.session)
            == checkpoint_digest(fast.session)
            == checkpoint_digest(direct)
        )
        slow.command({"command": "save"}, 2000)
        slow.command({"command": "step"}, 2000)
        assert slow.session.kernel.simulation_time == 41
        slow.command({"command": "restore"}, 2000)
        assert slow.session.kernel.simulation_time == 40
        assert not slow.running
    finally:
        slow.session.kernel.close()
        fast.session.kernel.close()
        direct.kernel.close()


def test_horizon_is_terminal_even_after_restore(tmp_path: Path) -> None:
    live = LiveLife(LifeSession(LifeConfig(horizon=2)), tmp_path / "end.json")
    try:
        live.command({"command": "step"}, 0)
        live.command({"command": "step"}, 0)
        assert live.view()["status"] == "HORIZON"
        assert live.session.kernel.in_flight
        live.command({"command": "save"}, 0)
        live.command({"command": "restore"}, 0)
        before = checkpoint_digest(live.session)
        live.command({"command": "start"}, 0)
        live.command({"command": "step"}, 0)
        live.pump(100)
        assert checkpoint_digest(live.session) == before
        assert live.session.kernel.simulation_time == 2
    finally:
        live.session.kernel.close()


def test_actor_inspection_has_no_remote_truth(tmp_path: Path) -> None:
    from journeymap.scenarios.life import initial_world

    normal = LiveLife(LifeSession(LifeConfig()), tmp_path / "n.json")
    remote = LiveLife(LifeSession(LifeConfig()), tmp_path / "r.json")
    old = remote.session.kernel
    world: Any = initial_world(LifeConfig())
    world["trade"]["wallets"]["employer"] = 0
    world["inventory"]["owners"]["employer"]["flour"] = 0
    world["movement"]["routes"]["square-shop"]["passable"] = False
    # Trusted test counterexample: swap only remote domain truth, preserving
    # the same application authority and actor-owned initial knowledge.
    old._state = world
    try:
        assert normal.view("ada") == remote.view("ada")
        view = normal.view("ada")
        assert not {"events", "people", "movement", "counts"} & view.keys()
        assert normal.view() != remote.view()
    finally:
        normal.session.kernel.close()
        remote.session.kernel.close()


@pytest.mark.parametrize(
    "damage", ["hash", "format", "source", "actor", "cursor", "fields", "type"]
)
def test_corrupted_or_incompatible_checkpoint_rejected(tmp_path: Path, damage: str) -> None:
    from journeymap.core.canonical import canonical_json

    session = LifeSession(LifeConfig())
    path = tmp_path / "checkpoint.json"
    session.run_until(8)
    save_checkpoint(session, path)
    before = checkpoint_digest(session)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if damage == "hash":
        doc["sha256"] = "0" * 64
    elif damage == "format":
        doc["format"] = "future-999"
    elif damage == "source":
        doc["payload"]["compatibility"]["source_sha256"] = "0" * 64
    elif damage == "actor":
        del doc["payload"]["policies"]["ada"]
    elif damage == "cursor":
        doc["payload"]["objects"]["run"]["_event_sequence"] += 1
    elif damage == "fields":
        doc["payload"]["objects"]["kernel"]["arbitrary"] = "unknown"
    elif damage == "type":
        doc["payload"]["config"]["fields"]["horizon"] = True
    if damage != "hash":
        doc["sha256"] = hashlib.sha256(canonical_json(doc["payload"]).encode()).hexdigest()
    path.write_text(json.dumps(doc), encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="invalid LifeSession checkpoint"):
            load_checkpoint(path)
        assert checkpoint_digest(session) == before
    finally:
        session.kernel.close()


def test_atomic_save_failure_preserves_previous_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = LifeSession(LifeConfig())
    path = tmp_path / "checkpoint.json"
    try:
        session.run_until(8)
        save_checkpoint(session, path)
        original = path.read_bytes()

        def fail(*args: object) -> None:
            raise OSError("simulated disk failure")

        monkeypatch.setattr("journeymap.experiments.checkpoint.os.replace", fail)
        with pytest.raises(OSError):
            save_checkpoint(session, path)
        assert path.read_bytes() == original
        assert list(tmp_path.iterdir()) == [path]
    finally:
        session.kernel.close()
