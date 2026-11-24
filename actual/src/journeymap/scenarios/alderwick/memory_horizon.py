"""Distinct Places v1 author inputs and current-location-only perception."""

from typing import cast

from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.core.observations import PerceptionContext
from journeymap.core.scheduler import ScenarioSchedule
from journeymap.modules.movement.models import ActorPosition
from journeymap.modules.movement.perception import perceive_position
from journeymap.scenarios.alderwick.fixture import initial_world

SCENARIO_VERSION = "memory-horizon-distinct-places-1"
TARGETS = ("inn", "bakery", "well")
SQUARE = "village-square"


def memory_horizon_world() -> JsonObject:
    world = initial_world()
    movement = cast(JsonObject, world["movement"])
    positions = cast(JsonObject, movement["positions"])
    positions["stranger"] = ActorPosition("stranger", SQUARE).to_json()
    return world


def memory_horizon_schedule() -> ScenarioSchedule:
    return ScenarioSchedule("alderwick", SCENARIO_VERSION, ())


def perceive_local(world: JsonObject, actor_id: str) -> JsonObject:
    location = perceive_position(world, actor_id).get("location_id")
    movement = cast(JsonObject, world["movement"])
    routes = cast(JsonObject, movement["routes"])
    exits: list[JsonValue] = [
        {key: route[key] for key in ("route_id", "destination", "traversal_cost")}
        for _, route in sorted(routes.items())
        if isinstance(route, dict) and route.get("origin") == location
    ]
    return {"local": {"exits": exits}}


def contribute_local(context: PerceptionContext) -> JsonObject:
    return {"local": context.perceived["local"]}
