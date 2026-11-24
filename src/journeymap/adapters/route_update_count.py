"""E04 count endpoint projection and separate, inert single-send count transport."""

import http.client
import os
from contextlib import suppress
from hashlib import sha256

from journeymap.adapters.llm import strict_json
from journeymap.adapters.provider import ProviderFailure
from journeymap.adapters.route_update import obj
from journeymap.adapters.route_update_live import validate_config, wire
from journeymap.core.canonical import JsonObject, canonical_json

COUNT_ENDPOINT = "https://api.openai.com/v1/responses/input_tokens"
COUNT_VERSION = "experiment04-responses-input-count-1"
COUNT_PROJECTION: JsonObject = {
    "included": ["model", "input", "text", "reasoning"],
    "excluded": {
        "store": "storage policy, not accepted count body field",
        "max_output_tokens": "generation output ceiling, not accepted count field",
        "service_tier": "generation service selection, not accepted count field",
    },
    "transport_only": ["endpoint", "timeout_seconds", "max_response_bytes", "returned_models"],
    "reference": "https://developers.openai.com/api/reference/resources/responses/subresources/input_tokens/methods/count",
    "review_date_kst": "2026-10-09",
    "service_schema_and_reasoning_acceptance": "UNVERIFIED; future authorized smoke",
    "count_price": "UNKNOWN; not assumed free",
}


def count_wire(generation: JsonObject, inputs: JsonObject, config: JsonObject) -> JsonObject:
    if generation != wire(inputs, config):
        raise ValueError("E04 generation wire drift before count")
    body = obj(generation["body"])
    projected = {k: body[k] for k in ("model", "input", "text", "reasoning")}
    text = canonical_json(projected)
    return {
        "body": projected,
        "canonical_utf8": text,
        "bytes": len(text.encode()),
        "sha256": sha256(text.encode()).hexdigest(),
        "generation_wire_sha256": generation["sha256"],
        "projection_version": COUNT_VERSION,
    }


def token_count(raw: str) -> int:
    response = obj(strict_json(raw))
    value = response.get("input_tokens")
    if (
        set(response) != {"object", "input_tokens"}
        or response["object"] != "response.input_tokens"
        or type(value) is not int
        or value < 0
    ):
        raise ValueError("E04 invalid count response")
    return value


class CountHTTPTransport:
    """Count only, no generation fallback. Construct only after exact preflight/intent."""

    def __init__(self, config: JsonObject) -> None:
        validate_config(config)
        self.used = False

    def send(self, payload: bytes) -> bytes:
        if self.used:
            raise ValueError("E04 count transport consumed")
        self.used = True
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ProviderFailure("CREDENTIAL_UNAVAILABLE")
        connection = None
        try:
            connection = http.client.HTTPSConnection("api.openai.com", timeout=120)
            connection.request(
                "POST",
                "/v1/responses/input_tokens",
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


class RecordingCountTransport:
    """Synthetic count outcome; zero service verification or pricing evidence."""

    def __init__(self, outcome: int | str = 1000) -> None:
        self.outcome, self.used = outcome, False
        self.payload: bytes | None = None

    def send(self, payload: bytes) -> bytes:
        if self.used:
            raise ValueError("E04 count mock consumed")
        self.used, self.payload = True, payload
        if self.outcome in ("TIMEOUT", "HTTP_ERROR", "TRANSPORT_ERROR"):
            raise ProviderFailure(self.outcome)
        if isinstance(self.outcome, str):
            return self.outcome.encode()
        return canonical_json(
            {"object": "response.input_tokens", "input_tokens": self.outcome}
        ).encode()
