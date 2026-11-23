"""Trusted own-inventory allowlist."""

from journeymap.core.canonical import JsonObject
from journeymap.modules.inventory.models import owned_quantities


def perceive_inventory(state: JsonObject, actor_id: str) -> JsonObject:
    return {item: quantity for item, quantity in owned_quantities(state, actor_id).items()}
