"""Explicit movement composition; no runtime plugin discovery."""

from journeymap.core.handlers import ActionRegistry
from journeymap.core.modules import ModuleMetadata
from journeymap.modules.movement.handlers import MoveHandler
from journeymap.modules.movement.models import ActorPosition, Location, Route, movement_state

__all__ = ["ActorPosition", "Location", "MoveHandler", "MovementModule", "Route", "movement_state"]


class MovementModule:
    @property
    def metadata(self) -> ModuleMetadata:
        return ModuleMetadata("movement", "0.2.0")

    def register_actions(self, registry: ActionRegistry) -> None:
        registry.register("MOVE", 1, MoveHandler())
