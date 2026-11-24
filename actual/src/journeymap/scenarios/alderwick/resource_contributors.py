"""Format only trusted actor-scoped resource facts."""

from journeymap.core.canonical import JsonObject
from journeymap.core.observations import PerceptionContext


def contribute_inventory(context: PerceptionContext) -> JsonObject:
    return {"inventory": context.perceived.get("inventory", {})}


def contribute_survival(context: PerceptionContext) -> JsonObject:
    return {"survival": context.perceived.get("survival", {})}


def contribute_trade(context: PerceptionContext) -> JsonObject:
    return {"trade": context.perceived.get("trade", {})}
