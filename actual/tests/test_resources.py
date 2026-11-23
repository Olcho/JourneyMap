"""M6 validation, ownership, conservation and latest-state timing contracts."""

from dataclasses import replace

import pytest
from test_alderwick import execution, obj

from journeymap.bootstrap import create_kernel
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json, clone_json_object
from journeymap.core.events import EventDraft
from journeymap.core.handlers import (
    ActionHandler,
    ActionRegistry,
    ActionRequest,
    ActionStatus,
    ActionValidationError,
    ResolutionContext,
    SystemEventRegistry,
    TransitionPlan,
    ValidationContext,
)
from journeymap.core.kernel import SimulationKernel
from journeymap.core.scheduler import ScheduledEvent, ScheduledEventSpec
from journeymap.modules.inventory.models import (
    ItemDefinition,
    inventory_state,
    item_definition,
    owned_quantities,
)
from journeymap.modules.inventory.transitions import decrement_candidate, transfer_candidate
from journeymap.modules.survival import SurvivalModule
from journeymap.modules.survival.models import SurvivalState, actor_survival, survival_state
from journeymap.modules.trade.handlers import BuyHandler
from journeymap.modules.trade.models import Offer, Wallet, payment_candidate, trade_state
from journeymap.scenarios.alderwick.resources import resource_world


def local_world() -> JsonObject:
    state = resource_world()
    obj(obj(obj(state["movement"])["positions"])["stranger"])["location_id"] = "bakery"
    obj(obj(state["inventory"])["owners"])["stranger"] = {"bread": 2}
    return state


def payload(action: str) -> JsonObject:
    data: dict[str, JsonObject] = {
        "BUY": {"offer_id": "edwin-bread", "quantity": 1},
        "CONSUME": {"item_id": "bread", "quantity": 1},
        "REST": {"duration": 1},
    }
    return data[action]


def request(action: str, data: JsonObject | None = None) -> ActionRequest:
    return ActionRequest(
        "resource-1",
        "run-default",
        "stranger",
        "opaque",
        0,
        action,
        1,
        payload(action) if data is None else data,
    )


class ReplaceState:
    """Test-only validated fixture replacement to simulate concurrent world changes."""

    handler_id = "test.replace.v1"

    def validate(self, event: ScheduledEvent, context: ValidationContext) -> None:
        clone_json_object(event.payload)

    def resolve(self, event: ScheduledEvent, context: ResolutionContext) -> TransitionPlan:
        context.rng.next_u64()
        return TransitionPlan(set_values=event.payload, events=(EventDraft("TestChanged", 1, {}),))


def kernel_for(
    state: JsonObject | None = None,
    *,
    buy: ActionHandler | None = None,
    consume: ActionHandler | None = None,
    boot: bool = True,
) -> SimulationKernel:
    from journeymap.modules.survival.handlers import ConsumeHandler, RestHandler

    actions, systems = ActionRegistry(), SystemEventRegistry()
    actions.register("BUY", 1, buy or BuyHandler())
    actions.register("CONSUME", 1, consume or ConsumeHandler())
    actions.register("REST", 1, RestHandler())
    SurvivalModule().register_system_events(systems)
    systems.register("TestReplace", 1, ReplaceState())
    kernel = create_kernel(
        initial_state=local_world() if state is None else state,
        action_registry=actions,
        system_event_registry=systems,
    )
    if boot:
        kernel.boot()
    return kernel


def tick(kernel: SimulationKernel, at: int = 1) -> None:
    kernel.schedule(ScheduledEventSpec(at, 0, "SurvivalTick", 1, {}))


def change(state: JsonObject, path: str, value: JsonValue) -> None:
    parts = path.split("/")
    row = state
    for part in parts[:-1]:
        row = obj(row[part])
    if value == "DELETE":
        del row[parts[-1]]
    else:
        row[parts[-1]] = value


@pytest.mark.parametrize("bad", [-1, True, False, 1.5, "1", None])
def test_inventory_and_wallet_reject_non_integer_or_negative_values(bad: JsonValue) -> None:
    with pytest.raises(ValueError):
        inventory_state((ItemDefinition("bread"),), {"a": {"bread": bad}})  # type: ignore[dict-item]
    with pytest.raises(ValueError):
        Wallet("a", bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [-1, 101, True, False, 1.5, "1", None])
@pytest.mark.parametrize("field", ["hunger", "fatigue"])
def test_survival_pressure_is_strictly_bounded(field: str, bad: JsonValue) -> None:
    with pytest.raises(ValueError):
        SurvivalState(**{field: bad, "fatigue" if field == "hunger" else "hunger": 0})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field,bad",
    [
        ("offer_id", ""),
        ("seller_id", ""),
        ("item_id", ""),
        ("unit_price", -1),
        ("unit_price", True),
        ("unit_price", 1.5),
        ("active", 1),
        ("active", "true"),
    ],
)
def test_offer_definition_validation(field: str, bad: JsonValue) -> None:
    data = Offer("offer", "seller", "bread", 2).to_json()
    data[field] = bad
    with pytest.raises(ValueError):
        Offer(**data)  # type: ignore[arg-type]


def test_initial_builders_reject_ambiguous_or_invalid_references() -> None:
    with pytest.raises(ValueError):
        inventory_state((ItemDefinition("bread"), ItemDefinition("bread")), {})
    with pytest.raises(ValueError):
        inventory_state((), {"a": {"missing": 0}})
    with pytest.raises(ValueError):
        trade_state((Wallet("a", 0), Wallet("a", 1)), ())
    with pytest.raises(ValueError):
        trade_state((), (Offer("o", "a", "bread", 1),))
    with pytest.raises(ValueError):
        trade_state((Wallet("a", 0),), (Offer("o", "a", "bread", 1),) * 2)
    with pytest.raises(ValueError):
        survival_state({"a": SurvivalState(0, 0)}, {"bread": True})
    with pytest.raises(ValueError):
        ItemDefinition("")


@pytest.mark.parametrize("quantity", [1, 2, 5])
def test_transfer_is_detached_and_conserves_both_sides(quantity: int) -> None:
    state = local_world()
    before = canonical_json(state)
    candidate = transfer_candidate(state, "edwin", "stranger", "bread", quantity)
    assert canonical_json(state) == before
    owners = obj(candidate["owners"])
    assert owners["edwin"] == {"bread": 5 - quantity}
    assert owners["stranger"] == {"bread": 2 + quantity}
    owners.clear()
    assert canonical_json(state) == before


@pytest.mark.parametrize(
    "source,target,item,quantity,reason",
    [
        ("edwin", "stranger", "bread", 0, "INVALID_QUANTITY"),
        ("edwin", "stranger", "bread", True, "INVALID_QUANTITY"),
        ("edwin", "stranger", "bread", 6, "INSUFFICIENT_QUANTITY"),
        ("edwin", "stranger", "missing", 1, "UNKNOWN_ITEM"),
        ("edwin", "edwin", "bread", 1, "SELF_TRANSFER"),
        ("missing", "stranger", "bread", 1, "UNKNOWN_INVENTORY_OWNER"),
        ("edwin", "missing", "bread", 1, "UNKNOWN_INVENTORY_OWNER"),
    ],
)
def test_invalid_transfer_never_changes_either_side(
    source: str,
    target: str,
    item: str,
    quantity: int,
    reason: str,
) -> None:
    state = local_world()
    before = canonical_json(state)
    with pytest.raises(ActionValidationError, match=f"^{reason}$"):
        transfer_candidate(state, source, target, item, quantity)
    assert canonical_json(state) == before


def test_missing_entry_is_zero_but_missing_owner_and_invalid_row_are_errors() -> None:
    state = local_world()
    owners = obj(obj(state["inventory"])["owners"])
    owners["stranger"] = {}
    assert owned_quantities(state, "stranger") == {}
    assert obj(transfer_candidate(state, "edwin", "stranger", "bread", 1)["owners"])[
        "stranger"
    ] == {"bread": 1}
    with pytest.raises(ActionValidationError, match="INSUFFICIENT_QUANTITY"):
        decrement_candidate(state, "stranger", "bread", 1)
    del owners["stranger"]
    with pytest.raises(ActionValidationError, match="UNKNOWN_INVENTORY_OWNER"):
        owned_quantities(state, "stranger")
    owners["stranger"] = []
    with pytest.raises(ActionValidationError, match="INVALID_INVENTORY_STATE"):
        owned_quantities(state, "stranger")
    obj(obj(state["inventory"])["items"])["bread"] = {"item_id": "wrong"}
    with pytest.raises(ActionValidationError, match="INVALID_INVENTORY_STATE"):
        item_definition(state, "bread")


@pytest.mark.parametrize("action", ["BUY", "CONSUME", "REST"])
@pytest.mark.parametrize("bad", [0, -1, True, False, 1.5, "2", None])
def test_action_invalid_quantity_or_duration_rejects_without_time(
    action: str, bad: JsonValue
) -> None:
    data = payload(action)
    data["duration" if action == "REST" else "quantity"] = bad
    kernel = kernel_for()
    try:
        tick(kernel)
        before = execution(kernel)
        result = kernel.submit_action(request(action, data))
        assert result.status == ActionStatus.REJECTED
        assert result.reason_code == (
            "INVALID_DURATION" if action == "REST" else "INVALID_QUANTITY"
        )
        assert execution(kernel) == before
        assert result.transition is None and result.emitted_event_ids == ()
    finally:
        kernel.close()


FAILURES: list[tuple[str, str, JsonValue, str]] = [
    ("BUY", "trade/offers/edwin-bread", "DELETE", "UNKNOWN_OFFER"),
    ("BUY", "trade/offers/edwin-bread/active", False, "OFFER_UNAVAILABLE"),
    ("BUY", "trade/offers/edwin-bread/unit_price", True, "INVALID_OFFER"),
    ("BUY", "trade/offers/edwin-bread/seller_id", "stranger", "SELF_PURCHASE"),
    ("BUY", "entities/edwin", "DELETE", "UNKNOWN_SELLER"),
    ("BUY", "entities/edwin/entity_id", "wrong", "INVALID_SELLER"),
    ("BUY", "movement/positions/edwin/location_id", "inn", "OUT_OF_RANGE"),
    ("BUY", "movement/positions/stranger", "DELETE", "MISSING_POSITION"),
    ("BUY", "movement/positions/edwin", "DELETE", "SELLER_MISSING_POSITION"),
    ("BUY", "movement/positions/edwin/actor_id", "wrong", "SELLER_INVALID_POSITION"),
    ("BUY", "trade/wallets/stranger", 1, "INSUFFICIENT_FUNDS"),
    ("BUY", "trade/wallets/edwin", -1, "INVALID_WALLET"),
    ("BUY", "trade/wallets/stranger", "DELETE", "MISSING_WALLET"),
    ("BUY", "inventory/owners/edwin/bread", 0, "INSUFFICIENT_STOCK"),
    ("BUY", "inventory/owners/edwin/bread", -1, "INVALID_INVENTORY_STATE"),
    ("BUY", "inventory/owners/stranger/bread", True, "INVALID_INVENTORY_STATE"),
    ("CONSUME", "inventory/owners/stranger/bread", 0, "INSUFFICIENT_QUANTITY"),
    ("CONSUME", "survival/consumables/bread", "DELETE", "ITEM_NOT_CONSUMABLE"),
    ("CONSUME", "survival/consumables/bread", 0, "INVALID_SURVIVAL_STATE"),
    ("CONSUME", "survival/actors/stranger", "DELETE", "MISSING_SURVIVAL_STATE"),
    ("REST", "survival/actors/stranger/fatigue", True, "INVALID_SURVIVAL_STATE"),
    ("REST", "entities/stranger", "DELETE", "UNKNOWN_ACTOR"),
    ("REST", "entities/stranger/entity_id", "wrong", "INVALID_ENTITY"),
]


@pytest.mark.parametrize("action,path,value,reason", FAILURES)
def test_resource_start_rejection_is_atomic(
    action: str,
    path: str,
    value: JsonValue,
    reason: str,
) -> None:
    state = local_world()
    change(state, path, value)
    kernel = kernel_for(state)
    try:
        before = execution(kernel)
        result = kernel.submit_action(request(action))
        assert result.status == ActionStatus.REJECTED and result.reason_code == reason
        assert execution(kernel) == before
    finally:
        kernel.close()


@pytest.mark.parametrize(
    "action,path,value,reason",
    [
        *[case for case in FAILURES if "seller_id" not in case[1]],
        ("BUY", "trade/offers/edwin-bread/unit_price", 3, "OFFER_CHANGED"),
        ("BUY", "trade/offers/edwin-bread/seller_id", "marta", "OFFER_CHANGED"),
        ("BUY", "trade/offers/edwin-bread/item_id", "other", "OFFER_CHANGED"),
    ],
)
def test_completion_failure_preserves_independent_system_commits(
    action: str,
    path: str,
    value: JsonValue,
    reason: str,
) -> None:
    changed = local_world()
    change(changed, path, value)
    module = path.split("/")[0]
    kernel, control = kernel_for(), kernel_for()
    try:
        for target in (kernel, control):
            tick(target)
            target.schedule(ScheduledEventSpec(1, 10, "TestReplace", 1, {module: changed[module]}))
        control.advance_to(1)
        result = kernel.submit_action(request(action))
        assert result.status == ActionStatus.FAILED and result.reason_code == reason
        assert result.started_at == 0 and result.resolved_at == 1
        assert result.transition is None and result.emitted_event_ids == ()
        assert execution(kernel) == execution(control)
    finally:
        kernel.close()
        control.close()


@pytest.mark.parametrize("action", ["REST", "CONSUME", "BUY"])
def test_same_tick_system_precedes_completion_and_effect_uses_latest_state(action: str) -> None:
    kernel = kernel_for()
    try:
        tick(kernel)
        before = kernel.state_snapshot
        result = kernel.submit_action(request(action))
        assert result.status == ActionStatus.SUCCEEDED
        assert result.resolved_at == 1
        assert [event.event_type for event in kernel.events[:-1]] == ["SurvivalAdvanced"] * 5
        assert [event.payload["actor_id"] for event in kernel.events[:-1]] == sorted(
            obj(before["entities"])
        )
        assert (
            kernel.events[-1].event_type
            == {"REST": "ActorRested", "CONSUME": "ItemConsumed", "BUY": "ItemPurchased"}[action]
        )
        survival = actor_survival(kernel.state_snapshot, "stranger")
        assert (
            survival
            == {
                "REST": SurvivalState(22, 8),
                "CONSUME": SurvivalState(12, 11),
                "BUY": SurvivalState(22, 11),
            }[action]
        )
        assert kernel.events[-1].source_ref == "resource-1"
        assert kernel.events[-1].transition_id == result.transition.transition_id  # type: ignore[union-attr]
        if action == "REST":
            assert kernel.state_snapshot["inventory"] == before["inventory"]
        if action != "BUY":
            assert kernel.state_snapshot["trade"] == before["trade"]
    finally:
        kernel.close()


@pytest.mark.parametrize("quantity,price", [(1, 2), (3, 2), (5, 2), (5, 0)])
def test_buy_conserves_currency_and_item_quantity(quantity: int, price: int) -> None:
    state = local_world()
    obj(obj(obj(state["trade"])["offers"])["edwin-bread"])["unit_price"] = price
    kernel = kernel_for(state)
    try:
        result = kernel.submit_action(
            request("BUY", {"offer_id": "edwin-bread", "quantity": quantity})
        )
        assert result.status == ActionStatus.SUCCEEDED
        after = kernel.state_snapshot
        wallets = obj(obj(after["trade"])["wallets"])
        assert wallets["stranger"] == 10 - price * quantity
        assert wallets["edwin"] == price * quantity
        assert sum(int(v) for v in wallets.values() if isinstance(v, int)) == 10
        assert owned_quantities(after, "stranger")["bread"] == 2 + quantity
        assert owned_quantities(after, "edwin")["bread"] == 5 - quantity
        assert after["survival"] == state["survival"]
        assert kernel.events[-1].payload["total_price"] == price * quantity
    finally:
        kernel.close()


def test_consume_source_sink_and_rest_clamping() -> None:
    kernel = kernel_for()
    try:
        result = kernel.submit_action(request("CONSUME", {"item_id": "bread", "quantity": 2}))
        assert result.status == ActionStatus.SUCCEEDED
        assert owned_quantities(kernel.state_snapshot, "stranger")["bread"] == 0
        assert actor_survival(kernel.state_snapshot, "stranger").hunger == 0
        assert kernel.events[-1].payload == {
            "actor_id": "stranger",
            "item_id": "bread",
            "quantity": 2,
            "hunger_before": 20,
            "hunger_after": 0,
        }
        rest = replace(request("REST", {"duration": 10}), submitted_at=1)
        kernel.submit_action(rest)
        assert kernel.simulation_time == 11
        assert actor_survival(kernel.state_snapshot, "stranger") == SurvivalState(0, 0)
    finally:
        kernel.close()


def test_tick_clamps_without_actions_and_payment_second_side_failure_is_detached() -> None:
    state = local_world()
    obj(obj(state["survival"])["actors"])["stranger"] = {"hunger": 99, "fatigue": 100}
    kernel = kernel_for(state)
    try:
        tick(kernel)
        kernel.advance_to(1)
        assert actor_survival(kernel.state_snapshot, "stranger") == SurvivalState(100, 100)
        assert kernel.action_results == ()
        assert kernel.state_snapshot["inventory"] == state["inventory"]
        assert kernel.state_snapshot["trade"] == state["trade"]
    finally:
        kernel.close()
    obj(obj(state["trade"])["wallets"])["edwin"] = -1
    before = canonical_json(state)
    with pytest.raises(ActionValidationError, match="INVALID_WALLET"):
        payment_candidate(state, "stranger", "edwin", 2)
    assert canonical_json(state) == before


@pytest.mark.parametrize("action", ["BUY", "CONSUME", "REST"])
@pytest.mark.parametrize("shape", ["missing", "extra", "wrong_id"])
def test_exact_payload_contract(action: str, shape: str) -> None:
    data = payload(action)
    if shape == "missing":
        data.clear()
    elif shape == "extra":
        data["set_values"] = {"trade": {}}
    else:
        data["offer_id" if action == "BUY" else "item_id"] = []
    kernel = kernel_for()
    try:
        before = execution(kernel)
        assert kernel.submit_action(request(action, data)).reason_code == "INVALID_PAYLOAD"
        assert execution(kernel) == before
    finally:
        kernel.close()
