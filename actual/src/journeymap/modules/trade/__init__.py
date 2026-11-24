"""One integer currency, local offers and atomic BUY."""

from journeymap.core.handlers import ActionRegistry
from journeymap.core.modules import ModuleMetadata
from journeymap.modules.trade.handlers import BuyHandler


class TradeModule:
    @property
    def metadata(self) -> ModuleMetadata:
        return ModuleMetadata("trade", "0.6.0", ("inventory", "movement"))

    def register_actions(self, registry: ActionRegistry) -> None:
        registry.register("BUY", 1, BuyHandler())
