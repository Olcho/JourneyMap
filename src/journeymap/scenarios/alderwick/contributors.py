"""Format already filtered bridge information; no World Truth dependency."""

from journeymap.core.canonical import JsonObject
from journeymap.core.observations import PerceptionContext


def contribute_bridge(context: PerceptionContext) -> JsonObject:
    return {"bridge": context.perceived.get("bridge", {})}
