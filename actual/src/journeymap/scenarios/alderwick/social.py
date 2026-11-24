"""Explicit M5 author inputs, separate from the unchanged M4 fixture."""

from journeymap.modules.knowledge.records import KnowledgeRecord
from journeymap.scenarios.alderwick.fixture import initial_knowledge

# Trusted application/example activation order, not scheduler system inputs.
# Hugh first observes after the collapse, walks back, then answers Thomas.
NPC_ACTIVATIONS = ("hugh", "thomas", "hugh", "thomas", "hugh")


def social_initial_knowledge(run_id: str) -> tuple[KnowledgeRecord, ...]:
    return (
        *initial_knowledge(run_id),
        KnowledgeRecord(
            f"{run_id}:initial:social:hugh-return",
            run_id,
            "hugh",
            "east-road",
            "npc_return_route",
            {"route_id": "east-road-to-village-square"},
            "INITIAL",
            "alderwick:social-v1:guard-instructions",
            0,
        ),
        KnowledgeRecord(
            f"{run_id}:initial:social:thomas-inquiry",
            run_id,
            "thomas",
            "east-bridge",
            "npc_inquiry",
            {"target_actor_id": "hugh", "predicate": "condition"},
            "INITIAL",
            "alderwick:social-v1:travel-instructions",
            0,
        ),
        KnowledgeRecord(
            f"{run_id}:initial:social:thomas-stale",
            run_id,
            "thomas",
            "east-bridge",
            "condition",
            "intact",
            "INITIAL",
            "alderwick:social-v1:old-travel-report",
            0,
        ),
    )
