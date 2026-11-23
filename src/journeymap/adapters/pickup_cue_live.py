"""Pickup-only Responses wire contract and one-shot transport; no world capability."""

import http.client
import math
import os
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol, cast

from journeymap.adapters.horizon_baseline import move_to
from journeymap.adapters.llm import Record, strict_json
from journeymap.adapters.memory_horizon_live import wire_schema as action_schema
from journeymap.adapters.pickup_cue_prompt import PROFILE, delivered_cue
from journeymap.adapters.provider import ProviderFailure, ProviderRequest, RawModelResponse
from journeymap.core.canonical import JsonObject, canonical_json

CONFIGURATION_VERSION = "pickup-cue-responses-1"
WIRE_VERSION = "pickup-cue-move-unit-wait-1"
ADAPTER_VERSION = "pickup-cue-responses-http-1"
MOCK_VERSION = "pickup-cue-http-recording-mock-1"
ENDPOINT = "https://api.openai.com/v1/responses"


def validate_config(config: JsonObject, *, ready: bool) -> None:
    if (
        set(config)
        != {
            "provider",
            "model",
            "returned_models",
            "reasoning_effort",
            "max_output_tokens",
            "timeout_seconds",
            "max_response_bytes",
            "model_support_review",
            "budget_review",
        }
        or config["provider"] != "openai"
    ):
        raise ValueError("pickup configuration inventory")
    for key in ("model", "model_support_review", "budget_review"):
        value = config[key]
        if value is None and not ready:
            continue
        if type(value) is not str or not value.strip():
            raise ValueError("unresolved pickup configuration")
    models = config["returned_models"]
    if (models is not None or ready) and (
        not isinstance(models, list)
        or not models
        or any(type(m) is not str or not m.strip() for m in models)
        or len(set(cast(list[str], models))) != len(models)
    ):
        raise ValueError("explicit returned-model allowlist required")
    effort = config["reasoning_effort"]
    if effort not in (None, "none", "minimal", "low", "medium", "high", "xhigh"):
        raise ValueError("unsupported reasoning syntax")
    for key in ("max_output_tokens", "max_response_bytes"):
        value = config[key]
        if value is None and not ready:
            continue
        if type(value) is not int or value <= 0:
            raise ValueError("positive integer limit required")
    timeout = config["timeout_seconds"]
    if (timeout is not None or ready) and (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("finite positive timeout required")


def provider_request(inputs: JsonObject, config: JsonObject) -> ProviderRequest:
    validate_config(config, ready=True)
    parameters: JsonObject = {"max_output_tokens": config["max_output_tokens"]}
    if config["reasoning_effort"] is not None:
        parameters["reasoning"] = {"effort": config["reasoning_effort"]}
    return ProviderRequest(
        PROFILE.render(inputs),
        PROFILE.version,
        cast(str, config["model"]),
        CONFIGURATION_VERSION,
        parameters,
    )


def request_body(request: ProviderRequest, config: JsonObject) -> JsonObject:
    """Exact allowlist. Re-rendering also rejects prompt/instruction/parameter drift."""
    try:
        inputs = strict_json(request.prompt.split("\nINPUT_JSON\n", 1)[1])
        if not isinstance(inputs, dict):
            raise ValueError("request mismatch")
        expected = provider_request(inputs, config)
        if request != expected or canonical_json(request.parameters) != canonical_json(
            expected.parameters
        ):
            raise ValueError("request mismatch")
    except (IndexError, TypeError, ValueError):
        raise ValueError("pickup request contract") from None
    return {
        "model": request.model,
        "input": request.prompt,
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "pickup_cue_decision",
                "strict": True,
                "schema": action_schema(),
            }
        },
        **request.parameters,
    }


def wire_record(request: ProviderRequest, config: JsonObject) -> JsonObject:
    body = request_body(request, config)
    text = canonical_json(body)
    payload = text.encode("utf-8")
    return {
        "body": body,
        "canonical_utf8": text,
        "bytes": len(payload),
        "sha256": sha256(payload).hexdigest(),
    }


def decode_response(document: JsonObject, *, mock: bool) -> RawModelResponse:
    """Preserve model text and allowlisted service provenance, never HTTP headers."""
    if any(type(document.get(k)) is not str or not document[k] for k in ("id", "model", "status")):
        raise ValueError("response identity missing")
    texts: list[str] = []
    refusal = False
    output = document.get("output")
    if not isinstance(output, list):
        raise ValueError("response output missing")
    for item in output:
        if not isinstance(item, dict):
            raise ValueError("response output invalid")
        if item.get("type") != "message":
            continue
        contents = item.get("content")
        if not isinstance(contents, list):
            raise ValueError("response message invalid")
        for content in contents:
            if not isinstance(content, dict):
                raise ValueError("response content invalid")
            kind = content.get("type")
            if kind in ("output_text", "refusal"):
                value = content.get("text" if kind == "output_text" else "refusal")
                if type(value) is not str:
                    raise ValueError("response text invalid")
                texts.append(value)
                refusal |= kind == "refusal"
    return RawModelResponse(
        "".join(texts),
        {
            "provider": "fake" if mock else "openai",
            "adapter_version": MOCK_VERSION if mock else ADAPTER_VERSION,
            "response_id": document["id"],
            "model": document["model"],
            "status": document["status"],
            "usage": document.get("usage"),
            "refusal": refusal,
            "incomplete_details": document.get("incomplete_details"),
        },
    )


class Transport(Protocol):
    def send(self, payload: bytes) -> RawModelResponse: ...


class HTTPTransport:
    """Construct only after preflight and durable intent. One fixed endpoint, no retry."""

    def __init__(self, config: JsonObject) -> None:
        validate_config(config, ready=True)
        self.timeout = cast(float, config["timeout_seconds"])
        self.limit = cast(int, config["max_response_bytes"])
        self.used = False

    def send(self, payload: bytes) -> RawModelResponse:
        if self.used:
            raise ValueError("transport already consumed")
        self.used = True
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ProviderFailure("CREDENTIAL_UNAVAILABLE")
        connection = None
        try:
            connection = http.client.HTTPSConnection("api.openai.com", timeout=self.timeout)
            connection.request(
                "POST",
                "/v1/responses",
                body=payload,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
            response = connection.getresponse()
            if response.status != 200:
                raise ProviderFailure("HTTP_ERROR", response.status)
            raw = response.read(self.limit + 1)
            if len(raw) > self.limit:
                raise ProviderFailure("RESPONSE_TOO_LARGE")
            document = strict_json(raw.decode("utf-8"))
            if not isinstance(document, dict):
                raise ProviderFailure("TRANSPORT_ERROR")
            # Check decoded values too: JSON escaping must not bypass secret filtering.
            if key in canonical_json(document):
                raise ProviderFailure("SECRET_ECHO")
            return decode_response(document, mock=False)
        except ProviderFailure:
            raise
        except TimeoutError:
            raise ProviderFailure("TIMEOUT") from None
        except Exception:
            raise ProviderFailure("TRANSPORT_ERROR") from None
        finally:
            if connection is not None:
                with suppress(Exception):
                    connection.close()


@dataclass(frozen=True)
class MockOutcome:
    kind: str = "notice"
    text: str = '{"action_type":"WAIT","payload":{"duration":1}}'


DEFAULT_OUTCOME = MockOutcome()


class RecordingTransport:
    """Concrete inert mock at the very same byte boundary as HTTPTransport.send."""

    def __init__(self, outcome: MockOutcome = DEFAULT_OUTCOME) -> None:
        if type(outcome) is not MockOutcome or outcome.kind not in (
            "notice",
            "text",
            "refusal",
            "incomplete",
            "timeout",
            "transport-error",
            "model-mismatch",
        ):
            raise ValueError("inert mock outcome required")
        self.outcome = outcome
        self.payloads: list[bytes] = []

    def send(self, payload: bytes) -> RawModelResponse:
        if self.payloads:
            raise ValueError("one call per fresh transport")
        self.payloads.append(payload)
        if self.outcome.kind in ("timeout", "transport-error"):
            raise ProviderFailure(
                "TIMEOUT" if self.outcome.kind == "timeout" else "TRANSPORT_ERROR"
            )
        body = cast(JsonObject, strict_json(payload.decode("utf-8")))
        text = self.outcome.text
        if self.outcome.kind == "notice":
            prompt = cast(str, body["input"])
            inputs = cast(JsonObject, strict_json(prompt.split("\nINPUT_JSON\n", 1)[1]))
            cue = delivered_cue(inputs)
            text = canonical_json(
                move_to(cast(JsonObject, inputs["observation"]), cue)
                if cue
                else {"action_type": "WAIT", "payload": {"duration": 1}}
            )
        refusal = self.outcome.kind == "refusal"
        return decode_response(
            {
                "id": "recording-mock-response",
                "model": (
                    "unexpected-model" if self.outcome.kind == "model-mismatch" else body["model"]
                ),
                "status": "incomplete" if self.outcome.kind == "incomplete" else "completed",
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "incomplete_details": {"reason": "max_output_tokens"}
                if self.outcome.kind == "incomplete"
                else None,
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "refusal" if refusal else "output_text",
                                "refusal" if refusal else "text": text,
                            }
                        ],
                    }
                ],
            },
            mock=True,
        )


class PickupProvider:
    """Only immutable request/config data and a transport; no runtime/archive handles."""

    def __init__(self, transport: Transport, config: JsonObject) -> None:
        self.transport = transport
        self.config = Record.capture(config)
        self.used = False

    def generate(self, request: ProviderRequest) -> RawModelResponse:
        if self.used:
            raise ValueError("one evaluation attempt")
        self.used = True
        body = request_body(request, self.config.data)
        return self.transport.send(canonical_json(body).encode("utf-8"))
