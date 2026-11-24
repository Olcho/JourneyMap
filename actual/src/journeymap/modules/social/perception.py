"""Trusted actor filtering over committed interactions, never global delivery."""

from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.core.events import EventEnvelope
from journeymap.core.observations import PerceptionContext
from journeymap.modules.social.contracts import EVENT_TYPES, validate_social_event


def perceive_social(events: tuple[EventEnvelope, ...], actor_id: str) -> JsonObject:
    incoming: list[JsonValue] = []
    answered: set[str] = set()
    for event in events:
        if event.event_type not in EVENT_TYPES.values():
            continue
        if actor_id not in (
            event.payload.get("sender_actor_id"),
            event.payload.get("target_actor_id"),
        ):
            continue
        validate_social_event(event)
        reply = event.payload.get("reply_to_event_id")
        if event.event_type == "ActorInformed" and isinstance(reply, str):
            answered.add(reply)
        if event.payload["target_actor_id"] == actor_id:
            # Claim source IDs belong to the sender's private history. The target
            # sees its own projected record, linked by this interaction ID.
            fields = (
                "sender_actor_id",
                "subject_ref",
                "predicate",
                "request_kind",
                "request_payload",
                "reply_to_event_id",
            )
            incoming.append(
                {
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "simulation_time": event.simulation_time,
                    **{key: event.payload[key] for key in fields if key in event.payload},
                }
            )
    for item in incoming:
        assert isinstance(item, dict)
        if item["event_type"] == "ActorAsked":
            item["answered"] = item["event_id"] in answered
    return {"interactions": incoming}


def contribute_social(context: PerceptionContext) -> JsonObject:
    return {"social": context.perceived.get("social", {"interactions": []})}
