"""One opt-in Responses HTTP adapter. No SDK, tool calls, retry or world handle."""

import http.client
import json
import math
import os

from journeymap.adapters.decision_schema import response_format
from journeymap.adapters.llm import parameters_v1
from journeymap.adapters.provider import ProviderFailure, ProviderRequest, RawModelResponse
from journeymap.core.canonical import JsonObject, canonical_json


def request_body(request: ProviderRequest) -> JsonObject:
    """Reviewable exact HTTP body; no environment access or transport side effect."""
    return {
        "model": request.model,
        "input": request.prompt,
        "store": False,
        "text": {"format": response_format()},
        **parameters_v1(request.parameters),
    }


class OpenAIProvider:
    name = "openai"
    version = "responses-http-2"

    def __init__(self, *, timeout_seconds: float = 30) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("invalid provider timeout")
        self.timeout_seconds = timeout_seconds

    def generate(self, request: ProviderRequest) -> RawModelResponse:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ProviderFailure("CREDENTIAL_UNAVAILABLE")
        body = request_body(request)
        # Fixed HTTPS destination, no redirects, no proxy environment, no arbitrary
        # headers/config, no request/response error body in exception or logs.
        connection = http.client.HTTPSConnection("api.openai.com", timeout=self.timeout_seconds)
        try:
            connection.request(
                "POST",
                "/v1/responses",
                body=canonical_json(body).encode("utf-8"),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            response = connection.getresponse()
            if response.status != 200:
                raise ProviderFailure("HTTP_ERROR", response.status)
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ProviderFailure("RESPONSE_TOO_LARGE")
            document = json.loads(raw.decode("utf-8"))
            texts: list[str] = []
            refusal = False
            for item in document.get("output", []):
                if item.get("type") == "message":
                    for content in item.get("content", []):
                        if content.get("type") == "output_text":
                            texts.append(content["text"])
                        elif content.get("type") == "refusal":
                            refusal = True
                            texts.append(content.get("refusal", ""))
            metadata: JsonObject = {
                "provider": self.name,
                "adapter_version": self.version,
                "response_id": document.get("id"),
                "model": document.get("model"),
                "status": document.get("status"),
                "usage": document.get("usage"),
                "refusal": refusal,
                "incomplete_details": document.get("incomplete_details"),
            }
            # Defensive last boundary: never persist an echoed credential.
            text = "".join(texts)
            if key in text or key in canonical_json(metadata):
                raise ProviderFailure("SECRET_ECHO")
            return RawModelResponse(text, metadata)
        except ProviderFailure:
            raise
        except TimeoutError:
            raise ProviderFailure("TIMEOUT") from None
        except Exception:
            raise ProviderFailure("TRANSPORT_ERROR") from None
        finally:
            connection.close()
