"""Small deterministic policy using only supplied Observation content."""

from journeymap.core.canonical import JsonObject
from journeymap.core.handlers import ActionRequest
from journeymap.core.observations import Observation, validate_observation_v1


class ScriptedController:
    """Follow an explicitly known travel route; wait at a visible collapsed bridge.

    Route instructions are initial actor knowledge, not a hidden map injected
    into the Controller. No state, callbacks, world handles or scenario imports.
    """

    __slots__ = ()

    def decide(self, observation: Observation) -> ActionRequest:
        validate_observation_v1(observation)
        location = None
        blocked = False
        records = []
        sections = observation.content.get("sections", [])
        if not isinstance(sections, list):
            raise ValueError("invalid Observation sections")
        for section in sections:
            if not isinstance(section, dict) or not isinstance(section.get("content"), dict):
                raise ValueError("invalid Observation section")
            content = section["content"]
            assert isinstance(content, dict)
            position = content.get("position")
            if isinstance(position, dict):
                location = position.get("location_id")
            bridge = content.get("bridge")
            if isinstance(bridge, dict) and bridge.get("condition") == "collapsed":
                blocked = True
            known = content.get("records")
            if isinstance(known, list):
                records.extend(known)
        action_type = "WAIT"
        payload: JsonObject = {"duration": 1}
        if not blocked:
            for record in records:
                if not isinstance(record, dict):
                    continue
                route = record.get("value")
                if (
                    record.get("actor_id") == observation.actor_id
                    and record.get("run_id") == observation.run_id
                    and record.get("subject_ref") == location
                    and record.get("predicate") == "travel_route"
                    and isinstance(route, dict)
                    and isinstance(route.get("route_id"), str)
                    and route["route_id"]
                ):
                    action_type, payload = "MOVE", {"route_id": route["route_id"]}
                    break
        return ActionRequest(
            f"{observation.observation_id}:scripted-v1",
            observation.run_id,
            observation.actor_id,
            observation.observation_id,
            observation.simulation_time,
            action_type,
            1,
            payload,
        )
