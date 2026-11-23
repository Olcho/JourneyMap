"""Minimal trusted spatial filtering: only the actor's own current position."""

from journeymap.core.canonical import JsonObject, clone_json_object
from journeymap.core.observations import PerceptionContext


def perceive_position(state: JsonObject, actor_id: str) -> JsonObject:
    runtime = state.get("_concurrent")
    absent = runtime.get("absent") if isinstance(runtime, dict) else None
    if isinstance(absent, dict) and actor_id in absent:
        return {}
    movement = state.get("movement")
    if not isinstance(movement, dict):
        return {}
    positions = movement.get("positions")
    if not isinstance(positions, dict) or actor_id not in positions:
        return {}
    position = positions[actor_id]
    if not isinstance(position, dict) or position.get("actor_id") != actor_id:
        raise ValueError("invalid self position")
    location_id = position.get("location_id")
    locations = movement.get("locations")
    if not isinstance(location_id, str) or not location_id or not isinstance(locations, dict):
        raise ValueError("invalid self location")
    location = locations.get(location_id)
    if not isinstance(location, dict) or location.get("location_id") != location_id:
        raise ValueError("invalid self location")
    # Reconstruct allowlisted scalar fields; never copy a module record wholesale.
    return {"location_id": location_id}


def perceive_movement(state: JsonObject, actor_id: str) -> JsonObject:
    runtime = state.get("_concurrent")
    absent = runtime.get("absent") if isinstance(runtime, dict) else None
    transit = absent.get(actor_id) if isinstance(absent, dict) else None
    if isinstance(transit, dict):
        return {
            "transit": clone_json_object(
                {
                    key: transit[key]
                    for key in ("route_id", "origin", "destination", "started_at", "completes_at")
                }
            )
        }
    return perceive_position(state, actor_id)


def contribute_position(context: PerceptionContext) -> JsonObject:
    return {"position": context.perceived.get("movement", {})}
