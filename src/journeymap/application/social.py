"""Live claim authority, kept outside the canonical kernel/replay boundary."""

from journeymap.core.canonical import canonical_json
from journeymap.core.events import EventEnvelope
from journeymap.core.handlers import ActionRequest, ActionValidationError
from journeymap.core.observations import Observation
from journeymap.modules.knowledge.records import ActorKnowledgeView
from journeymap.modules.social.contracts import validate_reply


def validate_claim(
    request: ActionRequest,
    observation: Observation,
    owned: ActorKnowledgeView,
    events: tuple[EventEnvelope, ...],
) -> None:
    claim = next(
        (
            record
            for record in owned.history()
            if record.knowledge_record_id == request.payload["claim_record_id"]
        ),
        None,
    )
    if (
        claim is None
        or claim.run_id != request.run_id
        or claim.actor_id != request.actor_id
        or claim.learned_at > request.submitted_at
        or claim.learned_at > observation.simulation_time
    ):
        raise ActionValidationError("INVALID_CLAIM_REFERENCE")
    sections = observation.content.get("sections")
    visible = []
    if isinstance(sections, list):
        for section in sections:
            if not isinstance(section, dict) or section.get("module_id") != "knowledge":
                continue
            content = section.get("content")
            records = content.get("records") if isinstance(content, dict) else None
            if isinstance(records, list):
                visible.extend(records)
    if canonical_json(claim.to_json()) not in [canonical_json(record) for record in visible]:
        raise ActionValidationError("INVALID_CLAIM_REFERENCE")
    reply, target = request.payload.get("reply_to_event_id"), request.payload["target_actor_id"]
    if isinstance(reply, str):
        assert isinstance(target, str)
        # The topic/source must have actually reached this Observation as well.
        interactions = []
        if isinstance(sections, list):
            for section in sections:
                if not isinstance(section, dict) or section.get("module_id") != "social":
                    continue
                content = section.get("content")
                social = content.get("social") if isinstance(content, dict) else None
                incoming = social.get("interactions") if isinstance(social, dict) else None
                if isinstance(incoming, list):
                    interactions.extend(incoming)
        if not any(
            isinstance(item, dict) and item.get("event_id") == reply for item in interactions
        ):
            raise ActionValidationError("INVALID_REPLY_REFERENCE")
        validate_reply(events, reply, request.actor_id, target, claim.subject_ref, claim.predicate)
