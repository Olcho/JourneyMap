"""Pure Responses wire/response boundary, with inert transport only.

No HTTP, credentials, retries, approval state or operational accounting is owned
here. A future authorized runner must durably record send intent and raw response
before engine delivery. A prepared request is not permission to send it.
"""

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from typing import Any, cast

from journeymap.adapters.llm import strict_json
from journeymap.adapters.route_update import SCHEMA
from journeymap_memoryreplica.paired import encode

MODEL = "gpt-5.4-mini-2026-03-17"
EFFORT = "medium"
MAX_OUTPUT_TOKENS = 8192
MAX_RESPONSE_BYTES = 2_000_000
TIMEOUT_MS = 120_000
RATES = {"input": "0.75", "cached_input": "0.075", "output": "4.50"}


def reconstruction_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["facts"],
        "properties": {
            "facts": {
                "type": "array",
                "maxItems": 512,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["route_id", "field", "value", "as_of", "evidence"],
                    "properties": {
                        "route_id": {"type": "string", "minLength": 1},
                        "field": {
                            "type": "string",
                            "enum": ["origin", "destination", "cost", "passable"],
                        },
                        "value": {
                            "anyOf": [
                                {"type": "string"},
                                {"type": "integer"},
                                {"type": "boolean"},
                                {"type": "null"},
                            ]
                        },
                        "as_of": {"type": ["integer", "null"], "minimum": 0},
                        "evidence": {"type": ["string", "null"]},
                    },
                },
            }
        },
    }


def build_wire(request_text: str) -> dict[str, Any]:
    request = strict_json(request_text)
    if not isinstance(request, dict) or set(request) != {"profile", "task", "context"}:
        raise ValueError("expected MemoryReplica request")
    if request["profile"] != "memoryreplica-task-offline-1":
        raise ValueError("unsupported request profile")
    task = request["task"]
    if not isinstance(task, dict) or task.get("kind") not in ("action", "reconstruction"):
        raise ValueError("unsupported task kind")
    kind = cast(str, task["kind"])
    body = {
        "model": MODEL,
        "input": request_text,
        "reasoning": {"effort": EFFORT},
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "store": False,
        "stream": False,
        "service_tier": "default",
        "text": {
            "format": {
                "type": "json_schema",
                "name": "memoryreplica_" + kind,
                "strict": True,
                "schema": deepcopy(SCHEMA) if kind == "action" else reconstruction_schema(),
            }
        },
    }
    serialized = encode(body)
    return {
        "body": body,
        "canonical_utf8": serialized,
        "sha256": sha256(serialized.encode()).hexdigest(),
        "request_bytes": len(serialized.encode()),
        "kind": kind,
        "wire_version": "memoryreplica-responses-preparation-1",
    }


def usage_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("missing usage")
    counts = [value.get(k) for k in ("input_tokens", "output_tokens", "total_tokens")]
    if any(type(n) is not int or n < 0 for n in counts):
        raise ValueError("invalid usage counts")
    inp, out, total = (cast(int, n) for n in counts)
    if total != inp + out:
        raise ValueError("inconsistent usage total")
    details = value.get("input_tokens_details")
    # Cache omission does not silently become exact measured uncached usage.
    cache_reported = isinstance(details, dict) and "cached_tokens" in details
    cached: Any = cast(dict[str, Any], details)["cached_tokens"] if cache_reported else 0
    if type(cached) is not int or not 0 <= cached <= inp:
        raise ValueError("invalid cached count")
    out_details = value.get("output_tokens_details")
    reasoning: Any = out_details.get("reasoning_tokens") if isinstance(out_details, dict) else None
    if reasoning is not None and (type(reasoning) is not int or not 0 <= reasoning <= out):
        raise ValueError("invalid reasoning count")
    cost = (
        Decimal(inp - cached) * Decimal(RATES["input"])
        + Decimal(cached) * Decimal(RATES["cached_input"])
        + Decimal(out) * Decimal(RATES["output"])
    ) / Decimal(1_000_000)
    return {
        "input_tokens": inp,
        "cached_input_tokens": cached if cache_reported else None,
        "output_tokens": out,
        "reasoning_tokens": reasoning,
        "total_tokens": total,
        "rate_converted_usd": str(cost),
        "cost_basis": "reported_usage" if cache_reported else "uncached_upper_estimate",
        "reasoning_already_in_output": True,
    }


@dataclass(frozen=True)
class Frame:
    body: bytes
    http_status: int = 200
    complete: bool = True
    elapsed_ms: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.body) is not bytes
            or type(self.complete) is not bool
            or type(self.http_status) is not int
            or not 100 <= self.http_status <= 599
            or type(self.elapsed_ms) is not int
            or self.elapsed_ms < 0
        ):
            raise ValueError("invalid inert frame")


def decode_frame(frame: Frame) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "TRANSPORT_UNCERTAIN",
        "text": None,
        "usage": None,
        "response_id": None,
        "reported_model": None,
        "incomplete_details": None,
        "raw_sha256": sha256(frame.body).hexdigest(),
        "raw_utf8": None,
        "http_status": frame.http_status,
        "elapsed_ms": frame.elapsed_ms,
        "frame_complete": frame.complete,
    }
    if frame.complete is not True or len(frame.body) > MAX_RESPONSE_BYTES:
        return result
    try:
        result["raw_utf8"] = frame.body.decode("utf-8")
        document = strict_json(result["raw_utf8"])
        if not isinstance(document, dict):
            raise ValueError("response must be object")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        result["status"] = "INVALID_RESPONSE"
        return result
    if frame.http_status != 200:
        result["status"] = "HTTP_ERROR"
        return result
    result.update(
        response_id=document.get("id"),
        reported_model=document.get("model"),
        incomplete_details=document.get("incomplete_details"),
    )
    if (
        type(document.get("id")) is not str
        or not document["id"]
        or document.get("model") != MODEL
        or document.get("service_tier") != "default"
    ):
        result["status"] = "IDENTITY_OR_TIER_MISMATCH"
        return result
    try:
        result["usage"] = usage_record(document.get("usage"))
    except ValueError:
        result["status"] = "USAGE_INVALID"
        return result
    if (
        result["usage"]["output_tokens"] > MAX_OUTPUT_TOKENS
        or result["usage"]["input_tokens"] > 400_000
    ):
        result["status"] = "USAGE_LIMIT_VIOLATION"
        return result
    if frame.elapsed_ms > TIMEOUT_MS:
        # The full late response has usable accounting, but no engine delivery.
        result["status"] = "LATE_COMPLETE"
        return result
    status = document.get("status")
    if status == "incomplete":
        result["status"] = "INCOMPLETE"
        return result
    if status != "completed":
        result["status"] = "API_NOT_COMPLETED"
        return result
    if document.get("error") is not None or document.get("incomplete_details") is not None:
        result["status"] = "INVALID_RESPONSE"
        return result
    output = document.get("output")
    if not isinstance(output, list):
        result["status"] = "INVALID_RESPONSE"
        return result
    messages = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") not in ("message", "reasoning"):
            result["status"] = "UNEXPECTED_OUTPUT_ITEM"
            return result
        if item["type"] == "message":
            messages.append(item)
    if (
        len(messages) != 1
        or messages[0].get("role") != "assistant"
        or messages[0].get("status") != "completed"
    ):
        result["status"] = "INVALID_RESPONSE"
        return result
    content = messages[0].get("content")
    if not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict):
        result["status"] = "INVALID_RESPONSE"
        return result
    if content[0].get("type") == "refusal" and type(content[0].get("refusal")) is str:
        result["status"] = "REFUSAL"
        return result
    if (
        content[0].get("type") != "output_text"
        or type(content[0].get("text")) is not str
        or not content[0]["text"]
    ):
        result["status"] = "INVALID_RESPONSE"
        return result
    result.update(status="TEXT_READY", text=content[0]["text"])
    return result


class InertTransport:
    """One immutable mocked frame, one send; accepts no executable callbacks."""

    def __init__(self, frame: Frame) -> None:
        if type(frame) is not Frame:
            raise TypeError("inert Frame required")
        self.frame = frame
        self.calls = 0
        self.sent: bytes | None = None

    def send(self, payload: bytes) -> Frame:
        if self.calls:
            raise ValueError("transport already consumed; retries prohibited")
        self.calls += 1
        self.sent = payload
        return self.frame


def exchange(request_text: str, transport: InertTransport) -> dict[str, Any]:
    if type(transport) is not InertTransport:
        raise TypeError("this preparation path permits only inert transport")
    wire = build_wire(request_text)
    frame = transport.send(wire["canonical_utf8"].encode())
    return {"wire": wire, "outcome": decode_frame(frame), "evidence_kind": "INERT_RESPONSES_MOCK"}
