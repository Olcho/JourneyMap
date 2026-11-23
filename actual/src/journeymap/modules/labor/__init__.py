"""Small opt-in employment/production module, independent of the scenario."""

from journeymap.core.handlers import ActionRegistry
from journeymap.core.modules import ModuleMetadata
from journeymap.modules.labor.handlers import WorkHandler


class LaborModule:
    @property
    def metadata(self) -> ModuleMetadata:
        return ModuleMetadata("labor", "0.1.0", ("inventory", "trade", "movement", "survival"))

    def register_actions(self, registry: ActionRegistry) -> None:
        registry.register("WORK", 1, WorkHandler())
