"""E04 counterfactual information boundary, real application prefix and time limits."""

import os
import socket
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from journeymap.adapters.llm import Record, strict_json
from journeymap.adapters.pickup_cue_live import HTTPTransport
from journeymap.adapters.provider import ProviderFailure
from journeymap.adapters.route_update import FakeProvider, Script, obj, rows, semantic_input
from journeymap.adapters.route_update_policy import first_policy, shortest_moves
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json, state_digest
from journeymap.core.events import EventSourceKind
from journeymap.core.scheduler import ScheduledEventSpec
from journeymap.experiments.pickup_cue_live_audit import read_json
from journeymap.experiments.route_update import Fixture, demo_fixtures, execute, make_plan, move
from journeymap.experiments.route_update_audit import audit_directory, check_first_inputs
from journeymap.experiments.route_update_metrics import arrived
from journeymap.experiments.route_update_session import Session
from journeymap.scenarios.route_update import (
    ACTOR,
    CLOSED,
    CONDITIONS,
    authored_map,
    project_knowledge,
)


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("offline: no network, HTTP transport, key lookup or audit Provider")


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(HTTPTransport, "__init__", forbidden)
    original = os.environ.get

    def no_key(key: str, default: str | None = None) -> str | None:
        if key == "OPENAI_API_KEY":
            raise AssertionError("no key lookup")
        return original(key, default)

    monkeypatch.setattr(os.environ, "get", no_key)


WAIT = '{"action_type":"WAIT","payload":{"duration":1}}'


def branch(position: str, condition: str, run_id: str = "test-e04") -> Session:
    session = Session(run_id)
    session.reconstruct_prefix(
        obj(strict_json(move("S-to-" + position))),
        {"record_sha256": "test-parent", "parent_run_id": "test-parent"},
    )
    session.install_branch(condition)
    return session


def decisions(session: Session, outputs: tuple[str, ...]) -> None:
    provider = FakeProvider(Script(outputs))
    for _ in outputs:
        if session.stop != "RUNNING":
            break
        session.prepare()
        session.finish(provider.generate(session.provider_request), None)


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, JsonObject]:
    directory = tmp_path_factory.mktemp("route-update") / "demo"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket, "create_connection", forbidden)
        patch.setattr(HTTPTransport, "__init__", forbidden)
        report = execute(demo_fixtures(), directory)
    assert report["status"] == "INCLUDED"
    return directory, report


def exported(directory: Path, prefix: int, condition: str | None = None) -> JsonObject:
    suffix = "" if condition is None else "-" + condition
    return obj(read_json(directory / f"p{prefix:03d}{suffix}.json")["result"])


def test_demo_denominators_and_no_fabricated_usage(demo: tuple[Path, JsonObject]) -> None:
    _, report = demo
    summary = obj(report["summary"])
    assert report["planned_initial_denominator"] == summary["initial_attempts"] == 2
    assert summary["normal_prefixes"] == 2
    assert summary["A_prefixes"] == summary["B_prefixes"] == 1
    assert summary["generated_branches"] == summary["eventual_arrivals"] == 6
    assert summary["judgeable_first_action_denominator"] == 6
    assert summary["appropriate_first_MOVE"] == 5
    assert summary["recovered_arrivals"] == 1
    assert report["fake_provider_decision_intents"] == 16
    assert report["api_usage"] is None
    assert report["usage_applicability"] == "NOT_APPLICABLE_FAKE"
    assert summary["incomplete_pairs"] == []


@pytest.mark.parametrize("prefix,position", [(1, "A"), (2, "B")])
def test_real_prefix_and_three_fresh_authorities(
    demo: tuple[Path, JsonObject], prefix: int, position: str
) -> None:
    directory, _ = demo
    parent = exported(directory, prefix)
    initial = obj(parent["prefix"])
    assert parent["prefix_calls"] == 1 and parent["prefix_replays"] == 0
    assert initial["provider_called"] is True
    assert obj(initial["receipt"])["status"] == "SUCCEEDED"
    assert obj(parent["checkpoint"])["tick"] == 2
    assert obj(parent["checkpoint"])["position"] == position
    assert len(rows(parent["event_traces"])) == 1
    initial_semantic = obj(initial["semantic_input"])
    assert initial_semantic["event_memory"] == []
    assert [r["source_kind"] for r in rows(obj(initial_semantic["observation"])["knowledge"])] == [
        "AUTHORED_INITIAL"
    ]
    raw_ids = {obj(initial["request"])["action_request_id"]}
    for condition in CONDITIONS:
        result = exported(directory, prefix, condition)
        replay = obj(result["prefix"])
        assert result["prefix_calls"] == 0 and result["prefix_replays"] == 1
        assert replay["provider_called"] is False and replay["raw_response"] is None
        assert result["checkpoint"] == parent["checkpoint"]
        assert result["run_id"] != parent["run_id"]
        request = obj(replay["request"])
        assert request["run_id"] == result["run_id"]
        assert request["action_request_id"] not in raw_ids
        raw_ids.add(request["action_request_id"])
        assert len(rows(result["steps"])) >= 2
        assert obj(rows(result["steps"])[0]["semantic_input"])["observation"] is not None
        assert len(rows(obj(rows(result["steps"])[0]["semantic_input"])["event_memory"])) == 1
    assert len(raw_ids) == 4


@pytest.mark.parametrize("position", ["A", "B"])
def test_first_full_wire_equal_and_notified_only_legal_change(position: str) -> None:
    sessions = [branch(position, c, "id-" + c) for c in CONDITIONS]
    try:
        prepared = [s.prepare() for s in sessions]
        check_first_inputs(prepared[0], prepared[1], notified=False)
        check_first_inputs(prepared[0], prepared[2], notified=True)
        a, b = obj(prepared[0]["wire"]), obj(prepared[1]["wire"])
        assert cast(str, a["canonical_utf8"]).encode() == cast(str, b["canonical_utf8"]).encode()
        assert a["bytes"] == b["bytes"] and a["sha256"] == b["sha256"]
        for step in prepared:
            body = obj(obj(step["wire"])["body"])
            assert set(body) == {"model", "input", "store", "text"}
            assert "previous_response_id" not in body
            assert "closed-notified" not in cast(str, body["input"])
            assert "closed-unannounced" not in cast(str, body["input"])
    finally:
        for session in sessions:
            session.close()


def replace_ids(value: JsonValue) -> JsonValue:
    metadata = {
        "run_id",
        "actor_id",
        "entity_id",
        "observation_id",
        "content_digest",
        "knowledge_record_id",
        "source_ref",
        "supersedes_id",
        "event_trace_id",
        "decision_opportunity_id",
        "action_request_id",
        "based_on_observation_id",
        "correlation_id",
    }
    if isinstance(value, list):
        return [replace_ids(v) for v in value]
    if isinstance(value, dict):
        return {
            k: "replaced-internal-id" if k in metadata else replace_ids(v) for k, v in value.items()
        }
    return value


@pytest.mark.parametrize("condition", CONDITIONS)
def test_nested_current_and_past_ids_are_removed(condition: str) -> None:
    session = branch("A", condition)
    try:
        raw = obj(session.prepare()["raw_input"])
        original = semantic_input(raw)
        changed = semantic_input(obj(replace_ids(raw)))
        assert canonical_json(original) == canonical_json(changed)
        assert "test-e04" not in canonical_json(original)
        assert "replaced-internal-id" not in canonical_json(changed)
        assert rows(obj(changed["observation"])["knowledge"])[0]["learned_at"] == 0
    finally:
        session.close()


@pytest.mark.parametrize(
    "location", ["root", "observation", "content", "trace", "past-content", "knowledge"]
)
def test_unknown_nested_fields_fail(location: str) -> None:
    session = branch("A", "unchanged")
    try:
        raw = obj(session.prepare()["raw_input"])
        target = raw
        if location == "observation":
            target = obj(raw["observation"])
        elif location == "content":
            target = obj(obj(raw["observation"])["content"])
        elif location in ("trace", "past-content"):
            target = rows(raw["event_memory"])[0]
            if location == "past-content":
                target = obj(obj(target["observation"])["content"])
        elif location == "knowledge":
            sections = rows(obj(obj(raw["observation"])["content"])["sections"])
            target = rows(obj(sections[-1]["content"])["records"])[0]
        target["hidden_condition"] = "closed"
        with pytest.raises(ValueError):
            semantic_input(raw)
    finally:
        session.close()


@pytest.mark.parametrize("position", ["A", "B"])
@pytest.mark.parametrize("condition", CONDITIONS)
def test_graph_truth_policy_expectations(position: str, condition: str) -> None:
    session = branch(position, condition)
    try:
        assert session.kernel.simulation_time == 3
        state = session.kernel.state_snapshot
        routes = obj(obj(state["movement"])["routes"])
        assert len(routes) == 18
        closed = condition != "unchanged"
        assert [obj(routes[r])["passable"] for r in CLOSED] == [not closed, not closed]
        semantic = obj(session.prepare()["semantic_input"])
        old = first_policy(semantic, "OLD-MAP")
        updated = first_policy(semantic, "RECEIVED-UPDATE")
        assert old["selected_route"] == ("A-to-C" if position == "A" else "B-to-D")
        assert updated["selected_route"] == (
            "A-to-D"
            if position == "A" and condition == "closed-notified"
            else old["selected_route"]
        )
        assert session.baseline == {
            "minimum_cost": 4 if position == "A" and not closed else 6,
            "optimal_first_routes": [
                "A-to-C" if position == "A" and not closed else position + "-to-D"
            ],
        }
        claims = rows(obj(semantic["observation"])["knowledge"])
        assert obj(claims[0]["claim"])["routes"] == authored_map()
        assert len(claims) == (2 if condition == "closed-notified" else 1)
        events = [
            e
            for e in session.kernel.events
            if e.event_type in ("RoutesClosed", "OfficialRouteNotice")
        ]
        assert [e.event_type for e in events] == (
            ["RoutesClosed", "OfficialRouteNotice"]
            if condition == "closed-notified"
            else ["RoutesClosed"]
            if closed
            else []
        )
        assert all(
            e.source_kind == EventSourceKind.SCHEDULED_EVENT and e.simulation_time == 3
            for e in events
        )
        assert len(session.archive.traces) == 1
        with pytest.raises((ValueError, KeyError)):
            first_policy(state, "RECEIVED-UPDATE")
    finally:
        session.close()


def test_local_observation_and_receipt_acquired_after_actual_engine_return() -> None:
    session = branch("A", "closed-unannounced")
    try:
        first = session.prepare()
        assert "DIRECT_OBSERVATION" not in canonical_json(first["semantic_input"])
        provider = FakeProvider(Script((move("A-to-C"), move("C-to-G"))))
        session.finish(provider.generate(session.provider_request), None)
        at_c = session.prepare()
        observed = obj(obj(at_c["semantic_input"])["observation"])
        assert observed["time"] == 5 and observed["position"] == "C"
        cg = next(r for r in rows(observed["local_routes"]) if r["route_id"] == "C-to-G")
        assert cg["passable"] is False
        knowledge = rows(observed["knowledge"])
        assert (
            knowledge[-1]["source_kind"] == "DIRECT_OBSERVATION"
            and knowledge[-1]["learned_at"] == 5
        )
        assert "ROUTE_IMPASSABLE" not in canonical_json(at_c["semantic_input"])
        session.finish(provider.generate(session.provider_request), None)
        after = session.prepare()
        trace = rows(obj(after["semantic_input"])["event_memory"])[-1]
        assert obj(trace["receipt"])["reason_code"] == "ROUTE_IMPASSABLE"
        assert trace["opened_at"] == trace["closed_at"] == 5
    finally:
        session.close()


def test_notice_recipient_projection_and_invalid_system_transaction() -> None:
    session = branch("A", "closed-notified")
    try:
        events = session.kernel.events
        notice = next(e for e in events if e.event_type == "OfficialRouteNotice")
        changed = tuple(
            replace(e, payload={**e.payload, "recipient_actor_id": "another-valid-actor"})
            if e is notice
            else e
            for e in events
        )
        ledger = project_knowledge(changed)
        assert [(r.actor_id, r.source_kind) for r in ledger] == [
            ("another-valid-actor", "OFFICIAL_NOTICE")
        ]
    finally:
        session.close()
    for payload, kind in (
        ({"route_ids": ["C-to-G"]}, "CLOSE_ROUTES"),
        ({"recipient_actor_id": "unknown", "route_ids": list(CLOSED)}, "OFFICIAL_NOTICE"),
        ({"recipient_actor_id": ACTOR, "route_ids": list(CLOSED)}, "OFFICIAL_NOTICE"),
    ):
        session = Session("transaction-reject")
        try:
            before = session.kernel.state_snapshot
            session.kernel.schedule(ScheduledEventSpec(3, 0, kind, 1, cast(JsonObject, payload)))
            with pytest.raises(ValueError):
                session.kernel.advance_to(3)
            assert session.kernel.state_snapshot == before
            assert not any(
                e.event_type in ("RoutesClosed", "OfficialRouteNotice")
                for e in session.kernel.events
            )
        finally:
            session.close()


def test_distinct_tie_graph_retains_all_optima() -> None:
    graph: list[JsonObject] = [
        {"route_id": route, "origin": a, "destination": b, "cost": cost, "passable": True}
        for route, a, b, cost in (
            ("z", "X", "Y", 1),
            ("a", "X", "Z", 2),
            ("yg", "Y", "G", 3),
            ("zg", "Z", "G", 2),
        )
    ]
    assert shortest_moves(graph, "X") == {"minimum_cost": 4, "optimal_first_routes": ["a", "z"]}


def test_recovery_preserves_first_failure_and_requires_engine_arrival(
    demo: tuple[Path, JsonObject],
) -> None:
    result = exported(demo[0], 1, "closed-unannounced")
    assessment = obj(result["metrics"])
    assert assessment["first_appropriate"] is False
    assert assessment["eventual_arrival"] is True and assessment["recovered_arrival"] is True
    assert assessment["decisions"] == assessment["submitted_actions"] == 4
    assert assessment["elapsed_ticks"] == assessment["completion_ticks"] == 8
    assert assessment["extra_ticks"] == 2
    assert assessment["initial_move_ticks"] == 2 and assessment["external_advance_ticks"] == 1
    steps = rows(result["steps"])
    report = obj(result["engine_report"])
    assert arrived(steps[-1], report)
    assert not arrived({**steps[-1], "engine_submitted": False}, report)
    assert not arrived(
        {**steps[-1], "receipt": {**obj(steps[-1]["receipt"]), "status": "FAILED"}}, report
    )
    moved = Record.capture(report).data
    moved["events"] = []
    assert not arrived(steps[-1], moved)


@pytest.mark.parametrize(
    "outcomes,stop,tick,submitted",
    [
        ((WAIT,) * 6, "DECISION_LIMIT", 9, 6),
        (('{"action_type":"WAIT","payload":{"duration":24}}',), "TIME_LIMIT", 27, 1),
        (('{"action_type":"WAIT","payload":{"duration":25}}',), "TIME_BUDGET_NOT_SUBMITTED", 3, 0),
        (
            ('{"action_type":"WAIT","payload":{"duration":23}}', move("A-to-C")),
            "TIME_BUDGET_NOT_SUBMITTED",
            26,
            1,
        ),
        ((move("A-to-C"), move("C-to-G"), "INTEGRITY"), "ARRIVED", 7, 2),
    ],
)
def test_limits_no_overrun_or_post_arrival_call(
    outcomes: tuple[str, ...], stop: str, tick: int, submitted: int
) -> None:
    session = branch("A", "unchanged")
    try:
        decisions(session, outcomes)
        assert session.stop == stop and session.kernel.simulation_time == tick
        assessment = obj(session.snapshot()["metrics"])
        assert assessment["submitted_actions"] == submitted
        assert len(session.archive.traces) == 1 + submitted
        assert assessment["completion_ticks"] == (4 if stop == "ARRIVED" else None)
        assert assessment["elapsed_ticks"] == tick - 3
        if stop == "TIME_BUDGET_NOT_SUBMITTED" and submitted == 0:
            assert assessment["first_appropriate"] is None
    finally:
        session.close()


def test_fixture_exhaustion_is_failure_not_synthesized_wait() -> None:
    provider = FakeProvider(Script(()))
    session = branch("B", "unchanged")
    try:
        session.prepare()
        with pytest.raises(ProviderFailure, match="PROVIDER_TRANSPORT_ERROR"):
            provider.generate(session.provider_request)
        assert not session.steps
        assert not hasattr(provider, "kernel") and not hasattr(provider, "research")
    finally:
        session.close()


@pytest.mark.parametrize(
    "limits", [{"max_decisions": 7}, {"max_ticks": 25}, {"max_decisions": 5}, {"max_ticks": 23}]
)
def test_public_protocol_has_no_added_limit_axis(limits: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        make_plan(2, **limits)


@pytest.mark.parametrize(
    "output,stop,response",
    [
        (WAIT, "PREFIX_UNSUCCESSFUL", "VALID"),
        (move("S-to-G"), "PREFIX_UNSUCCESSFUL", "VALID"),
        ("REFUSAL", "RESPONSE_FAILURE", "REFUSAL"),
        ("INCOMPLETE", "RESPONSE_FAILURE", "INCOMPLETE"),
        ("not-json", "RESPONSE_FAILURE", "MALFORMED"),
        ('{"action_type":"WAIT","payload":{"duration":true}}', "RESPONSE_FAILURE", "MALFORMED"),
        ("INFRASTRUCTURE", "INFRASTRUCTURE", "INFRASTRUCTURE"),
        ("INTEGRITY", "INTEGRITY", "INTEGRITY"),
    ],
)
def test_initial_failure_no_branch_or_retry(
    tmp_path: Path, output: str, stop: str, response: str
) -> None:
    fixture = Fixture(Script((output,)), (Script(("INTEGRITY",)),) * 3)
    directory = tmp_path / "initial"
    report = execute((fixture,), directory)
    assert report["status"] == "INCLUDED"
    assert report["fake_provider_decision_intents"] == 1
    assert obj(report["summary"])["generated_branches"] == 0
    initial = rows(report["prefixes"])[0]
    assert initial["status"] == stop and initial["initial_response_status"] == response
    assert len(list(directory.glob("p*.json"))) == 2  # plan and one initial export


def test_provider_free_audit_is_read_only(
    demo: tuple[Path, JsonObject], monkeypatch: pytest.MonkeyPatch
) -> None:
    directory, report = demo
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    monkeypatch.setattr(FakeProvider, "generate", forbidden)
    actual = audit_directory(
        directory, expected_plan_sha256=cast(str, read_json(directory / "plan.json")["plan_sha256"])
    )
    assert actual == report
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}


def test_engine_completion_failure_consumes_time_and_can_continue() -> None:
    # Unit lifecycle fixture, not an extra experimental condition: close during MOVE.
    session = branch("A", "unchanged")
    try:
        decisions(session, (move("A-to-C"),))
        session.kernel.schedule(
            ScheduledEventSpec(6, 0, "CLOSE_ROUTES", 1, {"route_ids": list(CLOSED)})
        )
        decisions(session, (move("C-to-G"),))
        assert session.stop == "RUNNING" and session.position == "C"
        assert session.kernel.simulation_time == 7
        assert session.steps[-1]["engine_status"] == "FAILED"
        assert len(session.archive.traces) == 3
        decisions(session, (move("C-to-D"), move("D-to-G")))
        assert session.stop == "ARRIVED"
        assert obj(session.snapshot()["metrics"])["first_appropriate"] is True
    finally:
        session.close()


def test_valid_other_recipient_notice_does_not_reach_traveller() -> None:
    from journeymap.bootstrap import create_application, create_kernel
    from journeymap.core.entities import Entity, entity_state
    from journeymap.core.handlers import ActionRegistry, SystemEventRegistry
    from journeymap.core.run import RunManifest
    from journeymap.modules.knowledge import KnowledgeModule
    from journeymap.modules.movement import MovementModule
    from journeymap.scenarios.route_update import (
        CloseHandler,
        NoticeHandler,
        RouteUpdateModule,
        initial_knowledge,
        world,
    )

    state = world()
    state["entities"] = entity_state((Entity(ACTOR, "actor"), Entity("other", "actor")))
    obj(obj(state["movement"])["positions"])["other"] = {"actor_id": "other", "location_id": "B"}
    systems = SystemEventRegistry()
    systems.register("CLOSE_ROUTES", 1, CloseHandler())
    systems.register("OFFICIAL_NOTICE", 1, NoticeHandler())
    kernel = create_kernel(
        modules=(MovementModule(), KnowledgeModule(), RouteUpdateModule()),
        initial_state=state,
        manifest=RunManifest(
            "recipient", "route-update", "recipient-unit", "0.1.0", 1, 42, 0, state_digest(state)
        ),
        action_registry=ActionRegistry(),
        system_event_registry=systems,
    )
    try:
        kernel.boot()
        app, _ = create_application(
            kernel,
            initial_knowledge=initial_knowledge("recipient"),
            knowledge_projector=project_knowledge,
        )
        kernel.schedule(ScheduledEventSpec(3, 0, "CLOSE_ROUTES", 1, {"route_ids": list(CLOSED)}))
        kernel.schedule(
            ScheduledEventSpec(
                3,
                1,
                "OFFICIAL_NOTICE",
                1,
                {"recipient_actor_id": "other", "route_ids": list(CLOSED)},
            )
        )
        kernel.advance_to(3)
        traveller = app.game_for(ACTOR).observe()
        other = app.game_for("other").observe()
        from journeymap.adapters.llm import observation_json

        def sources(observation: JsonObject) -> list[JsonValue]:
            sections = rows(obj(observation["content"])["sections"])
            records = rows(obj(sections[-1]["content"])["records"])
            return [r["source_kind"] for r in records]

        assert sources(observation_json(traveller)) == ["AUTHORED_INITIAL"]
        assert sources(observation_json(other)) == ["OFFICIAL_NOTICE"]
    finally:
        kernel.close()


def test_future_evidence_and_unsupported_schema_fail_projection() -> None:
    session = branch("A", "unchanged")
    try:
        raw = obj(session.prepare()["raw_input"])
        sections = rows(obj(obj(raw["observation"])["content"])["sections"])
        knowledge = rows(obj(sections[-1]["content"])["records"])[0]
        knowledge["learned_at"] = 4
        with pytest.raises(ValueError, match="future"):
            semantic_input(raw)
        knowledge["learned_at"] = 0
        rows(raw["event_memory"])[0]["schema_version"] = 2
        with pytest.raises(ValueError):
            semantic_input(raw)
    finally:
        session.close()
