"""Inventory-owned validation and explicit initial state construction."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from journeymap.core.canonical import JsonObject
from journeymap.core.entities import get_entity
from journeymap.core.handlers import ActionValidationError


@dataclass(frozen=True, slots=True)
class ItemDefinition:
    item_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, str) or not self.item_id:
            raise ValueError("item_id must be a non-empty string")

    def to_json(self) -> JsonObject:
        return {"item_id": self.item_id}


def inventory_state(
    items: Iterable[ItemDefinition], owners: Mapping[str, Mapping[str, int]]
) -> JsonObject:
    definitions: JsonObject = {}
    quantities: JsonObject = {}
    for item in items:
        if item.item_id in definitions:
            raise ValueError("duplicate item identity")
        definitions[item.item_id] = item.to_json()
    for owner, entries in owners.items():
        if not isinstance(owner, str) or not owner:
            raise ValueError("invalid inventory owner")
        row: JsonObject = {}
        for item_id, quantity in entries.items():
            if item_id not in definitions or type(quantity) is not int or quantity < 0:
                raise ValueError("invalid inventory entry")
            row[item_id] = quantity
        quantities[owner] = row
    return {"items": definitions, "owners": quantities}


def item_definition(state: JsonObject, item_id: str) -> ItemDefinition:
    inventory = state.get("inventory")
    items = inventory.get("items") if isinstance(inventory, dict) else None
    if not isinstance(items, dict):
        raise ActionValidationError("INVALID_INVENTORY_STATE")
    if item_id not in items:
        raise ActionValidationError("UNKNOWN_ITEM")
    row = items[item_id]
    if not isinstance(row, dict) or row.get("item_id") != item_id or not item_id:
        raise ActionValidationError("INVALID_INVENTORY_STATE")
    return ItemDefinition(item_id)


def owned_quantities(state: JsonObject, owner: str) -> dict[str, int]:
    try:
        entity = get_entity(state, owner)
    except ValueError as error:
        raise ActionValidationError("INVALID_INVENTORY_STATE") from error
    if entity is None:
        raise ActionValidationError("UNKNOWN_INVENTORY_OWNER")
    inventory = state.get("inventory")
    owners = inventory.get("owners") if isinstance(inventory, dict) else None
    if not isinstance(owners, dict):
        raise ActionValidationError("INVALID_INVENTORY_STATE")
    if owner not in owners:
        raise ActionValidationError("UNKNOWN_INVENTORY_OWNER")
    row = owners[owner]
    if not isinstance(row, dict):
        raise ActionValidationError("INVALID_INVENTORY_STATE")
    result: dict[str, int] = {}
    for item_id, quantity in sorted(row.items()):
        item_definition(state, item_id)
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
            raise ActionValidationError("INVALID_INVENTORY_STATE")
        result[item_id] = quantity
    return result


def positive_quantity(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ActionValidationError("INVALID_QUANTITY")
    return value
