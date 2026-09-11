"""Trusted World Truth filtering, before any Observation contributor runs."""

from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.core.entities import get_entity
from journeymap.core.observations import PerceptionContext
from journeymap.modules.knowledge import KnowledgeLedger
from journeymap.modules.movement.perception import perceive_position


def perceive(
    *,
    run_id: str,
    actor_id: str,
    simulation_time: int,
    world: JsonObject,
    knowledge: KnowledgeLedger,
) -> PerceptionContext:
    if knowledge.run_id != run_id:
        raise ValueError("knowledge belongs to another run")
    entity = get_entity(world, actor_id)
    if entity is None:
        raise ValueError("unknown observation actor")
    known: list[JsonValue] = [
        record.to_json()
        for record in knowledge.for_actor(actor_id).history()
        if record.learned_at <= simulation_time
    ]
    return PerceptionContext(
        run_id,
        actor_id,
        simulation_time,
        {"self": entity.to_json(), "movement": perceive_position(world, actor_id)},
        {"records": known},
    )
