"""Phase 2 real-engine lifecycle, accounting, information and autonomous gates."""

import socket
from dataclasses import replace
from typing import cast

import pytest
from test_alderwick import obj, section
from test_resources import ReplaceState

from journeymap.adapters.life import LifeController
from journeymap.application.concurrent import ConcurrentApplication, ConcurrentGamePort
from journeymap.application.concurrent_replay import LiveInput, replay_live_inputs
from journeymap.bootstrap_life import create_life_application, create_life_kernel
from journeymap.bootstrap_phase1 import create_phase1_application, create_phase1_kernel
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.core.concurrent import ConcurrentKernel
from journeymap.core.handlers import (
    ActionRequest,
    ActionStatus,
    ActionValidationError,
    ValidationContext,
)
from journeymap.core.scheduler import ScheduledEventSpec
from journeymap.examples.concurrent import intent
from journeymap.experiments.life import LifeRun, LifeSession, run_life
from journeymap.modules.inventory.models import owned_quantities
from journeymap.modules.inventory.transitions import transform_candidate
from journeymap.modules.labor.handlers import WorkHandler
from journeymap.modules.labor.rest import HomeRestHandler
from journeymap.modules.survival.models import SurvivalState, actor_survival
from journeymap.modules.survival.schedule import needs_schedule
from journeymap.modules.trade.models import wallet_balance
from journeymap.scenarios.life import ACTORS, WORKERS, LifeConfig, initial_world


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline-only")

    monkeypatch.setattr(socket, "socket", forbidden)


def local_world(config: LifeConfig | None = None) -> JsonObject:
    world = initial_world(LifeConfig() if config is None else config)
    for actor in WORKERS:
        obj(obj(obj(world["movement"])["positions"])[actor])["location_id"] = "workshop"
    return world


def boot(
    config: LifeConfig | None = None, state: JsonObject | None = None
) -> tuple[ConcurrentKernel, ConcurrentApplication]:
    kernel = create_life_kernel(LifeConfig() if config is None else config, state=state)
    kernel.boot()
    return kernel, create_life_application(kernel)


def work(game: ConcurrentGamePort) -> ActionRequest:
    return intent(game, "WORK", {"job_id": "bake"})


def test_work_acceptance_then_atomic_conversion_wages_and_idempotency() -> None:
    kernel, app = boot(state=local_world())
    try:
        game = app.game_for("ada")
        request = work(game)
        before = kernel.state_snapshot
        assert game.submit(request).status == "ACCEPTED"
        assert kernel.simulation_time == 0 and kernel.state_snapshot == before
        assert game.submit(request).status == "ACCEPTED"
        kernel.advance_to(2)
        assert kernel.state_snapshot == before
        kernel.advance_to(3)
        final = kernel.state_snapshot
        assert owned_quantities(final, "employer") == {"flour": 159, "bread": 16}
        assert wallet_balance(final, "employer") == 230
        assert wallet_balance(final, "ada") == 14
        assert actor_survival(final, "ada") == SurvivalState(20, 14)
        assert len(kernel.events) == 1 and kernel.events[0].event_type == "WorkCompleted"
        assert game.result(request.action_request_id).status == "SUCCEEDED"  # type: ignore[union-attr]
        assert game.submit(request).status == "ACCEPTED"
        kernel.advance_to(3)
        assert kernel.state_snapshot == final and len(kernel.events) == 1
        assert app.latest_public_result("ada")["status"] == "SUCCEEDED"  # type: ignore[index]
        public = app.latest_public_result("ada")
        assert public is not None
        obj(public["payload"]).clear()
        assert app.attempts[0].request.payload == {"job_id": "bake"}
    finally:
        kernel.close()


def test_capacity_is_enforced_at_start_without_reserving_or_cloning_money() -> None:
    kernel, app = boot(state=local_world())
    try:
        receipts = [app.game_for(a).submit(work(app.game_for(a))) for a in WORKERS]
        assert [r.status for r in receipts] == ["ACCEPTED", "ACCEPTED", "REJECTED", "REJECTED"]
        assert len(kernel.in_flight) == 2
        assert all(r.reason_code == "WORKPLACE_FULL" for r in kernel.action_results)
        assert receipts[-1].reason_code == "ACTION_REJECTED"
        kernel.advance_to(3)
        assert sum(wallet_balance(kernel.state_snapshot, a) for a in ACTORS) == 256
        assert owned_quantities(kernel.state_snapshot, "employer")["flour"] == 158
        assert len([e for e in kernel.events if e.event_type == "WorkCompleted"]) == 2
    finally:
        kernel.close()


@pytest.mark.parametrize("resource", ["material", "wages"])
def test_same_last_material_or_payroll_is_applied_once_at_completion(resource: str) -> None:
    config = (
        replace(LifeConfig(), flour=1)
        if resource == "material"
        else replace(LifeConfig(), employer_money=10)
    )
    kernel, app = boot(config, local_world(config))
    try:
        for actor in ("ada", "bert"):
            assert app.game_for(actor).submit(work(app.game_for(actor))).status == "ACCEPTED"
        kernel.advance_to(3)
        assert [r.status for r in kernel.action_results] == [
            ActionStatus.SUCCEEDED,
            ActionStatus.FAILED,
        ]
        assert wallet_balance(kernel.state_snapshot, "bert") == 4
        assert wallet_balance(kernel.state_snapshot, "ada") == 14
        assert owned_quantities(kernel.state_snapshot, "employer")["bread"] == 16
        assert (
            sum(wallet_balance(kernel.state_snapshot, a) for a in ACTORS)
            == config.employer_money + 16
        )
        assert app.latest_public_result("bert")["reason_code"] == "ACTION_FAILED"  # type: ignore[index]
    finally:
        kernel.close()


@pytest.mark.parametrize(
    "condition", ["materials", "wages", "quote", "location", "hunger", "fatigue"]
)
@pytest.mark.parametrize("change_tick", [2, 3])
def test_conditions_changing_during_work_or_at_completion_have_no_partial_effect(
    condition: str, change_tick: int
) -> None:
    kernel, app = boot(state=local_world())
    try:
        kernel._system_event_registry.register("Replace", 1, ReplaceState())
        game = app.game_for("ada")
        request = work(game)
        game.submit(request)
        changed = kernel.state_snapshot
        if condition == "materials":
            obj(obj(changed["inventory"])["owners"])["employer"] = {"flour": 0, "bread": 8}
            key = "inventory"
        elif condition == "wages":
            obj(obj(changed["trade"])["wallets"])["employer"] = 0
            key = "trade"
        elif condition == "quote":
            obj(obj(obj(changed["labor"])["jobs"])["bake"])["wage"] = 11
            key = "labor"
        elif condition == "location":
            obj(obj(obj(changed["movement"])["positions"])["ada"])["location_id"] = "homes"
            key = "movement"
        else:
            obj(obj(obj(changed["survival"])["actors"])["ada"])[condition] = 100
            key = "survival"
        kernel.schedule(ScheduledEventSpec(change_tick, 0, "Replace", 1, {key: changed[key]}))
        kernel.advance_to(3)
        assert kernel.action_results[-1].status == ActionStatus.FAILED
        assert kernel.action_results[-1].resolved_at == 3
        assert kernel.state_snapshot == changed
        assert [e.event_type for e in kernel.events] == ["TestChanged"]
        assert game.result(request.action_request_id).reason_code == "ACTION_FAILED"  # type: ignore[union-attr]
    finally:
        kernel.close()


def test_inventory_conversion_validates_all_outputs_without_partial_candidate() -> None:
    world = local_world()
    before = canonical_json(world)
    with pytest.raises(ActionValidationError):
        transform_candidate(world, "employer", {"flour": 1}, {"unknown": 8})
    assert canonical_json(world) == before
    candidate = transform_candidate(world, "employer", {"flour": 1}, {"bread": 8})
    assert canonical_json(world) == before
    assert obj(obj(candidate["owners"])["employer"]) == {"bread": 16, "flour": 159}


def test_transit_excludes_work_rest_and_jobs_but_not_elapsed_needs() -> None:
    kernel, app = boot()
    try:
        for spec in needs_schedule(0, 3):
            kernel.schedule(spec)
        game = app.game_for("ada")
        game.submit(intent(game, "MOVE", {"route_id": "home-square"}))
        context = ValidationContext(kernel.manifest.run_id, 0, kernel.perception_snapshot)
        with pytest.raises(ActionValidationError, match="NOT_AT_WORKPLACE"):
            WorkHandler().validate(work(game), context)
        with pytest.raises(ActionValidationError, match="NOT_AT_HOME"):
            HomeRestHandler().validate(intent(game, "REST", {"duration": 3}), context)
        assert obj(section(game.observe(), "life")["life"])["local_jobs"] == []
        kernel.advance_to(1)
        assert actor_survival(kernel.state_snapshot, "ada") == SurvivalState(22, 11)
    finally:
        kernel.close()


def test_needs_continue_after_20_split_time_and_extra_observations_do_not_change_them() -> None:
    finals = []
    for split in (False, True):
        kernel, app = boot(LifeConfig(horizon=48))
        try:
            for spec in needs_schedule(0, 48):
                kernel.schedule(spec)
            if split:
                kernel.advance_to(20)
                before = kernel.state_snapshot
                for _ in range(10):
                    app.game_for("ada").observe()
                assert kernel.state_snapshot == before
                kernel.advance_to(21)
                assert actor_survival(kernel.state_snapshot, "ada").hunger == 62
            kernel.advance_to(48)
            assert actor_survival(kernel.state_snapshot, "ada") == SurvivalState(100, 58)
            assert len(kernel.events) == 48 * 5
            assert kernel.pending_scheduled_events == ()
            finals.append(kernel.execution_snapshot)
        finally:
            kernel.close()
    assert finals[0] == finals[1]


def test_rest_and_work_use_latest_needs_at_same_tick_without_double_time_effect() -> None:
    world = initial_world(LifeConfig())
    obj(obj(world["survival"])["actors"])["ada"] = {"hunger": 20, "fatigue": 50}
    obj(obj(obj(world["movement"])["positions"])["bert"])["location_id"] = "workshop"
    kernel, app = boot(state=world)
    try:
        for spec in needs_schedule(0, 3):
            kernel.schedule(spec)
        app.game_for("ada").submit(intent(app.game_for("ada"), "REST", {"duration": 3}))
        app.game_for("bert").submit(work(app.game_for("bert")))
        kernel.advance_to(3)
        assert actor_survival(kernel.state_snapshot, "ada") == SurvivalState(26, 44)
        assert actor_survival(kernel.state_snapshot, "bert") == SurvivalState(26, 17)
        assert [e.event_type for e in kernel.events[-7:-2]] == ["SurvivalAdvanced"] * 5
    finally:
        kernel.close()


@pytest.mark.parametrize("hidden", ["money", "stock", "closure", "malformed"])
def test_remote_private_or_undiscovered_change_does_not_change_input_or_policy(hidden: str) -> None:
    observations, decisions = [], []
    for alter in (False, True):
        world = initial_world(LifeConfig())
        if alter:
            if hidden == "money":
                obj(obj(world["trade"])["wallets"])["employer"] = 0
            elif hidden == "stock":
                obj(obj(world["inventory"])["owners"])["employer"] = {"flour": 0, "bread": 0}
            elif hidden == "closure":
                obj(obj(obj(world["movement"])["routes"])["square-shop"])["passable"] = False
            else:
                obj(obj(world["inventory"])["owners"])["employer"] = "PRIVATE"
        kernel, app = boot(state=world)
        try:
            observation = app.game_for("ada").observe()
            observations.append(observation)
            decisions.append(LifeController().decide(observation))
        finally:
            kernel.close()
    assert observations[0] == observations[1]
    assert decisions[0] == decisions[1]


@pytest.mark.parametrize("shortage", ["payroll", "material"])
def test_local_unavailable_job_changes_policy_without_disclosing_private_reason(
    shortage: str,
) -> None:
    world = local_world()
    obj(obj(world["inventory"])["owners"])["ada"] = {"flour": 0, "bread": 2}
    if shortage == "payroll":
        obj(obj(world["trade"])["wallets"])["employer"] = 0
    else:
        obj(obj(world["inventory"])["owners"])["employer"] = {"flour": 0, "bread": 8}
    kernel, app = boot(state=world)
    try:
        observation = app.game_for("ada").observe()
        controller = LifeController()
        request = controller.decide(observation)
        assert request.action_type == "WAIT"
        assert controller.decisions[-1].reason == "JOB_UNAVAILABLE_RECHECK"
        assert "employer_money" not in canonical_json(observation.content)
        receipt = app.game_for("ada").submit(work(app.game_for("ada")))
        assert receipt.status == "REJECTED" and receipt.reason_code == "ACTION_REJECTED"
    finally:
        kernel.close()


def test_live_replay_preserves_effect_free_unknown_conflict_retry_and_busy_attempts() -> None:
    kernel = create_phase1_kernel()
    kernel.boot()
    app = create_phase1_application(kernel)
    inputs: list[LiveInput] = []
    try:
        observation = app.game_for("A").observe()
        inputs.append(
            LiveInput(
                "observe",
                0,
                "A",
                observation_id=observation.observation_id,
                observation_digest=observation.content_digest,
            )
        )
        base = ActionRequest(
            "unknown", observation.run_id, "A", observation.observation_id, 0, "UNKNOWN", 1, {}
        )
        for actor, request in (
            ("A", base),
            ("A", base),
            ("B", base),
            (
                "A",
                replace(
                    base,
                    action_request_id="move",
                    action_type="MOVE",
                    payload={"route_id": "a-route"},
                ),
            ),
            (
                "A",
                replace(
                    base, action_request_id="busy", action_type="WAIT", payload={"duration": 1}
                ),
            ),
        ):
            receipt = app.game_for(actor).submit(request)
            inputs.append(LiveInput("submit", 0, actor, request=request, receipt=receipt))
        kernel.advance_to(2)
        inputs.append(LiveInput("advance", 2))

        def factory() -> tuple[ConcurrentKernel, ConcurrentApplication]:
            k = create_phase1_kernel()
            return k, create_phase1_application(k)

        replay = replay_live_inputs(factory, tuple(inputs))
        assert replay.attempts == app.attempts and len(replay.attempts) == 5
        assert replay.observations == (observation,)
        assert replay.final_execution == kernel.execution_snapshot
        assert [c.request.action_type for c in kernel.commands if c.request] == ["MOVE", "WAIT"]
    finally:
        kernel.close()


@pytest.fixture(scope="module")
def normal_run() -> LifeRun:
    return run_life(verify_replay=True)


def test_normal_7_day_is_policy_driven_repeated_production_life_and_conserves(
    normal_run: LifeRun,
) -> None:
    s = normal_run.summary
    assert s["final_tick"] == 168 and s["event_count"] == len(normal_run.events)
    accounting = obj(s["accounting"])
    assert obj(accounting["checks"]) == {"currency": True, "flour": True, "bread": True}
    assert accounting["bread_produced"] > accounting["bread_initial"]  # type: ignore[operator]
    shares = []
    for actor in WORKERS:
        row = obj(obj(s["actors"])[actor])
        successes = obj(row["successful_actions"])
        assert all(cast(int, successes[a]) >= 5 for a in ("WORK", "BUY", "CONSUME", "REST"))
        assert row["risk_ticks"] == 0 and cast(int, row["max_hunger"]) < 90
        assert row["wages_received"] == cast(int, successes["WORK"]) * 10
        shares.append(cast(int, successes["WORK"]))
    assert max(shares) - min(shares) <= 1
    assert obj(obj(s["actors"])["employer"])["risk_ticks"] == 0
    assert obj(s["reproduction"]) == {
        "engine_replay": True,
        "live_attempt_observation_replay": True,
    }
    assert all(
        d.reason and "wallet" in d.evidence and "previous_public_result" in d.evidence
        for d in normal_run.decisions
    )


def test_controller_rerun_and_split_life_session_match_normal_records(normal_run: LifeRun) -> None:
    session = LifeSession(LifeConfig())
    try:
        session.run_until(73)
        session.run_until(168)
        assert session.decisions == normal_run.decisions
        assert session.kernel.execution_snapshot == normal_run.execution
        assert session.report()["daily"] == normal_run.summary["daily"]
    finally:
        session.close()


@pytest.mark.parametrize("case", ["stockout", "no-money", "no-materials", "no-wages", "closure"])
def test_autonomous_stress_case_responses_and_honest_unresolved_risk(case: str) -> None:
    changes = {
        "stockout": {"bread": 0},
        "no-money": {"worker_money": 0},
        "no-materials": {"flour": 0},
        "no-wages": {"employer_money": 0},
        "closure": {"closure_tick": 2},
    }
    run = run_life(replace(LifeConfig(), **changes[case]))
    assert all(obj(obj(run.summary["accounting"])["checks"]).values())
    reasons = {d.reason for d in run.decisions}
    worker_counts = [obj(obj(obj(run.summary["actors"])[a])["successful_actions"]) for a in WORKERS]
    if case in {"no-money", "stockout", "closure"}:
        assert all(
            cast(int, c.get("WORK", 0)) >= 2 and cast(int, c.get("CONSUME", 0)) >= 2
            for c in worker_counts
        )
    if case == "stockout":
        assert any(r["reason_code"] == "INSUFFICIENT_STOCK" for r in run.results)
        assert "EARN_AND_PRODUCE" in reasons
    if case == "no-materials":
        assert all(c.get("WORK", 0) == 0 for c in worker_counts)
        assert any(cast(int, obj(obj(run.summary["actors"])[a])["risk_ticks"]) > 0 for a in WORKERS)
    if case == "no-wages":
        assert obj(run.summary["accounting"])["currency_initial"] == 16
        assert "JOB_UNAVAILABLE_RECHECK" in reasons or "FOOD_OR_WORK_UNAVAILABLE_RECHECK" in reasons
    if case == "closure":
        assert any(r["reason_code"] == "ROUTE_IMPASSABLE" for r in run.results)
        detours = [d for d in run.decisions if d.payload.get("route_id") == "square-detour"]
        assert detours and all(
            d.tick >= 3 and "square-shop" in cast(list[JsonValue], d.evidence["blocked_routes"])
            for d in detours
        )
        assert any(d.actor_id == "employer" and d.tick >= 2 for d in run.decisions)


def test_horizon_reports_pending_without_finishing_past_it() -> None:
    run = run_life(LifeConfig(horizon=2))
    assert run.summary["final_tick"] == 2
    pending = run.summary["pending"]
    assert isinstance(pending, list) and pending
    assert all(cast(int, obj(p)["completes_at"]) > 2 for p in pending)
    assert all(cast(int, r["resolved_at"]) <= 2 for r in run.results)


def test_life_closure_affects_one_mover_and_policy_uses_discovered_detour() -> None:
    world = initial_world(LifeConfig())
    positions = obj(obj(world["movement"])["positions"])
    obj(positions["ada"])["location_id"] = "square"
    obj(positions["bert"])["location_id"] = "detour"
    kernel, app = boot(state=world)
    try:
        kernel.schedule(ScheduledEventSpec(1, 0, "CloseRoute", 1, {"route_id": "square-shop"}))
        for actor, route in (("ada", "square-shop"), ("bert", "detour-shop")):
            game = app.game_for(actor)
            game.submit(intent(game, "MOVE", {"route_id": route}))
        kernel.advance_to(2)
        assert [r.status for r in kernel.action_results] == [
            ActionStatus.FAILED,
            ActionStatus.SUCCEEDED,
        ]
        ada = LifeController()
        request = ada.decide(app.game_for("ada").observe())
        assert request.action_type == "MOVE" and request.payload == {"route_id": "square-detour"}
        bert = LifeController().decide(app.game_for("bert").observe())
        assert bert.action_type == "BUY"
        app.game_for("bert").submit(bert)
        kernel.advance_to(3)
        assert kernel.action_results[-1].status == ActionStatus.SUCCEEDED
    finally:
        kernel.close()


def test_live_input_and_policy_evidence_are_detached_and_replay_detects_changed_observation() -> (
    None
):
    kernel, app = boot()
    try:
        observation = app.game_for("ada").observe()
        controller = LifeController()
        request = controller.decide(observation)
        original = dict(request.payload)
        entry = LiveInput("submit", 0, "ada", request=request)
        request.payload.clear()
        assert entry.request is not None and entry.request.payload == original
        evidence = controller.decisions[0].evidence
        evidence.clear()
        assert "hunger" in controller.decisions[0].evidence
        tampered = LiveInput(
            "observe",
            0,
            "ada",
            observation_id=observation.observation_id,
            observation_digest="wrong",
        )

        def factory() -> tuple[ConcurrentKernel, ConcurrentApplication]:
            k = create_life_kernel(LifeConfig())
            return k, create_life_application(k)

        with pytest.raises(ValueError, match="Observation mismatch"):
            replay_live_inputs(factory, (tampered,))
    finally:
        kernel.close()


def test_two_example_cycles_and_event_balance_fold_match_final_resources(
    normal_run: LifeRun,
) -> None:
    cycles = normal_run.summary["example_life_cycles"]
    assert isinstance(cycles, list) and len(cycles) == 2
    for cycle in cycles:
        assert isinstance(cycle, list)
        kinds = {obj(row)["kind"] for row in cycle}
        assert {"WorkCompleted", "ItemPurchased", "ItemConsumed", "ActorRested"} <= kinds
    flow = normal_run.summary["resource_flows"]
    assert isinstance(flow, list)
    last_by_actor: dict[str, JsonObject] = {}
    for row in flow:
        data = obj(row)
        last_by_actor[str(data["actor"])] = data
    final = obj(normal_run.summary["final_people"])
    for actor, row in last_by_actor.items():
        if actor == "employer":
            continue  # Other actors' later work/trade also changes the employer.
        assert row["wallet_after"] == obj(final[actor])["wallet"]
        assert row["bread_after"] == obj(obj(final[actor])["inventory"])["bread"]
    last = obj(flow[-1])
    assert last["employer_wallet_after"] == obj(final["employer"])["wallet"]
    assert last["employer_bread_after"] == obj(obj(final["employer"])["inventory"])["bread"]
    assert last["employer_flour_after"] == obj(obj(final["employer"])["inventory"])["flour"]
