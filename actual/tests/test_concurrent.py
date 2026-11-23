"""Real-engine concurrency, authority, counterexamples and replay gates."""

import socket
from dataclasses import replace
from typing import cast

import pytest
from test_alderwick import obj, section
from test_resources import ReplaceState

from journeymap.adapters.scripted import ScriptedController
from journeymap.application.concurrent import (
    AutonomousRunner,
    ConcurrentApplication,
    ConcurrentGamePort,
)
from journeymap.bootstrap import create_alderwick_application, create_alderwick_kernel
from journeymap.bootstrap_phase1 import create_phase1_application, create_phase1_kernel
from journeymap.core.canonical import JsonObject, canonical_json, state_digest
from journeymap.core.concurrent import ConcurrentKernel
from journeymap.core.concurrent_replay import replay_concurrent
from journeymap.core.controller import GameSubmissionError
from journeymap.core.events import EventDeliveryError, EventDraft, EventEnvelope
from journeymap.core.handlers import (
    ActionRequest,
    ActionStatus,
    ResolutionContext,
    TransitionPlan,
    ValidationContext,
)
from journeymap.core.observations import Observation
from journeymap.core.scheduler import ScheduledEventSpec
from journeymap.examples.concurrent import intent, scene
from journeymap.modules.inventory.models import owned_quantities
from journeymap.modules.movement.perception import perceive_position
from journeymap.modules.trade.models import wallet_balance
from journeymap.scenarios.alderwick.experiment import perceive_experiment
from journeymap.scenarios.alderwick.fixture import scenario_schedule


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network forbidden")

    monkeypatch.setattr(socket, "socket", forbidden)


def move(game: ConcurrentGamePort, route: str = "a-route") -> ActionRequest:
    return intent(game, "MOVE", {"route_id": route})


def test_two_intervals_overlap_and_next_event_and_snapshot_detachment() -> None:
    kernel = create_phase1_kernel()
    kernel.boot()
    try:
        app = create_phase1_application(kernel)
        a, b = app.game_for("A"), app.game_for("B")
        ar, br = move(a), move(b, "b-route")
        assert a.submit(ar).status == b.submit(br).status == "ACCEPTED"
        initial_results = kernel.action_results
        assert kernel.simulation_time == 0 and initial_results == ()
        kernel.advance_to(1)
        assert len(kernel.in_flight) == 2
        assert section(a.observe(), "movement")["position"] == {
            "transit": {
                "route_id": "a-route",
                "origin": "square",
                "destination": "a-end",
                "started_at": 0,
                "completes_at": 2,
            }
        }
        assert perceive_position(kernel.perception_snapshot, "A") == {}
        flight = kernel.in_flight[0]
        flight.request.payload.clear()
        assert kernel.in_flight[0].request.payload == {"route_id": "a-route"}
        assert kernel.advance_next() == 2
        assert perceive_position(kernel.state_snapshot, "A") == {"location_id": "a-end"}
        assert perceive_position(kernel.state_snapshot, "B") == {"location_id": "b-end"}
        intervals: list[tuple[int, int]] = [
            (r.started_at, r.resolved_at) for r in kernel.action_results
        ]
        assert intervals == [(0, 2), (0, 2)]
        assert kernel.advance_next() is None
        assert a.result(ar.action_request_id).status == "SUCCEEDED"  # type: ignore[union-attr]
        assert b.result(ar.action_request_id) is None
        before = kernel.execution_snapshot
        assert a.submit(ar).status == "ACCEPTED"  # retry returns original admission
        kernel.advance_to(2)
        assert kernel.execution_snapshot == before
        assert state_digest(kernel.state_snapshot) == kernel.state_digest
    finally:
        kernel.close()


@pytest.mark.parametrize("tick", [1, 2])
def test_route_closes_before_completion_a_fails_b_arrives(tick: int) -> None:
    data = scene(closure=tick)
    results = data["results"]
    assert isinstance(results, list)
    assert [(r["status"], r["started_at"], r["resolved_at"]) for r in results] == [
        ("FAILED", 0, 2),
        ("SUCCEEDED", 0, 2),
    ]
    assert results[0]["reason_code"] == "ROUTE_IMPASSABLE"
    assert results[0]["transition"] is None
    world = cast(JsonObject, data["world"])
    assert perceive_position(world, "A") == {"location_id": "square"}
    assert perceive_position(world, "B") == {"location_id": "b-end"}
    assert owned_quantities(world, "A") == {"bread": 0}
    journal = data["journal"]
    assert isinstance(journal, list)
    assert [r["kind"] for r in journal] == [
        "accepted",
        "accepted",
        "environment",
        "completed",
        "completed",
    ]


def test_busy_rejection_retry_conflict_and_authority_do_not_mutate_or_delay_peer() -> None:
    kernel = create_phase1_kernel()
    kernel.boot()
    try:
        app = create_phase1_application(kernel)
        a, b = app.game_for("A"), app.game_for("B")
        original = move(a)
        a.submit(original)
        b.submit(move(b, "b-route"))
        domain = kernel.state_snapshot
        assert a.submit(intent(a, "WAIT", {"duration": 1})).reason_code == "ACTION_REJECTED"
        assert kernel.action_results[-1].reason_code == "ACTOR_BUSY"
        assert kernel.simulation_time == 0 and kernel.state_snapshot == domain
        assert (
            a.submit(replace(original, payload={"route_id": "b-route"})).reason_code
            == "REQUEST_ID_CONFLICT"
        )
        assert b.submit(original).reason_code == "REQUEST_ID_CONFLICT"
        forged = replace(move(a), actor_id="B")
        assert a.submit(forged).reason_code == "WRONG_ACTOR"
        kernel.advance_to(2)
        assert [r.status for r in kernel.action_results] == [
            ActionStatus.REJECTED,
            ActionStatus.SUCCEEDED,
            ActionStatus.SUCCEEDED,
        ]
        assert kernel.simulation_time == 2
        with pytest.raises(ValueError):
            kernel.advance_to(1)
        with pytest.raises(RuntimeError, match="requires accept_action"):
            kernel.submit_action(original)
    finally:
        kernel.close()


def test_stock_race_conserves_and_loser_is_not_charged() -> None:
    data = scene(buy=True)
    results = data["results"]
    assert isinstance(results, list)
    assert [r["status"] for r in results] == ["SUCCEEDED", "FAILED"]
    world = cast(JsonObject, data["world"])
    assert [owned_quantities(world, a)["bread"] for a in ("A", "B", "seller")] == [1, 0, 0]
    assert [wallet_balance(world, a) for a in ("A", "B", "seller")] == [8, 10, 2]


def test_repeated_tie_rotation_reversing_input_order_does_not_starve_either_buyer() -> None:
    kernel = create_phase1_kernel()
    kernel.boot()
    try:
        kernel._system_event_registry.register("Replace", 1, ReplaceState())
        app = create_phase1_application(kernel)
        winners = []
        for round_no in range(6):
            if round_no:
                inventory = obj(kernel.state_snapshot["inventory"])
                obj(inventory["owners"])["seller"] = {"bread": 1}
                kernel.schedule(
                    ScheduledEventSpec(round_no, 0, "Replace", 1, {"inventory": inventory})
                )
                kernel.advance_to(round_no)
            for actor in ("B", "A"):
                game = app.game_for(actor)
                assert (
                    game.submit(
                        intent(game, "BUY", {"offer_id": "bread-offer", "quantity": 1})
                    ).status
                    == "ACCEPTED"
                )
            kernel.advance_to(round_no + 1)
            success = [r for r in kernel.action_results[-2:] if r.status == ActionStatus.SUCCEEDED]
            assert len(success) == 1
            winners.append(
                next(
                    c.request.actor_id
                    for c in kernel.commands
                    if c.request and c.request.action_request_id == success[0].action_request_id
                )
            )
        assert winners == ["A", "B", "A", "B", "A", "B"]
        assert [wallet_balance(kernel.state_snapshot, a) for a in ("A", "B", "seller")] == [
            4,
            4,
            12,
        ]
    finally:
        kernel.close()


@pytest.mark.parametrize("field", ["stock", "wallet", "quote"])
def test_completion_revalidates_changed_resources_atomically(field: str) -> None:
    kernel = create_phase1_kernel()
    kernel.boot()
    try:
        kernel._system_event_registry.register("Replace", 1, ReplaceState())
        app = create_phase1_application(kernel)
        game = app.game_for("A")
        request = intent(game, "BUY", {"offer_id": "bread-offer", "quantity": 1})
        game.submit(request)
        changed = kernel.state_snapshot
        if field == "stock":
            obj(obj(changed["inventory"])["owners"])["seller"] = {"bread": 0}
        elif field == "wallet":
            obj(obj(changed["trade"])["wallets"])["A"] = 1
        else:
            obj(obj(obj(changed["trade"])["offers"])["bread-offer"])["unit_price"] = 3
        key = "inventory" if field == "stock" else "trade"
        kernel.schedule(ScheduledEventSpec(1, 0, "Replace", 1, {key: changed[key]}))
        kernel.advance_to(1)
        assert kernel.action_results[-1].status == ActionStatus.FAILED
        assert kernel.state_snapshot == changed
        assert game.result(request.action_request_id).status == "FAILED"  # type: ignore[union-attr]
    finally:
        kernel.close()


@pytest.mark.parametrize("action", ["BUY", "ASK", "REQUEST"])
@pytest.mark.parametrize("depart_after_start", [False, True])
def test_moving_seller_or_social_target_is_unavailable(
    action: str, depart_after_start: bool
) -> None:
    kernel = create_phase1_kernel()
    kernel.boot()
    try:
        app = create_phase1_application(kernel)
        a, seller = app.game_for("A"), app.game_for("seller")
        payloads: dict[str, JsonObject] = {
            "BUY": {"offer_id": "bread-offer", "quantity": 1},
            "ASK": {"target_actor_id": "seller", "subject_ref": "bridge", "predicate": "condition"},
            "REQUEST": {"target_actor_id": "seller", "request_kind": "help", "request_payload": {}},
        }
        payload = payloads[action]
        if depart_after_start:
            assert a.submit(intent(a, action, payload)).status == "ACCEPTED"
        seller.submit(move(seller))
        if not depart_after_start:
            assert a.submit(intent(a, action, payload)).status == "REJECTED"
        kernel.advance_to(1)
        assert kernel.action_results[-1].status == (
            ActionStatus.FAILED if depart_after_start else ActionStatus.REJECTED
        )
        assert owned_quantities(kernel.state_snapshot, "A") == {"bread": 0}
        assert wallet_balance(kernel.state_snapshot, "A") == 10
        assert kernel.events == ()
    finally:
        kernel.close()


def test_inform_authority_no_private_claim_or_remote_truth_leak() -> None:
    kernel = create_phase1_kernel()
    kernel.boot()
    try:
        app = create_phase1_application(kernel)
        game = app.game_for("A")
        assert (
            game.submit(
                intent(game, "INFORM", {"target_actor_id": "B", "claim_record_id": "secret"})
            ).reason_code
            == "INVALID_CLAIM_REFERENCE"
        )
        kernel.schedule(ScheduledEventSpec(1, 0, "CloseRoute", 1, {"route_id": "a-route"}))
        game.submit(move(game))
        before = section(game.observe(), "knowledge")
        kernel.advance_to(1)
        observed = game.observe()
        assert section(observed, "knowledge") == before == {"records": []}
        assert "RouteClosed" not in canonical_json(observed.content)
        assert "passable" not in canonical_json(observed.content)
    finally:
        kernel.close()


def alderwick_concurrent() -> ConcurrentKernel:
    base = create_alderwick_kernel(resources=True, social=True, experiment=True)
    return ConcurrentKernel(
        base._modules,
        base._persistence,
        base.manifest,
        base.state_snapshot,
        base._action_registry,
        base._system_event_registry,
        base._event_bus,
    )


def test_moving_actor_is_not_local_peer_or_bridge_witness_and_remote_observation_is_stable() -> (
    None
):
    kernel = alderwick_concurrent()
    kernel.boot()
    try:
        authority, research = create_alderwick_application(
            kernel, resources=True, social=True, experiment=True
        )
        app = ConcurrentApplication(kernel, authority, social=True)
        remote = app.game_for("stranger")
        remote_before = remote.observe().content
        hugh = app.game_for("hugh")
        hugh.submit(move(hugh, "east-road-to-village-square"))
        local = perceive_experiment(kernel.perception_snapshot, "thomas")
        peers = obj(local["local"])["actor_ids"]
        assert isinstance(peers, list) and "hugh" not in peers
        assert (
            obj(perceive_experiment(kernel.perception_snapshot, "hugh")["local"])["actor_ids"] == []
        )
        kernel.schedule(scenario_schedule(1).events[0])
        kernel.advance_to(1)
        assert kernel.events[0].payload["witness_actor_ids"] == []
        assert all(k.predicate != "condition" for k in research.knowledge_history("hugh"))
        assert remote.observe().content == remote_before
    finally:
        kernel.close()


class Immediate:
    handler_id = "test.immediate.v1"

    def validate(self, request: ActionRequest, context: ValidationContext) -> None:
        pass

    def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
        context.rng.next_u64()
        return TransitionPlan(events=(EventDraft("Immediate", 1, {}),))


class Rule:
    def __init__(self, action: str) -> None:
        self.action = action

    def decide(self, observation: Observation) -> ActionRequest:
        return ActionRequest(
            observation.observation_id + ":rule",
            observation.run_id,
            observation.actor_id,
            observation.observation_id,
            observation.simulation_time,
            self.action,
            1,
            {"duration": 1} if self.action == "WAIT" else {},
        )


@pytest.mark.parametrize("policy", ["WAIT", "MOVE", "NOW", "INVALID"])
def test_autonomous_reactivation_idle_failure_and_zero_time_are_bounded(policy: str) -> None:
    kernel = create_phase1_kernel()
    kernel._action_registry.register("NOW", 1, Immediate())
    kernel.boot()
    try:
        app = create_phase1_application(kernel)
        controller = ScriptedController() if policy == "MOVE" else Rule(policy)
        runner = AutonomousRunner(kernel, app, {"A": controller, "B": controller})
        runner.run_until(5)
        assert kernel.simulation_time == 5
        assert len(runner.activations) == 10
        assert [a.tick for a in runner.activations] == [t for t in range(5) for _ in range(2)]
        assert runner.run_until(5) == runner.activations
        if policy in {"WAIT", "MOVE", "NOW"}:
            assert len(kernel.action_results) == 10
        else:
            assert kernel.action_results == ()
        if policy != "NOW":
            assert replay_concurrent(create_phase1_kernel, kernel.commands).final_execution == (
                kernel.execution_snapshot
            )
    finally:
        kernel.close()


def test_replay_reexecutes_in_flight_frames_and_split_advance_equals_whole() -> None:
    states = []
    for split in (False, True):
        kernel = create_phase1_kernel()
        kernel.boot()
        try:
            app = create_phase1_application(kernel)
            kernel.schedule(ScheduledEventSpec(1, 0, "CloseRoute", 1, {"route_id": "a-route"}))
            for actor, route in (("A", "a-route"), ("B", "b-route")):
                app.game_for(actor).submit(move(app.game_for(actor), route))
            midway = None
            if split:
                kernel.advance_to(1)
                midway = kernel.execution_snapshot
            kernel.advance_to(2)
            expected = kernel.execution_snapshot
            replay = replay_concurrent(create_phase1_kernel, kernel.commands)
            assert replay.final_execution == expected
            assert len(replay.frames[1]["in_flight"]) == 1  # type: ignore[arg-type]
            assert len(replay.frames[2]["in_flight"]) == 2  # type: ignore[arg-type]
            if midway:
                assert replay.frames[3] == midway
            states.append(expected)
        finally:
            kernel.close()
    assert states[0] == states[1]


def test_pending_same_tick_environment_must_be_drained_before_admission() -> None:
    kernel = create_phase1_kernel()
    kernel.boot()
    try:
        kernel.schedule(ScheduledEventSpec(0, 0, "CloseRoute", 1, {"route_id": "a-route"}))
        app = create_phase1_application(kernel)
        game = app.game_for("A")
        with pytest.raises(GameSubmissionError, match="ENGINE_ERROR"):
            game.submit(move(game))
        assert kernel.simulation_time == 0 and kernel.action_results == ()
        kernel.advance_to(0)
        assert game.submit(move(game)).reason_code == "ROUTE_IMPASSABLE"
    finally:
        kernel.close()


def test_autonomous_patrol_and_split_runner_have_same_engine_and_activations() -> None:
    runs = []
    for split in (False, True):
        kernel = create_phase1_kernel()
        kernel.boot()
        try:
            app = create_phase1_application(kernel, patrol=True)
            runner = AutonomousRunner(
                kernel, app, {"A": ScriptedController(), "B": ScriptedController()}
            )
            if split:
                runner.run_until(3)
            runner.run_until(6)
            assert len(runner.activations) == 6
            assert [a.tick for a in runner.activations] == [0, 0, 2, 2, 4, 4]
            assert len(kernel.events) == 6
            assert all(e.event_type == "ActorMoved" for e in kernel.events)
            assert kernel.in_flight == ()
            runs.append((runner.activations, app.attempts, kernel.execution_snapshot))
        finally:
            kernel.close()
    assert runs[0] == runs[1]


@pytest.mark.parametrize("departure", ["none", "before", "after"])
def test_real_inform_claim_authority_and_transit_revalidation(departure: str) -> None:
    kernel = alderwick_concurrent()
    kernel.boot()
    try:
        authority, research = create_alderwick_application(
            kernel, resources=True, social=True, experiment=True
        )
        app = ConcurrentApplication(kernel, authority, social=True)
        kernel.schedule(scenario_schedule(1).events[0])
        kernel.advance_to(1)
        thomas, hugh = app.game_for("thomas"), app.game_for("hugh")
        thomas.submit(move(thomas, "village-square-to-east-road"))
        kernel.advance_to(3)
        claim = next(k for k in research.knowledge_history("hugh") if k.predicate == "condition")
        if departure == "before":
            thomas.submit(move(thomas, "east-road-to-village-square"))
        request = intent(
            hugh,
            "INFORM",
            {"target_actor_id": "thomas", "claim_record_id": claim.knowledge_record_id},
        )
        receipt = hugh.submit(request)
        assert receipt.status == ("REJECTED" if departure == "before" else "ACCEPTED")
        if departure == "after":
            thomas.submit(move(thomas, "east-road-to-village-square"))
        kernel.advance_to(4)
        result = hugh.result(request.action_request_id)
        assert result is not None
        assert (
            result.status
            == {"none": "SUCCEEDED", "before": "REJECTED", "after": "FAILED"}[departure]
        )
        assert sum(e.event_type == "ActorInformed" for e in kernel.events) == (
            1 if departure == "none" else 0
        )
    finally:
        kernel.close()


def test_delivery_failure_preserves_one_completion_and_original_batch_order() -> None:
    kernel = create_phase1_kernel()
    kernel.boot()
    try:

        def subscriber(event: EventEnvelope) -> None:
            if event.payload.get("actor_id") == "A":
                raise RuntimeError("delivery defect")

        kernel._event_bus.subscribe(
            event_type="ActorMoved",
            priority=0,
            module_id="test",
            subscriber_id="broken",
            subscriber=subscriber,
        )
        app = create_phase1_application(kernel)
        a, b = app.game_for("A"), app.game_for("B")
        ar = move(a)
        a.submit(ar)
        b.submit(move(b, "b-route"))
        with pytest.raises(EventDeliveryError):
            kernel.advance_to(2)
        assert len(kernel.action_results) == 1
        assert a.result(ar.action_request_id).status == "SUCCEEDED"  # type: ignore[union-attr]
        kernel.advance_to(2)
        assert len(kernel.action_results) == len(kernel.events) == 2
        assert [e.payload["actor_id"] for e in kernel.events] == ["A", "B"]
        assert kernel.execution_snapshot["priority_cursor"] == 1
    finally:
        kernel.close()


def test_resolve_defect_keeps_world_rng_and_pending_without_partial_commit() -> None:
    class Broken(Immediate):
        def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
            context.state.clear()
            context.rng.next_u64()
            raise RuntimeError("resolve defect")

    kernel = create_phase1_kernel()
    kernel._action_registry.register("BROKEN", 1, Broken())
    kernel.boot()
    try:
        app = create_phase1_application(kernel)
        game = app.game_for("A")
        request = intent(game, "BROKEN", {})
        game.submit(request)
        world, rng = kernel.state_snapshot, kernel.rng_snapshot
        with pytest.raises(RuntimeError, match="resolve defect"):
            kernel.advance_to(0)
        assert kernel.state_snapshot == world and kernel.rng_snapshot == rng
        assert kernel.events == () and kernel.action_results == ()
        assert len(kernel.in_flight) == 1
        assert game.submit(request).status == "ACCEPTED"
    finally:
        kernel.close()


def test_transactional_rng_and_journal_replay_and_subscriber_reentrancy() -> None:
    def factory() -> ConcurrentKernel:
        result = create_phase1_kernel()
        result._action_registry.register("NOW", 1, Immediate())
        return result

    kernel = factory()
    kernel.boot()
    try:
        app = create_phase1_application(kernel)
        runner = AutonomousRunner(kernel, app, {"A": Rule("NOW"), "B": Rule("NOW")})
        runner.run_until(3)
        assert kernel.rng_snapshot.draw_count == 6
        assert (
            replay_concurrent(factory, kernel.commands).final_execution == kernel.execution_snapshot
        )
        game = app.game_for("A")
        request = move(game)

        def subscriber(event: EventEnvelope) -> None:
            kernel.accept_action(request)

        kernel._event_bus.subscribe(
            event_type="ActorMoved",
            priority=0,
            module_id="test",
            subscriber_id="reentrant",
            subscriber=subscriber,
        )
        game.submit(request)
        with pytest.raises(EventDeliveryError):
            kernel.advance_to(5)
        assert kernel.action_results[-1].status == ActionStatus.SUCCEEDED
        assert kernel.in_flight == ()
    finally:
        kernel.close()


@pytest.mark.parametrize("crash", [False, True])
def test_runner_reactivates_after_expected_rejection_or_controller_exception(crash: bool) -> None:
    class Crashing:
        def decide(self, observation: Observation) -> ActionRequest:
            raise ValueError("Controller failure")

    kernel = create_phase1_kernel()
    kernel.boot()
    try:
        app = create_phase1_application(kernel)
        runner = AutonomousRunner(kernel, app, {"A": Crashing() if crash else Rule("MOVE")})
        runner.run_until(4)
        assert [a.tick for a in runner.activations] == [0, 1, 2, 3]
        if crash:
            assert kernel.action_results == ()
        else:
            assert len(kernel.action_results) == 4
            assert all(r.status == ActionStatus.REJECTED for r in kernel.action_results)
        assert kernel.simulation_time == 4
    finally:
        kernel.close()


def test_horizon_retains_real_pending_action_and_resumes_without_duplicate_activation() -> None:
    kernel = create_phase1_kernel()
    kernel.boot()
    try:
        app = create_phase1_application(kernel, patrol=True)
        runner = AutonomousRunner(
            kernel, app, {"A": ScriptedController(), "B": ScriptedController()}
        )
        runner.run_until(1)
        pending_at_one = kernel.in_flight
        assert kernel.simulation_time == 1 and len(pending_at_one) == 2
        assert len(runner.activations) == 2
        runner.run_until(2)
        assert kernel.in_flight == () and len(runner.activations) == 2
        assert len(kernel.action_results) == 2
    finally:
        kernel.close()
