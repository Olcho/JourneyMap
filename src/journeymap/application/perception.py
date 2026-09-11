"""Trusted World Truth filtering, before any Observation contributor runs."""

from collections.abc import Callable

from journeymap.core.canonical import JsonObject, JsonValue, clone_json_object
from journeymap.core.entities import get_entity
from journeymap.core.observations import PerceptionContext
from journeymap.modules.knowledge.projection import KnowledgeReader
from journeymap.modules.movement.perception import perceive_position

type PerceptionExtension = Callable[[JsonObject, str], JsonObject]


def perceive(
    *,
    run_id: str,
    actor_id: str,
    simulation_time: int,
    world: JsonObject,
    knowledge: KnowledgeReader,
    extension: PerceptionExtension | None = None,
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
    perceived: JsonObject = {
        "self": entity.to_json(),
        "movement": perceive_position(world, actor_id),
    }
    if extension is not None:
        extra = clone_json_object(extension(clone_json_object(world), actor_id))
        if perceived.keys() & extra.keys():
            raise ValueError("perception extension cannot replace existing scopes")
        perceived.update(extra)
    return PerceptionContext(
        run_id,
        actor_id,
        simulation_time,
        perceived,
        {"records": known},
    )
