"""Experiment 04 authored map, local perception and validated system interventions."""

from typing import cast

from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.core.entities import Entity, entity_state, get_entity
from journeymap.core.events import EventDraft, EventEnvelope, EventSourceKind
from journeymap.core.handlers import (
    ResolutionContext,
    TransitionPlan,
    ValidationContext,
)
from journeymap.core.modules import ModuleMetadata
from journeymap.core.observations import PerceptionContext
from journeymap.core.scheduler import ScheduledEvent, ScheduledEventSpec
from journeymap.modules.knowledge import KnowledgeRecord
from journeymap.modules.movement import (
    ActorPosition,
    Location,
    Route,
    movement_state,
)
from journeymap.modules.movement.perception import perceive_position
from journeymap.modules.movement.transitions import close_routes

VERSION = "experiment04-route-update-1"
ACTOR = "traveller"
CONDITIONS = ("unchanged", "closed-unannounced", "closed-notified")
EDGES = (
    ("S", "A", 2),
    ("S", "B", 2),
    ("A", "C", 2),
    ("A", "D", 4),
    ("B", "D", 4),
    ("B", "C", 6),
    ("C", "G", 2),
    ("D", "G", 2),
    ("C", "D", 4),
)
CLOSED = ("C-to-G", "G-to-C")


def authored_map() -> list[JsonValue]:
    """Author input, never constructed from current World Truth."""
    return [
        {
            "route_id": f"{origin}-to-{destination}",
            "origin": origin,
            "destination": destination,
            "cost": cost,
            "passable": True,
        }
        for a, b, cost in EDGES
        for origin, destination in ((a, b), (b, a))
    ]


def world() -> JsonObject:
    return {
        "entities": entity_state((Entity(ACTOR, "actor"),)),
        "movement": movement_state(
            locations=tuple(Location(p) for p in ("S", "A", "B", "C", "D", "G")),
            routes=tuple(
                Route(
                    cast(str, r["route_id"]),
                    cast(str, r["origin"]),
                    cast(str, r["destination"]),
                    cast(int, r["cost"]),
                )
                for item in authored_map()
                for r in (cast(JsonObject, item),)
            ),
            positions=(ActorPosition(ACTOR, "S"),),
        ),
    }


def initial_knowledge(run_id: str) -> tuple[KnowledgeRecord, ...]:
    return (
        KnowledgeRecord(
            f"{run_id}:initial-map",
            run_id,
            ACTOR,
            "initial-route-map",
            "authored-map",
            authored_map(),
            "AUTHORED_INITIAL",
            "author-map-1",
            0,
        ),
    )


class RouteUpdateModule:
    @property
    def metadata(self) -> ModuleMetadata:
        return ModuleMetadata("route-update", "0.1.0", ("movement", "knowledge"))


class CloseHandler:
    handler_id = "route-update.close-both.v1"

    def validate(self, event: ScheduledEvent, context: ValidationContext) -> None:
        if event.payload != {"route_ids": list(CLOSED)}:
            raise ValueError("invalid fixed closure")
        routes = cast(JsonObject, cast(JsonObject, context.state["movement"])["routes"])
        if any(cast(JsonObject, routes[r])["passable"] is not True for r in CLOSED):
            raise ValueError("closure already installed")
        close_routes(context.state, CLOSED)

    def resolve(self, event: ScheduledEvent, context: ResolutionContext) -> TransitionPlan:
        self.validate(
            event, ValidationContext(context.run_id, context.simulation_time, context.state)
        )
        positions = cast(JsonObject, cast(JsonObject, context.state["movement"])["positions"])
        witnesses: list[JsonValue] = [
            actor
            for actor, p in sorted(positions.items())
            if cast(JsonObject, p)["location_id"] in ("C", "G")
        ]
        return TransitionPlan(
            set_values={"movement": close_routes(context.state, CLOSED)},
            events=(
                EventDraft(
                    "RoutesClosed", 1, {"route_ids": list(CLOSED), "witness_actor_ids": witnesses}
                ),
            ),
        )


class NoticeHandler:
    handler_id = "route-update.official-notice.v1"

    def validate(self, event: ScheduledEvent, context: ValidationContext) -> None:
        recipient = event.payload.get("recipient_actor_id")
        if (
            set(event.payload) != {"recipient_actor_id", "route_ids"}
            or type(recipient) is not str
            or event.payload["route_ids"] != list(CLOSED)
            or get_entity(context.state, recipient) is None
        ):
            raise ValueError("invalid notice recipient")
        routes = cast(JsonObject, cast(JsonObject, context.state["movement"])["routes"])
        if any(cast(JsonObject, routes[r])["passable"] is not False for r in CLOSED):
            raise ValueError("notice requires committed closure")

    def resolve(self, event: ScheduledEvent, context: ResolutionContext) -> TransitionPlan:
        self.validate(
            event, ValidationContext(context.run_id, context.simulation_time, context.state)
        )
        return TransitionPlan(
            events=(
                EventDraft(
                    "OfficialRouteNotice",
                    1,
                    {
                        "recipient_actor_id": event.payload["recipient_actor_id"],
                        "route_ids": list(CLOSED),
                    },
                ),
            )
        )


def interventions(condition: str) -> tuple[ScheduledEventSpec, ...]:
    if condition not in CONDITIONS:
        raise ValueError("unknown E04 condition")
    if condition == "unchanged":
        return ()
    result: tuple[ScheduledEventSpec, ...] = (
        ScheduledEventSpec(3, 0, "CLOSE_ROUTES", 1, {"route_ids": list(CLOSED)}),
    )
    if condition == "closed-notified":
        result += (
            ScheduledEventSpec(
                3, 1, "OFFICIAL_NOTICE", 1, {"recipient_actor_id": ACTOR, "route_ids": list(CLOSED)}
            ),
        )
    return result


def project_knowledge(events: tuple[EventEnvelope, ...]) -> tuple[KnowledgeRecord, ...]:
    """Fold committed events; preserve initial truth and each later source/time."""
    closed = False
    records: list[KnowledgeRecord] = []
    for event in events:
        recipients: list[str] = []
        kind = "DIRECT_OBSERVATION"
        if event.event_type == "RoutesClosed":
            if (
                closed
                or event.source_kind != EventSourceKind.SCHEDULED_EVENT
                or event.payload.get("route_ids") != list(CLOSED)
            ):
                raise ValueError("invalid closure evidence")
            closed = True
            recipients = cast(list[str], event.payload["witness_actor_ids"])
        elif event.event_type == "OfficialRouteNotice":
            if (
                not closed
                or event.source_kind != EventSourceKind.SCHEDULED_EVENT
                or event.payload.get("route_ids") != list(CLOSED)
            ):
                raise ValueError("notice before closure")
            recipients = [cast(str, event.payload["recipient_actor_id"])]
            kind = "OFFICIAL_NOTICE"
        elif event.event_type == "ActorMoved" and event.payload.get("destination") in ("C", "G"):
            if event.source_kind != EventSourceKind.ACTION:
                raise ValueError("invalid arrival witness")
            recipients = [cast(str, event.payload["actor_id"])]
        for actor in recipients:
            records.append(
                KnowledgeRecord(
                    f"{event.event_id}:route-status:{actor}",
                    event.run_id,
                    actor,
                    "C-G-status",
                    "route-status",
                    {"route_ids": list(CLOSED), "passable": not closed},
                    kind,
                    event.event_id,
                    event.simulation_time,
                )
            )
    return tuple(records)


def perceive_local(state: JsonObject, actor_id: str) -> JsonObject:
    place = perceive_position(state, actor_id).get("location_id")
    routes = cast(JsonObject, cast(JsonObject, state["movement"])["routes"])
    # Rebuild only outgoing fields. Remote passability cannot change shape/order.
    exits: list[JsonValue] = [
        {
            "route_id": r["route_id"],
            "destination": r["destination"],
            "cost": r["traversal_cost"],
            "passable": r["passable"],
        }
        for _, item in sorted(routes.items())
        for r in (cast(JsonObject, item),)
        if r["origin"] == place
    ]
    return {"local_routes": exits}


def contribute_local(context: PerceptionContext) -> JsonObject:
    return {"local_routes": context.perceived["local_routes"]}
