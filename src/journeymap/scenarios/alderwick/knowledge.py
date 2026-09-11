"""Version 1 direct acquisition rules for the one-way M4 bridge transition."""

from journeymap.core.events import EventEnvelope, EventSourceKind
from journeymap.modules.knowledge.records import KnowledgeRecord
from journeymap.scenarios.alderwick.bridge import BRIDGE_ID, OBSERVABLE_LOCATIONS


def project_bridge_knowledge(events: tuple[EventEnvelope, ...]) -> tuple[KnowledgeRecord, ...]:
    """Fold occurrence order, never today's truth or today's witness positions.

    Initial bridge truth is intact in scenario v1, and collapse is its only
    condition transition. The fold tracks only evidence needed for acquisition;
    it is not a replacement World Truth store. Repair requires a new rule version.
    """
    collapsed = False
    learned: set[str] = set()
    records = []
    for event in events:
        actors: list[str] = []
        if event.event_type == "BridgeCollapsed":
            payload = event.payload
            witnesses = payload.get("witness_actor_ids")
            if (
                event.schema_version != 1
                or event.source_kind != EventSourceKind.SCHEDULED_EVENT
                or payload.get("bridge_id") != BRIDGE_ID
                or payload.get("condition") != "collapsed"
                or not isinstance(witnesses, list)
                or any(not isinstance(actor, str) or not actor for actor in witnesses)
                or collapsed
            ):
                raise ValueError("invalid BridgeCollapsed projection evidence")
            actors = [str(actor) for actor in witnesses]
            if actors != sorted(set(actors)):
                raise ValueError("witnesses must be distinct and ordered")
            collapsed = True
        elif event.event_type == "ActorMoved":
            if event.schema_version != 1 or event.source_kind != EventSourceKind.ACTION:
                raise ValueError("unsupported ActorMoved evidence")
            actor = event.payload.get("actor_id")
            if not isinstance(actor, str) or not actor:
                raise ValueError("invalid moving actor")
            if collapsed and event.payload.get("destination") in OBSERVABLE_LOCATIONS:
                actors = [actor]
        for index, actor in enumerate(actors, start=1):
            if actor in learned:
                continue
            records.append(
                KnowledgeRecord(
                    f"{event.event_id}:alderwick-direct-v1:{index:08d}",
                    event.run_id,
                    actor,
                    BRIDGE_ID,
                    "condition",
                    "collapsed",
                    "DIRECT_OBSERVATION",
                    event.event_id,
                    event.simulation_time,
                )
            )
            learned.add(actor)
    return tuple(records)
