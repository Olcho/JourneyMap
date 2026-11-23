"""Movement-owned validated candidates for cross-module transitions."""

from journeymap.core.canonical import JsonObject, clone_json_object
from journeymap.modules.movement.models import Route


def close_routes(state: JsonObject, route_ids: tuple[str, ...]) -> JsonObject:
    """Return a detached movement value; caller commits it in one TransitionPlan.

    Validate every affected route before returning. Preserve unrelated records
    and extension fields. This helper has no kernel or mutation capability.
    """
    if not route_ids or len(set(route_ids)) != len(route_ids):
        raise ValueError("closure requires distinct route IDs")
    movement = state.get("movement")
    if not isinstance(movement, dict):
        raise ValueError("missing movement")
    candidate = clone_json_object(movement)
    routes, locations = candidate.get("routes"), candidate.get("locations")
    if not isinstance(routes, dict) or not isinstance(locations, dict):
        raise ValueError("invalid movement tables")
    for route_id in sorted(route_ids):
        row = routes.get(route_id)
        if not isinstance(row, dict) or row.get("route_id") != route_id:
            raise ValueError("invalid closure route")
        origin, destination = row.get("origin"), row.get("destination")
        cost, passable = row.get("traversal_cost"), row.get("passable")
        if (
            not isinstance(origin, str)
            or not isinstance(destination, str)
            or not isinstance(cost, int)
            or not isinstance(passable, bool)
        ):
            raise ValueError("invalid closure route fields")
        Route(route_id, origin, destination, cost, passable)
        for endpoint in (origin, destination):
            location = locations.get(endpoint)
            if not isinstance(location, dict) or location.get("location_id") != endpoint:
                raise ValueError("invalid closure endpoint")
        row["passable"] = False
    return candidate
