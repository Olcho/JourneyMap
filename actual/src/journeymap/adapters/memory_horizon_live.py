"""Pilot wire contract and concrete recording mock. No network or credential code."""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import cast

from journeymap.adapters.event_memory import EventMemoryPolicy, EventTraceArchive
from journeymap.adapters.llm import (
    DecisionFailure,
    Record,
    event_memory_input,
    observation_json,
    parameters_v1,
    parse_candidate,
    strict_json,
)
from journeymap.adapters.memory_horizon_prompt import PROFILE, semantic_input
from journeymap.adapters.provider import ProviderFailure, ProviderRequest, RawModelResponse
from journeymap.core.canonical import JsonObject, canonical_json
from journeymap.core.handlers import ActionRequest
from journeymap.core.observations import Observation

WIRE_SCHEMA_VERSION = "memory-horizon-decision-1"
CONFIGURATION_VERSION = "memory-horizon-responses-preflight-1"
MOCK_VERSION = "memory-horizon-recording-mock-1"
ENDPOINT = "https://api.openai.com/v1/responses"


def wire_schema() -> JsonObject:
    """Responses-compatible object envelope; exact action/payload pairing is local.

    As with the existing adapter, root unions are avoided. Neither other action
    names nor arbitrary positive WAIT durations occur in this independent schema.
    Cross-paired MOVE/duration or WAIT/route objects fail validate_candidate.
    """
    return {
        "type": "object",
        "properties": {
            "action_type": {"type": "string", "enum": ["MOVE", "WAIT"]},
            "payload": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {"route_id": {"type": "string", "minLength": 1}},
                        "required": ["route_id"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {"duration": {"type": "integer", "enum": [1]}},
                        "required": ["duration"],
                        "additionalProperties": False,
                    },
                ]
            },
        },
        "required": ["action_type", "payload"],
        "additionalProperties": False,
    }


def validate_candidate(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {"action_type", "payload"}:
        raise ValueError("invalid pilot candidate")
    payload = value["payload"]
    if not isinstance(payload, dict):
        raise ValueError("invalid pilot payload")
    move = (
        value["action_type"] == "MOVE"
        and set(payload) == {"route_id"}
        and type(payload["route_id"]) is str
        and bool(payload["route_id"])
    )
    wait = (
        value["action_type"] == "WAIT"
        and set(payload) == {"duration"}
        and type(payload["duration"]) is int
        and payload["duration"] == 1
    )
    if not (move or wait):
        raise ValueError("pilot permits only MOVE or unit WAIT")


def request_body(request: ProviderRequest) -> JsonObject:
    if (
        request.configuration_version != CONFIGURATION_VERSION
        or request.prompt_version != PROFILE.version
    ):
        raise ValueError("wrong pilot request version")
    return {
        "model": request.model,
        "input": request.prompt,
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "memory_horizon_decision_v1",
                "strict": True,
                "schema": wire_schema(),
            }
        },
        **parameters_v1(request.parameters),
    }


def call_evidence(request: ProviderRequest) -> JsonObject:
    body = request_body(request)
    return {
        "provider_request": cast(JsonObject, asdict(request)),
        "endpoint": ENDPOINT,
        "method": "POST",
        "configuration_version": CONFIGURATION_VERSION,
        "wire_schema_version": WIRE_SCHEMA_VERSION,
        "body": body,
        "body_canonical": canonical_json(body),
        "body_sha256": sha256(canonical_json(body).encode()).hexdigest(),
        "prompt_sha256": sha256(request.prompt.encode()).hexdigest(),
    }


@dataclass(frozen=True)
class MockOutcome:
    text: str = '{"action_type":"WAIT","payload":{"duration":1}}'
    status: str = "completed"
    refusal: bool = False
    transport_failure: bool = False


class RecordingMockTransport:
    """Consumes inert fixtures only; it cannot dispatch a real transport."""

    def __init__(self, outcomes: tuple[MockOutcome, ...]) -> None:
        if any(type(item) is not MockOutcome for item in outcomes):
            raise TypeError("inert MockOutcome fixtures required")
        self._outcomes = tuple(outcomes)
        self._cursor = 0
        self._claimed = False
        self.calls: list[Record] = []

    def claim(self) -> None:
        if self._claimed or self.calls:
            raise ValueError("transport reuse across trials is forbidden")
        self._claimed = True

    def send(self, body: JsonObject) -> MockOutcome:
        if not self._claimed:
            raise ValueError("unclaimed mock transport")
        self.calls.append(Record.capture(body))
        outcome = (
            self._outcomes[self._cursor] if self._cursor < len(self._outcomes) else MockOutcome()
        )
        self._cursor += 1
        return outcome


class MockHorizonProvider:
    def __init__(
        self,
        transport: RecordingMockTransport,
        record: Callable[[str, JsonObject], None],
    ) -> None:
        if type(transport) is not RecordingMockTransport:
            raise TypeError("preflight accepts only the concrete recording mock")
        self._transport = transport
        self._record = record
        self._claimed = False
        self.attempts: list[Record] = []

    def claim(self) -> None:
        if self._claimed:
            raise ValueError("provider reuse across trials is forbidden")
        self._transport.claim()
        self._claimed = True

    def generate(self, request: ProviderRequest) -> RawModelResponse:
        if not self._claimed:
            raise ValueError("provider must be claimed by its trial")
        evidence = call_evidence(request)
        self._record("provider_intent", evidence)
        outcome = self._transport.send(cast(JsonObject, evidence["body"]))
        result: JsonObject = {
            "raw_output": None,
            "metadata": {},
            "transport_failure": None,
            "transport_body": self._transport.calls[-1].data,
        }
        if outcome.transport_failure:
            result["transport_failure"] = "TRANSPORT_ERROR"
        else:
            result.update(
                {
                    "raw_output": outcome.text,
                    "metadata": {
                        "provider": "fake",
                        "adapter_version": MOCK_VERSION,
                        "model": request.model,
                        "status": outcome.status,
                        "refusal": outcome.refusal,
                        "response_id": f"mock-response-{len(self.attempts) + 1}",
                    },
                }
            )
        self._record("provider_result", result)
        self.attempts.append(Record.capture({**evidence, **result}))
        if outcome.transport_failure:
            raise ProviderFailure("TRANSPORT_ERROR")
        return RawModelResponse(outcome.text, cast(JsonObject, result["metadata"]))


class PilotController:
    """Same semantic v2 prompt and archive, independently versioned wire validation."""

    def __init__(
        self,
        provider: MockHorizonProvider,
        memory: EventMemoryPolicy,
        model: str,
        parameters: JsonObject,
    ) -> None:
        if type(provider) is not MockHorizonProvider:
            raise TypeError("live transport is disabled")
        provider.claim()
        self._provider = provider
        self._memory = memory
        self._model = model
        self._parameters = parameters_v1(parameters)
        self.archive = EventTraceArchive()
        self.last_decision: Record | None = None

    def decide(self, observation: Observation) -> ActionRequest:
        self.last_decision = None
        attempt_offset = len(self._provider.attempts)
        context = self.archive.begin(observation)
        inputs, provenance = event_memory_input(context, self._memory)
        inputs = semantic_input(inputs)
        request = ProviderRequest(
            PROFILE.render(inputs),
            PROFILE.version,
            self._model,
            CONFIGURATION_VERSION,
            self._parameters,
        )
        data: JsonObject = {
            **provenance,
            "observation": observation_json(observation),
            "model_visible_input": inputs,
            "model_visible_input_canonical": canonical_json(inputs),
            "opportunity_sequence": context.opportunity_sequence,
            "opportunity_attempt": context.attempt,
            "wire_schema_version": WIRE_SCHEMA_VERSION,
            "wire_schema": wire_schema(),
            "provider_request": cast(JsonObject, asdict(request)),
            "provider_called": True,
            "raw_output": None,
            "provider_metadata": {},
            "action_request": None,
            "failure": None,
        }
        try:
            response = self._provider.generate(request)
            data.update({"raw_output": response.text, "provider_metadata": response.metadata})
            if response.metadata.get("refusal"):
                data["failure"] = "REFUSAL"
                raise ValueError("refused")
            if response.metadata.get("status") != "completed":
                data["failure"] = "INCOMPLETE_OUTPUT"
                raise ValueError("incomplete")
            data["failure"] = "PARSER_FAILURE"
            candidate = strict_json(response.text)
            data["failure"] = "SCHEMA_INVALID"
            validate_candidate(candidate)
            action = parse_candidate(candidate, observation)
            data.update({"failure": None, "action_request": cast(JsonObject, asdict(action))})
            return action
        except ProviderFailure:
            data["failure"] = "INFRASTRUCTURE_FAILURE"
            raise DecisionFailure("INFRASTRUCTURE_FAILURE") from None
        except ValueError:
            raise DecisionFailure(cast(str, data["failure"])) from None
        finally:
            data["call"] = (
                self._provider.attempts[-1].data
                if len(self._provider.attempts) > attempt_offset
                else None
            )
            self.last_decision = Record.capture(data)
