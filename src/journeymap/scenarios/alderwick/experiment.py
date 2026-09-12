"""M8 opt-in local affordances, filtered before Observation contributors."""

from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.core.observations import PerceptionContext
from journeymap.core.scheduler import ScenarioSchedule, ScheduledEventSpec
from journeymap.modules.movement.perception import perceive_position
from journeymap.scenarios.alderwick.fixture import scenario_schedule
from journeymap.scenarios.alderwick.resources import perceive_resources

EXPERIMENT_SCENARIO_VERSION = "social-resources-24h-1"


def experiment_schedule() -> ScenarioSchedule:
    """M8 finite input; M6 resources-1 and its 1..20 horizon are untouched."""
    return ScenarioSchedule(
        "alderwick",
        EXPERIMENT_SCENARIO_VERSION,
        (
            *scenario_schedule().events,
            *(ScheduledEventSpec(tick, 10, "SurvivalTick", 1, {}) for tick in range(1, 25)),
        ),
    )


def perceive_experiment(world: JsonObject, actor_id: str) -> JsonObject:
    location = perceive_position(world, actor_id).get("location_id")
    movement = world["movement"]
    assert isinstance(movement, dict)
    positions, routes = movement["positions"], movement["routes"]
    assert isinstance(positions, dict) and isinstance(routes, dict)
    actors: list[JsonValue] = [
        identity
        for identity, position in sorted(positions.items())
        if isinstance(position, dict)
        and position.get("location_id") == location
        and identity != actor_id
    ]
    exits: list[JsonValue] = [
        {key: route[key] for key in ("route_id", "destination", "traversal_cost")}
        for _, route in sorted(routes.items())
        if isinstance(route, dict) and route.get("origin") == location
    ]
    return {**perceive_resources(world, actor_id), "local": {"actor_ids": actors, "exits": exits}}


def contribute_local(context: PerceptionContext) -> JsonObject:
    return {"local": context.perceived["local"]}
