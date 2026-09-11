"""Exact M5 v1 schemas; communication conveys claims, never World Truth."""

from journeymap.core.canonical import JsonObject
from journeymap.core.events import EventEnvelope, EventSourceKind
from journeymap.core.handlers import ActionValidationError

EVENT_TYPES = {"ASK": "ActorAsked", "INFORM": "ActorInformed", "REQUEST": "ActorRequested"}


def validate_payload(action_type: str, payload: JsonObject) -> None:
    required = {
        "ASK": {"target_actor_id", "subject_ref", "predicate"},
        "INFORM": {"target_actor_id", "claim_record_id"},
        "REQUEST": {"target_actor_id", "request_kind", "request_payload"},
    }[action_type]
    optional = {"reply_to_event_id"} if action_type == "INFORM" else set()
    if not required <= payload.keys() or payload.keys() - required - optional:
        raise ActionValidationError("INVALID_PAYLOAD")
    for key, value in payload.items():
        if key == "request_payload":
            if not isinstance(value, dict):
                raise ActionValidationError("INVALID_PAYLOAD")
        elif type(value) is not str or not value:
            raise ActionValidationError("INVALID_PAYLOAD")


def validate_social_event(event: EventEnvelope) -> None:
    action = next((a for a, name in EVENT_TYPES.items() if name == event.event_type), None)
    if action is None:
        raise ValueError("not a social Event")
    if event.schema_version != 1 or event.source_kind != EventSourceKind.ACTION:
        raise ValueError("unsupported social Event evidence")
    payload = event.payload.copy()
    sender, location = payload.pop("sender_actor_id", None), payload.pop("location_id", None)
    if (
        not isinstance(sender, str)
        or not sender
        or not isinstance(location, str)
        or not location
        or sender == payload.get("target_actor_id")
    ):
        raise ValueError("invalid social Event participants/location")
    validate_payload(action, payload)


def validate_reply(
    events: tuple[EventEnvelope, ...],
    reference: str,
    sender: str,
    target: str,
    subject: str,
    predicate: str,
) -> None:
    asked = next((event for event in events if event.event_id == reference), None)
    if asked is None or asked.event_type != "ActorAsked":
        raise ActionValidationError("INVALID_REPLY_REFERENCE")
    validate_social_event(asked)
    if (
        asked.payload["sender_actor_id"] != target
        or asked.payload["target_actor_id"] != sender
        or asked.payload["subject_ref"] != subject
        or asked.payload["predicate"] != predicate
    ):
        raise ActionValidationError("INVALID_REPLY_REFERENCE")
