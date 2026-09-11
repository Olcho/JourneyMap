"""Bounded survival pressure and timed REST/CONSUME actions."""

from journeymap.core.handlers import ActionRegistry, SystemEventRegistry
from journeymap.core.modules import ModuleMetadata
from journeymap.modules.survival.handlers import ConsumeHandler, RestHandler, SurvivalTickHandler


class SurvivalModule:
    @property
    def metadata(self) -> ModuleMetadata:
        return ModuleMetadata("survival", "0.6.0", ("inventory",))

    def register_actions(self, registry: ActionRegistry) -> None:
        registry.register("REST", 1, RestHandler())
        registry.register("CONSUME", 1, ConsumeHandler())

    def register_system_events(self, registry: SystemEventRegistry) -> None:
        registry.register("SurvivalTick", 1, SurvivalTickHandler())
