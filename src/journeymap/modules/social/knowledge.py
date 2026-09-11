"""Indirect acquisition from sender-owned records in a committed Event prefix."""

from journeymap.core.events import EventEnvelope
from journeymap.modules.knowledge.records import KnowledgeLedger, KnowledgeRecord
from journeymap.modules.social.contracts import validate_reply, validate_social_event


def project_informed_knowledge(
    event: EventEnvelope, previous: tuple[EventEnvelope, ...], knowledge: KnowledgeLedger
) -> tuple[KnowledgeRecord, ...]:
    if event.event_type != "ActorInformed":
        return ()
    validate_social_event(event)
    sender, target = event.payload["sender_actor_id"], event.payload["target_actor_id"]
    assert isinstance(sender, str) and isinstance(target, str)
    claim = next(
        (
            record
            for record in knowledge.for_actor(sender).history()
            if record.knowledge_record_id == event.payload["claim_record_id"]
        ),
        None,
    )
    if claim is None or claim.run_id != event.run_id or claim.learned_at > event.simulation_time:
        raise ValueError("INFORM requires sender-owned Knowledge in the Event prefix")
    reply = event.payload.get("reply_to_event_id")
    if isinstance(reply, str):
        validate_reply(previous, reply, sender, target, claim.subject_ref, claim.predicate)
    return (
        KnowledgeRecord(
            f"{event.event_id}:social-informed-v1:00000001",
            event.run_id,
            target,
            claim.subject_ref,
            claim.predicate,
            claim.value,
            "INFORMED",
            event.event_id,
            event.simulation_time,
        ),
    )
