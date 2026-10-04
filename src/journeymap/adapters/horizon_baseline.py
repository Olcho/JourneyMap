"""Stateless previous-place reference policy, never a fifth LLM condition."""

import json
from typing import cast

from journeymap.adapters.provider import ProviderIdentity, ProviderRequest, RawModelResponse
from journeymap.core.canonical import JsonObject, canonical_json


class BaselineInputError(ValueError):
    """The delivered evidence does not satisfy the reference policy's premises."""


def sections(observation: JsonObject) -> dict[str, JsonObject]:
    return {
        cast(str, section["contributor_id"]): cast(JsonObject, section["content"])
        for section in cast(list[JsonObject], cast(JsonObject, observation["content"])["sections"])
    }


def move_to(observation: JsonObject, destination: str) -> JsonObject:
    exits = cast(
        list[JsonObject], cast(JsonObject, sections(observation)["local"]["local"])["exits"]
    )
    routes = [route for route in exits if route["destination"] == destination]
    if len(routes) != 1:
        raise BaselineInputError("BASELINE_INPUT_PRECONDITION: unique local route required")
    return {"action_type": "MOVE", "payload": {"route_id": routes[0]["route_id"]}}


class PreviousPlaceProvider:
    __slots__ = ()

    @property
    def identity(self) -> ProviderIdentity:
        return ProviderIdentity("fake", "memory-horizon-previous-place-fixture-1")

    def generate(self, request: ProviderRequest) -> RawModelResponse:
        try:
            inputs = json.loads(request.prompt.split("\nINPUT_JSON\n", 1)[1])
            current = inputs["observation"]
            location = sections(current)["position"]["position"]
            location_id = cast(JsonObject, location)["location_id"]
            memory = inputs["event_memory"]
            if len(memory) > 1:
                raise ValueError("requires at most one closed experience")
            previous = (
                cast(JsonObject, sections(memory[0]["observation"])["position"]["position"])[
                    "location_id"
                ]
                if memory
                else None
            )
            destination: str | None
            if location_id in ("inn", "bakery", "well"):
                destination = "village-square"
            elif location_id == "village-square":
                destination = {
                    None: "inn",
                    "inn": "bakery",
                    "bakery": "well",
                    "well": None,
                    "village-square": None,
                }[cast(str | None, previous)]
            else:
                raise ValueError("unsupported current place")
            action: JsonObject = (
                move_to(current, destination)
                if destination is not None
                else {"action_type": "WAIT", "payload": {"duration": 1}}
            )
        except (KeyError, TypeError, ValueError, IndexError):
            return RawModelResponse(
                "",
                {
                    "provider": "fake",
                    "adapter_version": self.identity.version,
                    "model": request.model,
                    "status": "failed",
                    "incomplete_details": {"reason": "BASELINE_INPUT_PRECONDITION"},
                },
            )
        return RawModelResponse(
            canonical_json(action),
            {
                "provider": "fake",
                "adapter_version": self.identity.version,
                "model": request.model,
            },
        )
