"""MOVE validation and completion through the Core timed-action contract."""

from journeymap.core.actions import validate_actor
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.core.events import EventDraft
from journeymap.core.handlers import (
    ActionRequest,
    ActionTiming,
    ActionValidationError,
    ResolutionContext,
    TransitionPlan,
    ValidationContext,
)
from journeymap.modules.movement.models import ActorPosition, Route


def _object(value: JsonValue, reason: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ActionValidationError(reason)
    return value


def _text(value: JsonValue, reason: str) -> str:
    if not isinstance(value, str) or not value:
        raise ActionValidationError(reason)
    return value


def validate_move_payload(payload: JsonObject) -> str:
    if set(payload) != {"route_id"}:
        raise ActionValidationError("INVALID_PAYLOAD")
    return _text(payload["route_id"], "INVALID_PAYLOAD")


def _route(request: ActionRequest, state: JsonObject) -> Route:
    route_id = validate_move_payload(request.payload)
    movement = _object(state.get("movement"), "MISSING_POSITION")
    positions = _object(movement.get("positions"), "MISSING_POSITION")
    position = _object(positions.get(request.actor_id), "MISSING_POSITION")
    if position.get("actor_id") != request.actor_id:
        raise ActionValidationError("INVALID_POSITION")
    location_id = _text(position.get("location_id"), "INVALID_POSITION")
    routes = _object(movement.get("routes"), "UNKNOWN_ROUTE")
    record = _object(routes.get(route_id), "UNKNOWN_ROUTE")
    if record.get("route_id") != route_id:
        raise ActionValidationError("INVALID_ROUTE")
    origin = _text(record.get("origin"), "INVALID_ROUTE")
    destination = _text(record.get("destination"), "INVALID_ROUTE")
    cost = record.get("traversal_cost")
    if not isinstance(cost, int) or isinstance(cost, bool) or cost <= 0:
        raise ActionValidationError("INVALID_TRAVERSAL_COST")
    passable = record.get("passable")
    if not isinstance(passable, bool):
        raise ActionValidationError("INVALID_PASSABILITY")
    locations = _object(movement.get("locations"), "UNKNOWN_LOCATION")
    for endpoint in (location_id, origin, destination):
        location = _object(locations.get(endpoint), "UNKNOWN_LOCATION")
        if location.get("location_id") != endpoint:
            raise ActionValidationError("INVALID_LOCATION")
    if location_id != origin:
        raise ActionValidationError("WRONG_ORIGIN")
    if not passable:
        raise ActionValidationError("ROUTE_IMPASSABLE")
    return Route(route_id, origin, destination, cost, passable)


class MoveHandler:
    handler_id = "movement.move.v1"

    def validate(self, request: ActionRequest, context: ValidationContext) -> None:
        validate_actor(request, context)
        _route(request, context.state)

    def prepare(self, request: ActionRequest, context: ValidationContext) -> ActionTiming:
        route = _route(request, context.state)
        return ActionTiming(
            route.traversal_cost,
            {"origin": route.origin, "destination": route.destination},
        )

    def validate_completion(
        self, request: ActionRequest, timing: ActionTiming, context: ValidationContext
    ) -> None:
        validate_actor(request, context)
        route = _route(request, context.state)
        if route.origin != timing.data["origin"] or route.destination != timing.data["destination"]:
            raise ActionValidationError("ROUTE_CHANGED")
        # Cost was fixed at start; current cost must still be valid, but does not
        # retroactively reschedule completion. Current passability is authoritative.

    def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
        route = _route(request, context.state)
        movement = _object(context.state.get("movement"), "MISSING_POSITION")
        positions = _object(movement.get("positions"), "MISSING_POSITION")
        positions[request.actor_id] = ActorPosition(request.actor_id, route.destination).to_json()
        return TransitionPlan(
            set_values={"movement": movement},
            events=(
                EventDraft(
                    "ActorMoved",
                    1,
                    {
                        "actor_id": request.actor_id,
                        "route_id": route.route_id,
                        "origin": route.origin,
                        "destination": route.destination,
                    },
                ),
            ),
        )
