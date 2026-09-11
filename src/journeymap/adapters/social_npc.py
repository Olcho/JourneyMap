"""Small stateless non-LLM policy, driven entirely by delivered Observation."""

from journeymap.core.canonical import JsonObject
from journeymap.core.handlers import ActionRequest
from journeymap.core.observations import Observation


class SocialNpcController:
    """Answer unambiguous questions, follow explicit instructions, otherwise wait.

    Multiple matching claims mean abstention, not automatic belief adjudication.
    Reply completion comes from perceived committed interactions, not local memory.
    """

    __slots__ = ()

    def decide(self, observation: Observation) -> ActionRequest:
        sections = observation.content.get("sections", [])
        records: list[JsonObject] = []
        interactions: list[JsonObject] = []
        location = None
        if not isinstance(sections, list):
            raise ValueError("invalid Observation sections")
        for section in sections:
            if not isinstance(section, dict) or not isinstance(section.get("content"), dict):
                raise ValueError("invalid Observation section")
            content = section["content"]
            assert isinstance(content, dict)
            if section.get("module_id") == "knowledge":
                known = content.get("records", [])
                if isinstance(known, list):
                    records.extend(
                        record
                        for record in known
                        if isinstance(record, dict)
                        and record.get("actor_id") == observation.actor_id
                        and record.get("run_id") == observation.run_id
                    )
            position = content.get("position")
            if isinstance(position, dict):
                location = position.get("location_id")
            social = content.get("social")
            incoming = social.get("interactions") if isinstance(social, dict) else None
            if isinstance(incoming, list):
                interactions.extend(item for item in incoming if isinstance(item, dict))

        def request(action: str, payload: JsonObject) -> ActionRequest:
            return ActionRequest(
                f"{observation.observation_id}:social-npc-v1",
                observation.run_id,
                observation.actor_id,
                observation.observation_id,
                observation.simulation_time,
                action,
                1,
                payload,
            )

        for interaction in interactions:
            if interaction.get("event_type") != "ActorAsked" or interaction.get("answered"):
                continue
            matches = [
                record
                for record in records
                if record.get("subject_ref") == interaction.get("subject_ref")
                and record.get("predicate") == interaction.get("predicate")
            ]
            if len(matches) == 1:
                return request(
                    "INFORM",
                    {
                        "target_actor_id": interaction["sender_actor_id"],
                        "claim_record_id": matches[0]["knowledge_record_id"],
                        "reply_to_event_id": interaction["event_id"],
                    },
                )
        for record in records:
            value = record.get("value")
            if not isinstance(value, dict):
                continue
            if (
                record.get("predicate") == "npc_return_route"
                and record.get("subject_ref") == location
                and any(item.get("source_kind") == "DIRECT_OBSERVATION" for item in records)
            ):
                # Return only after acquiring direct information; no tick/hidden schedule query.
                return request("MOVE", {"route_id": value["route_id"]})
            if record.get("predicate") == "npc_inquiry" and not any(
                item.get("subject_ref") == record.get("subject_ref")
                and item.get("predicate") == value.get("predicate")
                and item.get("source_kind") == "INFORMED"
                for item in records
            ):
                return request(
                    "ASK",
                    {
                        "target_actor_id": value["target_actor_id"],
                        "subject_ref": record["subject_ref"],
                        "predicate": value["predicate"],
                    },
                )
        return request("WAIT", {"duration": 1})
