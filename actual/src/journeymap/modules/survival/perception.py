"""Trusted survival allowlist without hidden effect configuration."""

from journeymap.core.canonical import JsonObject
from journeymap.modules.survival.models import actor_survival


def perceive_survival(state: JsonObject, actor_id: str) -> JsonObject:
    return actor_survival(state, actor_id).to_json()
