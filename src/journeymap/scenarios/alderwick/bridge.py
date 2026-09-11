"""Scenario-owned bridge truth, location visibility and atomic collapse."""

from journeymap.core.canonical import JsonObject, JsonValue, clone_json_object
from journeymap.core.entities import get_entity
from journeymap.core.events import EventDraft
from journeymap.core.handlers import ResolutionContext, TransitionPlan, ValidationContext
from journeymap.core.scheduler import ScheduledEvent
from journeymap.modules.movement.perception import perceive_position
from journeymap.modules.movement.transitions import close_routes

BRIDGE_ID = "east-bridge"
OBSERVABLE_LOCATIONS = ("east-road", "east-bridge")
AFFECTED_ROUTES = ("east-bridge-to-east-road", "east-road-to-east-bridge")


def bridge_condition(world: JsonObject) -> str:
    alderwick = world.get("alderwick")
    bridge = alderwick.get("east_bridge") if isinstance(alderwick, dict) else None
    if not isinstance(bridge, dict) or bridge.get("bridge_id") != BRIDGE_ID:
        raise ValueError("invalid East Bridge identity")
    condition = bridge.get("condition")
    if condition not in ("intact", "collapsed"):
        raise ValueError("invalid East Bridge condition")
    assert isinstance(condition, str)
    return condition


def bridge_witnesses(world: JsonObject) -> tuple[str, ...]:
    entities = world.get("entities")
    if not isinstance(entities, dict):
        raise ValueError("missing entities")
    witnesses = []
    for actor_id in sorted(entities):
        get_entity(world, actor_id)
        if perceive_position(world, actor_id).get("location_id") in OBSERVABLE_LOCATIONS:
            witnesses.append(actor_id)
    return tuple(witnesses)


def perceive_bridge(world: JsonObject, actor_id: str) -> JsonObject:
    """Trusted allowlist: invisible bridge truth is never read or copied."""
    if perceive_position(world, actor_id).get("location_id") not in OBSERVABLE_LOCATIONS:
        return {"bridge": {}}
    return {"bridge": {"bridge_id": BRIDGE_ID, "condition": bridge_condition(world)}}


class CollapseBridgeHandler:
    handler_id = "alderwick.collapse-east-bridge.v1"

    def validate(self, event: ScheduledEvent, context: ValidationContext) -> None:
        if event.payload != {"bridge_id": BRIDGE_ID}:
            raise ValueError("invalid collapse payload")
        if bridge_condition(context.state) != "intact":
            raise ValueError("East Bridge is already collapsed")
        close_routes(context.state, AFFECTED_ROUTES)
        bridge_witnesses(context.state)

    def resolve(self, event: ScheduledEvent, context: ResolutionContext) -> TransitionPlan:
        self.validate(
            event, ValidationContext(context.run_id, context.simulation_time, context.state)
        )
        movement = close_routes(context.state, AFFECTED_ROUTES)
        witnesses: list[JsonValue] = list(bridge_witnesses(context.state))
        alderwick = context.state["alderwick"]
        assert isinstance(alderwick, dict)
        candidate = clone_json_object(alderwick)
        bridge = candidate["east_bridge"]
        assert isinstance(bridge, dict)
        bridge["condition"] = "collapsed"
        return TransitionPlan(
            set_values={"alderwick": candidate, "movement": movement},
            events=(
                EventDraft(
                    "BridgeCollapsed",
                    1,
                    {
                        "bridge_id": BRIDGE_ID,
                        "condition": "collapsed",
                        "closed_route_ids": list(AFFECTED_ROUTES),
                        "witness_actor_ids": witnesses,
                    },
                ),
            ),
        )
