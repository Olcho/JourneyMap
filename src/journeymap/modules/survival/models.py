"""Survival owns pressure and the one linear hunger-recovery contract."""

from collections.abc import Mapping
from dataclasses import dataclass

from journeymap.core.canonical import JsonObject, clone_json_object
from journeymap.core.entities import get_entity
from journeymap.core.handlers import ActionValidationError
from journeymap.modules.inventory.models import item_definition

HUNGER_PER_TICK = 2
FATIGUE_PER_TICK = 1
REST_RECOVERY_PER_TICK = 3


@dataclass(frozen=True, slots=True)
class SurvivalState:
    hunger: int
    fatigue: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or not 0 <= value <= 100 for value in (self.hunger, self.fatigue)
        ):
            raise ValueError("survival pressure must be an integer in 0..100")

    def to_json(self) -> JsonObject:
        return {"hunger": self.hunger, "fatigue": self.fatigue}


def survival_state(
    actors: Mapping[str, SurvivalState], consumables: Mapping[str, int]
) -> JsonObject:
    if any(not isinstance(actor, str) or not actor for actor in actors):
        raise ValueError("invalid survival owner")
    if any(
        not isinstance(item, str) or not item or type(effect) is not int or effect <= 0
        for item, effect in consumables.items()
    ):
        raise ValueError("invalid hunger recovery")
    return {
        "actors": {actor: value.to_json() for actor, value in actors.items()},
        "consumables": dict(consumables),
    }


def actor_survival(state: JsonObject, actor: str) -> SurvivalState:
    survival = state.get("survival")
    actors = survival.get("actors") if isinstance(survival, dict) else None
    if not isinstance(actors, dict):
        raise ActionValidationError("INVALID_SURVIVAL_STATE")
    if actor not in actors:
        raise ActionValidationError("MISSING_SURVIVAL_STATE")
    row = actors[actor]
    if not isinstance(row, dict):
        raise ActionValidationError("INVALID_SURVIVAL_STATE")
    hunger, fatigue = row.get("hunger"), row.get("fatigue")
    if not isinstance(hunger, int) or not isinstance(fatigue, int):
        raise ActionValidationError("INVALID_SURVIVAL_STATE")
    try:
        if get_entity(state, actor) is None:
            raise ValueError("unknown survival owner")
        return SurvivalState(hunger, fatigue)
    except ValueError as error:
        raise ActionValidationError("INVALID_SURVIVAL_STATE") from error


def hunger_recovery(state: JsonObject, item_id: str) -> int:
    item_definition(state, item_id)
    survival = state.get("survival")
    effects = survival.get("consumables") if isinstance(survival, dict) else None
    if not isinstance(effects, dict):
        raise ActionValidationError("INVALID_SURVIVAL_STATE")
    if item_id not in effects:
        raise ActionValidationError("ITEM_NOT_CONSUMABLE")
    effect = effects[item_id]
    if not isinstance(effect, int) or isinstance(effect, bool) or effect <= 0:
        raise ActionValidationError("INVALID_SURVIVAL_STATE")
    return effect


def survival_candidate(state: JsonObject, actor: str, value: SurvivalState) -> JsonObject:
    actor_survival(state, actor)
    survival = state["survival"]
    assert isinstance(survival, dict)
    candidate = clone_json_object(survival)
    actors = candidate["actors"]
    assert isinstance(actors, dict)
    row = actors[actor]
    assert isinstance(row, dict)
    row.update(value.to_json())
    return candidate
