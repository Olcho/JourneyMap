"""Concurrent-v1 fixture and validated environment input; no engine ownership."""

from journeymap.core.canonical import JsonObject
from journeymap.core.entities import Entity, entity_state
from journeymap.core.events import EventDraft
from journeymap.core.handlers import (
    ResolutionContext,
    TransitionPlan,
    ValidationContext,
)
from journeymap.core.scheduler import ScheduledEvent
from journeymap.modules.inventory.models import ItemDefinition, inventory_state
from journeymap.modules.movement import (
    ActorPosition,
    Location,
    Route,
    movement_state,
)
from journeymap.modules.movement.transitions import close_routes
from journeymap.modules.trade.models import Offer, Wallet, trade_state


def initial_world() -> JsonObject:
    actors = ("A", "B", "seller")
    return {
        "entities": entity_state(Entity(a, "person") for a in actors),
        "movement": movement_state(
            locations=(Location(p) for p in ("square", "a-end", "b-end")),
            routes=(
                Route("a-route", "square", "a-end", 2),
                Route("b-route", "square", "b-end", 2),
                Route("a-return", "a-end", "square", 2),
                Route("b-return", "b-end", "square", 2),
            ),
            positions=(ActorPosition(a, "square") for a in actors),
        ),
        "inventory": inventory_state(
            (ItemDefinition("bread"),), {a: {"bread": 1 if a == "seller" else 0} for a in actors}
        ),
        "trade": trade_state(
            (Wallet(a, 0 if a == "seller" else 10) for a in actors),
            (Offer("bread-offer", "seller", "bread", 2),),
        ),
    }


class CloseRoute:
    handler_id = "phase1.close-route.v1"

    def validate(self, event: ScheduledEvent, context: ValidationContext) -> None:
        if set(event.payload) != {"route_id"} or not isinstance(event.payload["route_id"], str):
            raise ValueError("invalid route closure")
        close_routes(context.state, (event.payload["route_id"],))

    def resolve(self, event: ScheduledEvent, context: ResolutionContext) -> TransitionPlan:
        self.validate(
            event, ValidationContext(context.run_id, context.simulation_time, context.state)
        )
        route_id = event.payload["route_id"]
        assert isinstance(route_id, str)
        return TransitionPlan(
            set_values={"movement": close_routes(context.state, (route_id,))},
            events=(EventDraft("RouteClosed", 1, {"route_id": route_id}),),
        )
