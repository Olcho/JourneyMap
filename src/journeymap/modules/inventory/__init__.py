"""Minimal item identity and ownership; no survival or trade semantics."""

from journeymap.core.modules import ModuleMetadata


class InventoryModule:
    @property
    def metadata(self) -> ModuleMetadata:
        return ModuleMetadata("inventory", "0.6.0")
