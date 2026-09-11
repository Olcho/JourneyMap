"""Knowledge owns epistemic records independently of canonical World Truth."""

from journeymap.core.modules import ModuleMetadata
from journeymap.modules.knowledge.records import (
    ActorKnowledgeView,
    KnowledgeLedger,
    KnowledgeRecord,
    contribute_knowledge,
)

__all__ = [
    "ActorKnowledgeView",
    "KnowledgeLedger",
    "KnowledgeModule",
    "KnowledgeRecord",
    "contribute_knowledge",
]


class KnowledgeModule:
    @property
    def metadata(self) -> ModuleMetadata:
        return ModuleMetadata("knowledge", "0.5.0")
