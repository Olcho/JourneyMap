"""Versioned, deterministic author inputs; no runtime autonomous NPC behavior."""

from journeymap.core.canonical import JsonObject
from journeymap.core.entities import Entity, entity_state
from journeymap.core.scheduler import ScenarioSchedule, ScheduledEventSpec
from journeymap.modules.knowledge.records import KnowledgeRecord
from journeymap.modules.movement.models import ActorPosition, Location, Route, movement_state

LOCATIONS = (
    "west-gate",
    "village-square",
    "inn",
    "bakery",
    "well",
    "smithy",
    "east-road",
    "east-bridge",
)
ACTOR_LOCATIONS = (
    ("stranger", "west-gate"),
    ("marta", "inn"),
    ("edwin", "bakery"),
    ("hugh", "east-road"),
    ("thomas", "village-square"),
)


def initial_world() -> JsonObject:
    edges = (
        ("west-gate", "village-square"),
        ("village-square", "inn"),
        ("village-square", "bakery"),
        ("village-square", "well"),
        ("village-square", "smithy"),
        ("village-square", "east-road"),
        ("east-road", "east-bridge"),
    )
    routes = tuple(
        Route(f"{origin}-to-{destination}", origin, destination, 2)
        for a, b in edges
        for origin, destination in ((a, b), (b, a))
    )
    return {
        "entities": entity_state(Entity(actor, "person") for actor, _ in ACTOR_LOCATIONS),
        "movement": movement_state(
            locations=map(Location, LOCATIONS),
            routes=routes,
            positions=(ActorPosition(actor, location) for actor, location in ACTOR_LOCATIONS),
        ),
        "alderwick": {"east_bridge": {"bridge_id": "east-bridge", "condition": "intact"}},
    }


def scenario_schedule(collapse_tick: int = 3) -> ScenarioSchedule:
    return ScenarioSchedule(
        "alderwick",
        "1",
        (
            ScheduledEventSpec(
                collapse_tick, 0, "CollapseEastBridge", 1, {"bridge_id": "east-bridge"}
            ),
        ),
    )


def initial_knowledge(run_id: str) -> tuple[KnowledgeRecord, ...]:
    """Explicit travel instructions; no passability, condition or future timing."""
    return tuple(
        KnowledgeRecord(
            f"{run_id}:initial:travel:{index}",
            run_id,
            "stranger",
            origin,
            "travel_route",
            {"route_id": f"{origin}-to-{destination}", "destination": destination},
            "INITIAL",
            "alderwick:1:travel-instructions",
            0,
        )
        for index, (origin, destination) in enumerate(
            (
                ("west-gate", "village-square"),
                ("village-square", "east-road"),
                ("east-road", "east-bridge"),
            ),
            start=1,
        )
    )
