"""M5 structured interaction module; no canonical social state."""

from journeymap.core.handlers import ActionRegistry
from journeymap.core.modules import ModuleMetadata
from journeymap.modules.social.contracts import EVENT_TYPES
from journeymap.modules.social.handlers import SocialHandler


class SocialModule:
    @property
    def metadata(self) -> ModuleMetadata:
        return ModuleMetadata("social", "0.5.0", ("movement", "knowledge"))

    def register_actions(self, registry: ActionRegistry) -> None:
        for action in EVENT_TYPES:
            registry.register(action, 1, SocialHandler(action))
