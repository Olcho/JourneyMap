"""Immutable scenario-owned pickup notice, perceived only at Well."""

from typing import cast

from journeymap.core.canonical import JsonObject
from journeymap.core.observations import PerceptionContext
from journeymap.core.scheduler import ScenarioSchedule
from journeymap.modules.movement.perception import perceive_position
from journeymap.scenarios.alderwick.memory_horizon import memory_horizon_world, perceive_local

SCENARIO_VERSION = "pickup-cue-diagnostic-1"
TARGETS = ("inn", "bakery")


def pickup_world(target: str) -> JsonObject:
    if type(target) is not str or target not in TARGETS:
        raise ValueError("pickup target must be inn or bakery")
    world = memory_horizon_world()
    world["pickup_cue"] = {"pickup_location": target}
    return world


def pickup_schedule() -> ScenarioSchedule:
    return ScenarioSchedule("alderwick", SCENARIO_VERSION, ())


def perceive_pickup(world: JsonObject, actor_id: str) -> JsonObject:
    return {
        **perceive_local(world, actor_id),
        "pickup_notice": (
            {"pickup_location": cast(JsonObject, world["pickup_cue"])["pickup_location"]}
            if perceive_position(world, actor_id).get("location_id") == "well"
            else None
        ),
    }


def contribute_pickup(context: PerceptionContext) -> JsonObject:
    return {"pickup_notice": context.perceived["pickup_notice"]}


def prefix_intents(d: int) -> tuple[JsonObject, ...]:
    if type(d) is not int or d not in (1, 2, 3, 4):
        raise ValueError("cue distance must be 1..4")
    intents: list[JsonObject] = [
        {"action_type": "WAIT", "payload": {"duration": 1}} for _ in range(4 - d)
    ]
    intents.extend(
        {"action_type": "MOVE", "payload": {"route_id": route}}
        for route in ("village-square-to-well", "well-to-village-square")
    )
    intents.extend({"action_type": "WAIT", "payload": {"duration": 1}} for _ in range(d - 1))
    return tuple(intents)
