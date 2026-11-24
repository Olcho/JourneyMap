"""Desktop import, canonical replay and isolated offline failure contracts."""

import json
import os
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from journeymap_memoryreplica.paired import (
    OfflineReplies,
    move,
    observe,
    prepare,
    run_pair,
)
from journeymap_memoryreplica.probe import source_fingerprint, validate_imports
from journeymap_memoryreplica.projection import project_context


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("no network or credential lookup")

    previous = os.environ.get

    def get(key: str, default: str | None = None) -> str | None:
        if key == "OPENAI_API_KEY":
            forbidden()
        return previous(key, default)

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(os.environ, "get", get)
    yield


def test_current_import_tree_is_required(tmp_path: Path) -> None:
    validate_imports(Path(__file__).resolve().parents[1])
    with pytest.raises(ValueError, match="source/import mismatch"):
        validate_imports(tmp_path)


def test_current_fingerprint_needs_no_historical_manifest(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/example.py").write_text("value = 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    before = source_fingerprint(tmp_path)
    assert set(before) == {"src/example.py", "pyproject.toml"}
    (tmp_path / "src/example.py").write_text("value = 2\n")
    assert before != source_fingerprint(tmp_path)


def test_authored_map_is_removed_from_every_memory_condition() -> None:
    session = prepare("unchanged", "memoryreplica-no-map")
    try:
        _, semantic, _ = observe(session, "full")
        for policy in ("none", "recent", "full"):
            context = project_context(semantic, policy)
            observations = [context["observation"]] + [
                trace["observation"] for trace in context["event_memory"]
            ]
            for observation in observations:
                assert set(observation) == {"time", "position", "local_routes", "received_updates"}
                assert all(
                    update["claim"]["kind"] != "initial-map"
                    for update in observation["received_updates"]
                )
    finally:
        session.close()


def pair() -> dict[str, Any]:
    return run_pair(
        "unchanged",
        "full",
        OfflineReplies(('{"facts":[]}',)),
        OfflineReplies((move("A-to-S"), move("S-to-B"))),
        goal="B",
    )


def test_action_stream_and_events_are_deterministic() -> None:
    assert pair() == pair()


def test_full_canonical_starting_world_is_common() -> None:
    results = [
        run_pair(
            "unchanged",
            policy,
            OfflineReplies(('{"facts":[]}',)),
            OfflineReplies((move("A-to-S"), move("S-to-B"))),
            goal="B",
        )
        for policy in ("none", "recent", "full")
    ]
    assert len({result["baseline"]["world_state_digest"] for result in results}) == 1
    assert len({result["baseline"]["full_experience_digest"] for result in results}) == 1


def test_engine_rejection_is_recorded_separately_and_recovery_can_continue() -> None:
    result = run_pair(
        "closed-unannounced",
        "recent",
        OfflineReplies(('{"facts":[]}',)),
        OfflineReplies(tuple(move(route) for route in ("A-to-C", "C-to-G", "C-to-D", "D-to-G"))),
    )
    action = result["action"]
    assert action["stop"] == "ARRIVED"
    assert action["elapsed_ticks"] == 8
    rejected = action["steps"][1]
    assert rejected["parser_status"] == "VALID"
    assert rejected["engine"]["receipt"]["status"] == "REJECTED"
    assert rejected["engine"]["receipt"]["reason_code"] == "ROUTE_IMPASSABLE"
    context = json.loads(action["steps"][2]["request_text"])["context"]
    assert context["event_memory"][-1]["receipt"]["status"] == "REJECTED"


def test_nonarrival_and_exhausted_fixture_are_separate() -> None:
    limited = run_pair(
        "unchanged",
        "none",
        OfflineReplies(('{"facts":[]}',)),
        OfflineReplies((move("A-to-S"),)),
        max_decisions=1,
        goal="B",
    )
    exhausted = run_pair(
        "unchanged", "none", OfflineReplies(('{"facts":[]}',)), OfflineReplies(()), goal="B"
    )
    assert limited["action"]["stop"] == "DECISION_LIMIT"
    assert limited["action"]["goal_reached"] is False
    assert exhausted["action"]["stop"] == "FIXTURE_EXHAUSTED"
    assert exhausted["action"]["steps"][0]["engine"] is None


def test_fixture_outputs_must_be_inert_strings() -> None:
    with pytest.raises(TypeError, match="immutable tuple of strings"):
        OfflineReplies((1,))  # type: ignore[arg-type]
