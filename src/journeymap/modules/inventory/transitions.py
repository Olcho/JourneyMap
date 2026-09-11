"""Detached inventory candidates; callers own the single TransitionPlan commit."""

from journeymap.core.canonical import JsonObject, clone_json_object
from journeymap.core.handlers import ActionValidationError
from journeymap.modules.inventory.models import (
    item_definition,
    owned_quantities,
    positive_quantity,
)


def decrement_candidate(state: JsonObject, owner: str, item_id: str, quantity: int) -> JsonObject:
    quantity = positive_quantity(quantity)
    item_definition(state, item_id)
    owned = owned_quantities(state, owner)
    if owned.get(item_id, 0) < quantity:
        raise ActionValidationError("INSUFFICIENT_QUANTITY")
    inventory = state["inventory"]
    assert isinstance(inventory, dict)
    candidate = clone_json_object(inventory)
    owners = candidate["owners"]
    assert isinstance(owners, dict)
    row = owners[owner]
    assert isinstance(row, dict)
    row[item_id] = owned[item_id] - quantity
    return candidate


def transfer_candidate(
    state: JsonObject, source: str, target: str, item_id: str, quantity: int
) -> JsonObject:
    positive_quantity(quantity)
    if source == target:
        raise ActionValidationError("SELF_TRANSFER")
    target_owned = owned_quantities(state, target)
    candidate = decrement_candidate(state, source, item_id, quantity)
    owners = candidate["owners"]
    assert isinstance(owners, dict)
    row = owners[target]
    assert isinstance(row, dict)
    row[item_id] = target_owned.get(item_id, 0) + quantity
    return candidate
