"""E04-only Responses byte boundary. No application or world capabilities."""

import http.client
import os
from contextlib import suppress
from hashlib import sha256
from typing import Protocol

from journeymap.adapters.llm import strict_json
from journeymap.adapters.provider import ProviderFailure, RawModelResponse
from journeymap.adapters.route_update import PROFILE, SCHEMA, obj, rows
from journeymap.core.canonical import JsonObject, canonical_json

CONFIG_VERSION = "experiment04-responses-config-1"
WIRE_VERSION = "experiment04-responses-wire-1"
ADAPTER_VERSION = "experiment04-responses-http-1"
MOCK_VERSION = "experiment04-responses-inert-mock-1"
ENDPOINT = "https://api.openai.com/v1/responses"
CONFIG: JsonObject = {
    "model": "gpt-6.1-sol",
    "returned_models": ["gpt-6.1-sol"],
    "reasoning_effort": "low",
    "max_output_tokens": 2048,
    "timeout_seconds": 120,
    "max_response_bytes": 2000000,
    "service_tier": "default",
    "endpoint": ENDPOINT,
}


def validate_config(config: JsonObject) -> None:
    if canonical_json(config) != canonical_json(CONFIG):
        raise ValueError("E04 exact candidate configuration required")


def wire(inputs: JsonObject, config: JsonObject) -> JsonObject:
    validate_config(config)
    body: JsonObject = {
        "model": config["model"],
        "input": PROFILE.render(inputs),
        "store": False,
        "reasoning": {"effort": config["reasoning_effort"]},
        "max_output_tokens": config["max_output_tokens"],
        "service_tier": "default",
        "text": {
            "format": {
                "type": "json_schema",
                "name": "experiment04_action",
                "strict": True,
                "schema": SCHEMA,
            }
        },
    }
    text = canonical_json(body)
    return {
        "body": body,
        "canonical_utf8": text,
        "bytes": len(text.encode()),
        "sha256": sha256(text.encode()).hexdigest(),
    }


def decode(document: JsonObject, mode: str) -> RawModelResponse:
    if mode not in ("live", "recording-mock"):
        raise ValueError("E04 response mode")
    if any(type(document.get(k)) is not str or not document[k] for k in ("id", "model", "status")):
        raise ValueError("E04 returned identity missing")
    texts: list[str] = []
    refusal = False
    for item in rows(document.get("output")):
        if item.get("type") != "message":
            continue
        for content in rows(item.get("content")):
            kind = content.get("type")
            if kind not in ("output_text", "refusal"):
                raise ValueError("E04 unsupported message content")
            value = content.get("text" if kind == "output_text" else "refusal")
            if type(value) is not str:
                raise ValueError("E04 output text missing")
            texts.append(value)
            refusal |= kind == "refusal"
    return RawModelResponse(
        "".join(texts),
        {
            "provider": "openai" if mode == "live" else "e04-inert-http-mock",
            "adapter_version": ADAPTER_VERSION if mode == "live" else MOCK_VERSION,
            "configuration_version": CONFIG_VERSION,
            "response_id": document["id"],
            "model": document["model"],
            "status": document["status"],
            "refusal": refusal,
            "incomplete_details": document.get("incomplete_details"),
            "usage": document.get("usage"),
            "service_tier": document.get("service_tier"),
            "created_at": document.get("created_at"),
        },
    )


class Transport(Protocol):
    def send(self, payload: bytes) -> bytes: ...


class HTTPTransport:
    """Single send; credential lookup occurs only here, after durable SEND intent."""

    def __init__(self, config: JsonObject) -> None:
        validate_config(config)
        self.used = False

    def send(self, payload: bytes) -> bytes:
        if self.used:
            raise ValueError("E04 transport consumed")
        self.used = True
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ProviderFailure("CREDENTIAL_UNAVAILABLE")
        connection = None
        try:
            connection = http.client.HTTPSConnection("api.openai.com", timeout=120)
            connection.request(
                "POST",
                "/v1/responses",
                body=payload,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            if response.status != 200:
                raise ProviderFailure("HTTP_ERROR", response.status)
            raw = response.read(2000001)
            if len(raw) > 2000000:
                raise ProviderFailure("RESPONSE_TOO_LARGE")
            document = obj(strict_json(raw.decode("utf-8")))
            if key in canonical_json(document):
                raise ProviderFailure("SECRET_ECHO")
            return raw
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


class RecordingTransport:
    """Explicit inert outcomes, one call at the identical bytes boundary; no defaults."""

    def __init__(self, outcome: str) -> None:
        self.outcome, self.used = outcome, False
        self.payload: bytes | None = None

    def send(self, payload: bytes) -> bytes:
        if self.used:
            raise ValueError("E04 mock consumed")
        self.used, self.payload = True, payload
        if self.outcome in ("TIMEOUT", "HTTP_ERROR", "TRANSPORT_ERROR"):
            raise ProviderFailure(self.outcome)
        body = obj(strict_json(payload.decode()))
        refusal = self.outcome == "REFUSAL"
        incomplete = self.outcome in ("TOKEN_CAP", "INCOMPLETE")
        text = (
            '{"action_type":"WAIT","payload":{"duration":1}}'
            if self.outcome in ("USAGE", "TIER")
            else self.outcome
        )
        document: JsonObject = {
            "id": "e04-inert-response",
            "model": body["model"],
            "status": "incomplete" if incomplete else "completed",
            "service_tier": "default",
            "usage": {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100},
            "incomplete_details": {
                "reason": "max_output_tokens" if self.outcome == "TOKEN_CAP" else "content_filter"
            }
            if incomplete
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
        }
        if self.outcome == "IDENTITY":
            document["model"] = "wrong-model"
        if self.outcome == "USAGE":
            document["usage"] = None
        if self.outcome == "TIER":
            document["service_tier"] = "priority"
        return canonical_json(document).encode()
