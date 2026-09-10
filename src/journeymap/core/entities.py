"""Run-local entity identity only; domain state belongs to modules."""

from collections.abc import Iterable
from dataclasses import dataclass

from journeymap.core.canonical import JsonObject


@dataclass(frozen=True, slots=True)
class Entity:
    entity_id: str
    entity_type: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value for value in (self.entity_id, self.entity_type)
        ):
            raise ValueError("entity identity and type must be non-empty strings")

    def to_json(self) -> JsonObject:
        return {"entity_id": self.entity_id, "entity_type": self.entity_type}


def entity_state(entities: Iterable[Entity]) -> JsonObject:
    """Build the initial entities value, rejecting ambiguous identities."""

    result: JsonObject = {}
    for entity in entities:
        if entity.entity_id in result:
            raise ValueError(f"duplicate entity_id: {entity.entity_id}")
        result[entity.entity_id] = entity.to_json()
    return result


def get_entity(state: JsonObject, entity_id: str) -> Entity | None:
    entities = state.get("entities")
    if not isinstance(entities, dict) or entity_id not in entities:
        return None
    record = entities[entity_id]
    if not isinstance(record, dict) or record.get("entity_id") != entity_id:
        raise ValueError("invalid entity record")
    entity_type = record.get("entity_type")
    if not isinstance(entity_type, str):
        raise ValueError("invalid entity type")
    return Entity(entity_id, entity_type)
