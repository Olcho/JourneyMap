"""A Stranger in Alderwick: the minimal M4 bridge scenario."""

from journeymap.core.handlers import SystemEventRegistry
from journeymap.core.modules import ModuleMetadata
from journeymap.scenarios.alderwick.bridge import CollapseBridgeHandler


class AlderwickModule:
    @property
    def metadata(self) -> ModuleMetadata:
        return ModuleMetadata("alderwick", "0.4.0", ("movement", "knowledge"))

    def register_system_events(self, registry: SystemEventRegistry) -> None:
        registry.register("CollapseEastBridge", 1, CollapseBridgeHandler())
