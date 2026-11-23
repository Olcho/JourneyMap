"""Minimal directed spatial graph and module-owned actor positions."""

from collections.abc import Iterable
from dataclasses import dataclass

from journeymap.core.canonical import JsonObject


def _identity(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("spatial identity must be a non-empty string")


@dataclass(frozen=True, slots=True)
class Location:
    location_id: str

    def __post_init__(self) -> None:
        _identity(self.location_id)

    def to_json(self) -> JsonObject:
        return {"location_id": self.location_id}


@dataclass(frozen=True, slots=True)
class Route:
    """A directed edge; reverse travel requires another explicit route."""

    route_id: str
    origin: str
    destination: str
    traversal_cost: int
    passable: bool = True

    def __post_init__(self) -> None:
        for value in (self.route_id, self.origin, self.destination):
            _identity(value)
        if type(self.traversal_cost) is not int or self.traversal_cost <= 0:
            raise ValueError("traversal_cost must be a positive integer")
        if type(self.passable) is not bool:
            raise ValueError("passable must be a boolean")

    def to_json(self) -> JsonObject:
        return {
            "route_id": self.route_id,
            "origin": self.origin,
            "destination": self.destination,
            "traversal_cost": self.traversal_cost,
            "passable": self.passable,
        }


@dataclass(frozen=True, slots=True)
class ActorPosition:
    actor_id: str
    location_id: str

    def __post_init__(self) -> None:
        _identity(self.actor_id)
        _identity(self.location_id)

    def to_json(self) -> JsonObject:
        return {"actor_id": self.actor_id, "location_id": self.location_id}


def movement_state(
    *, locations: Iterable[Location], routes: Iterable[Route], positions: Iterable[ActorPosition]
) -> JsonObject:
    """Build the initial movement value; runtime checks also validate references.

    Entities are owned outside this module, so actor existence is checked by
    handlers. Location references are validated here before a run is created.
    """

    location_records: JsonObject = {}
    route_records: JsonObject = {}
    position_records: JsonObject = {}
    for location in locations:
        if location.location_id in location_records:
            raise ValueError(f"duplicate location_id: {location.location_id}")
        location_records[location.location_id] = location.to_json()
    for route in routes:
        if route.route_id in route_records:
            raise ValueError(f"duplicate route_id: {route.route_id}")
        if route.origin not in location_records or route.destination not in location_records:
            raise ValueError("route references an unknown location")
        route_records[route.route_id] = route.to_json()
    for position in positions:
        if position.actor_id in position_records:
            raise ValueError(f"duplicate actor position: {position.actor_id}")
        if position.location_id not in location_records:
            raise ValueError("position references an unknown location")
        position_records[position.actor_id] = position.to_json()
    return {"locations": location_records, "routes": route_records, "positions": position_records}
