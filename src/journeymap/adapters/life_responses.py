"""Life Responses wire/accounting preflight. Concrete recording transport only.

No credential or network implementation is selected by this module. A future
paid runner requires separate human approval and composition work.
"""

from dataclasses import asdict, dataclass, field, fields, replace
from hashlib import sha256
from typing import NoReturn, cast

from journeymap.adapters.life_llm import (
    CONFIG_VERSION,
    PROMPT_VERSION,
    life_body,
)
from journeymap.adapters.llm import Record, strict_json
from journeymap.adapters.provider import ProviderRequest, RawModelResponse
from journeymap.core.canonical import JsonObject, canonical_json

MODEL = "gpt-5.4-mini-2026-03-17"
VERSION = "life-responses-preflight-2"
ENDPOINT = "https://api.openai.com/v1/responses"


@dataclass(frozen=True, slots=True)
class LifeExecutionPlan:
    """Frozen proposal, never authorization. USD amounts use integer nano-USD."""

    model: str = MODEL
    reasoning: str = "high"
    service_tier: str = "default"
    actor: str = "ada"
    seed: int = 42
    horizon: int = 24
    request_limit: int = 32
    max_output_tokens: int = 8192
    max_input_bytes: int = 65_536
    max_output_bytes: int = 16_384
    context_input_reserve: int = 400_000
    total_input_tokens: int = 500_000
    total_output_tokens: int = 131_072
    cost_limit_nano_usd: int = 1_000_000_000
    input_nano_usd_per_token: int = 750
    cached_nano_usd_per_token: int = 75
    output_nano_usd_per_token: int = 4500
    timeout_seconds: int = 120
    invalid_limit: int = 3

    def __post_init__(self) -> None:
        # This is one reviewable plan, not a general live configuration surface.
        if any(
            type(getattr(self, f.name)) is not type(f.default) or getattr(self, f.name) != f.default
            for f in fields(self)
        ):
            raise ValueError("unsupported execution plan; review a new version")

    @property
    def digest(self) -> str:
        return sha256(canonical_json(cast(JsonObject, asdict(self))).encode()).hexdigest()


def proposed_request(request: ProviderRequest, plan: LifeExecutionPlan) -> ProviderRequest:
    if (
        request.model != "offline-fixture"
        or request.prompt_version != PROMPT_VERSION
        or request.configuration_version != CONFIG_VERSION
        or request.parameters
        != {"max_output_tokens": plan.max_output_tokens, "reasoning": {"effort": plan.reasoning}}
    ):
        raise ValueError("unexpected offline life envelope")
    return replace(request, model=plan.model, configuration_version=VERSION)


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    reasoning_tokens: int
    total_tokens: int

    @classmethod
    def parse(cls, value: object) -> "Usage":
        if not isinstance(value, dict):
            raise ValueError("USAGE_UNAVAILABLE")
        try:
            cached = value["input_tokens_details"]["cached_tokens"]
            reasoning = value["output_tokens_details"]["reasoning_tokens"]
            fields = (value["input_tokens"], value["output_tokens"], cached, reasoning)
            total = value["total_tokens"]
        except (KeyError, TypeError):
            raise ValueError("USAGE_UNAVAILABLE") from None
        if (
            any(type(n) is not int or n < 0 for n in (*fields, total))
            or cached > fields[0]
            or reasoning > fields[1]
            or total != fields[0] + fields[1]
        ):
            raise ValueError("INVALID_USAGE")
        return cls(*fields, total)


class PreflightStop(ValueError):
    """Allowlisted local stop reason; does not expose transport exception text."""


@dataclass(slots=True)
class Budget:
    plan: LifeExecutionPlan
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_nano_usd: int = 0
    reserved_input: int = 0
    reserved_output: int = 0
    reserved_cost: int = 0
    stop: str | None = None

    def fail(self, reason: str) -> NoReturn:
        self.stop = reason
        raise PreflightStop(reason)

    def reserve(self, body: JsonObject) -> None:
        if self.stop is not None:
            raise PreflightStop(self.stop)
        if self.reserved_input or self.reserved_output:
            self.fail("REQUEST_UNCERTAIN")
        p = self.plan
        if len(canonical_json(body).encode("utf-8")) > p.max_input_bytes:
            self.fail("INPUT_TOO_LARGE")
        if self.attempts >= p.request_limit:
            self.fail("REQUEST_LIMIT")
        cost = (
            p.context_input_reserve * p.input_nano_usd_per_token
            + p.max_output_tokens * p.output_nano_usd_per_token
        )
        if self.input_tokens + p.context_input_reserve > p.total_input_tokens:
            self.fail("INPUT_TOKEN_LIMIT")
        if self.output_tokens + p.max_output_tokens > p.total_output_tokens:
            self.fail("OUTPUT_TOKEN_LIMIT")
        if self.cost_nano_usd + cost > p.cost_limit_nano_usd:
            self.fail("COST_LIMIT")
        self.reserved_input = p.context_input_reserve
        self.reserved_output = p.max_output_tokens
        self.reserved_cost = cost
        self.attempts += 1

    def settle(self, usage: Usage) -> None:
        if not self.reserved_input:
            self.fail("NO_RESERVATION")
        p = self.plan
        if usage.input_tokens > self.reserved_input or usage.output_tokens > self.reserved_output:
            self.fail("USAGE_EXCEEDS_RESERVATION")
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        # output_tokens already includes reasoning; never add it a second time.
        self.cost_nano_usd += (
            (usage.input_tokens - usage.cached_tokens) * p.input_nano_usd_per_token
            + usage.cached_tokens * p.cached_nano_usd_per_token
            + usage.output_tokens * p.output_nano_usd_per_token
        )
        self.reserved_input = self.reserved_output = self.reserved_cost = 0


@dataclass(slots=True)
class RecordingResponsesTransport:
    """Concrete offline transport: synthetic HTTP status + raw JSON response."""

    responses: tuple[tuple[int, str] | Exception, ...]
    bodies: list[Record] = field(default_factory=list)

    def send(self, body: JsonObject) -> tuple[int, str]:
        self.bodies.append(Record.capture(body))
        index = len(self.bodies) - 1
        if index >= len(self.responses):
            raise RuntimeError("fixture exhausted")
        result = self.responses[index]
        if isinstance(result, Exception):
            raise result
        return result


def response_text(document: JsonObject) -> tuple[str, bool]:
    output = document.get("output")
    if not isinstance(output, list):
        raise ValueError("INVALID_RESPONSE")
    texts: list[str] = []
    refusal = False
    messages = 0
    for item in output:
        if not isinstance(item, dict):
            raise ValueError("INVALID_RESPONSE")
        if item.get("type") == "reasoning":
            continue  # Do not request or expose hidden reasoning or summaries.
        if item.get("type") != "message" or item.get("role") != "assistant":
            raise ValueError("UNEXPECTED_OUTPUT")
        messages += 1
        content = item.get("content")
        if not isinstance(content, list):
            raise ValueError("INVALID_RESPONSE")
        for part in content:
            if not isinstance(part, dict):
                raise ValueError("INVALID_RESPONSE")
            kind = part.get("type")
            text = part.get("text" if kind == "output_text" else "refusal")
            if kind not in {"output_text", "refusal"} or type(text) is not str:
                raise ValueError("UNEXPECTED_OUTPUT")
            texts.append(text)
            refusal |= kind == "refusal"
    if messages > 1:
        raise ValueError("UNEXPECTED_OUTPUT")
    return "".join(texts), refusal


class LifeResponsesPreflightProvider:
    """Fixture bridge: real proposed wire is recorded separately from old envelope.

    Kept unidentified intentionally: Provider contract classifies it as fixture.
    The old offline request/body stays valid for engine replay. This is not a
    live-capable session, network factory, approval token or checkpoint format.
    """

    def __init__(
        self, transport: RecordingResponsesTransport, plan: LifeExecutionPlan | None = None
    ) -> None:
        if type(transport) is not RecordingResponsesTransport:
            raise PermissionError("recording Responses transport required")
        self.plan = plan or LifeExecutionPlan()
        self.transport = transport
        self.budget = Budget(self.plan)
        self.records: list[Record] = []

    def generate(self, request: ProviderRequest) -> RawModelResponse:
        wire = proposed_request(request, self.plan)
        body = life_body(wire)
        body["service_tier"] = self.plan.service_tier
        self.budget.reserve(body)
        record: JsonObject = {
            "sequence": self.budget.attempts,
            "plan_digest": self.plan.digest,
            "endpoint": ENDPOINT,
            "wire_request": asdict(wire),
            "body": body,
            "evidence_kind": "synthetic-recording-transport",
        }
        try:
            status, raw = self.transport.send(body)
            record["http_status"] = status
            record["raw_response"] = raw
            if status != 200:
                self.budget.fail("HTTP_ERROR")
            if len(raw.encode("utf-8")) > 2_000_000:
                self.budget.fail("RESPONSE_TOO_LARGE")
            document = strict_json(raw)
            if not isinstance(document, dict):
                self.budget.fail("INVALID_RESPONSE")
            try:
                usage = Usage.parse(document.get("usage"))
            except ValueError as error:
                self.budget.fail(str(error))
            record["usage"] = asdict(usage)
            self.budget.settle(usage)
            if document.get("model") != self.plan.model:
                self.budget.fail("IDENTITY_MISMATCH")
            if document.get("service_tier") != self.plan.service_tier:
                self.budget.fail("SERVICE_TIER_MISMATCH")
            if type(document.get("id")) is not str or not document["id"]:
                self.budget.fail("INVALID_RESPONSE_ID")
            state = document.get("status")
            if state not in {"completed", "incomplete"}:
                self.budget.fail("RESPONSE_FAILED")
            try:
                text, refusal = response_text(document)
            except ValueError as error:
                self.budget.fail(str(error))
            if len(text.encode("utf-8")) > self.plan.max_output_bytes:
                self.budget.fail("OUTPUT_TOO_LARGE")
            classification = (
                "REFUSAL" if refusal else "INCOMPLETE" if state != "completed" else "READY"
            )
            record["classification"] = classification
            if classification != "READY":
                self.budget.stop = classification
            # Identity of the fixture bridge differs from the proposed wire model.
            # Preserve that distinction explicitly, never label mock as live.
            return RawModelResponse(
                text,
                {
                    "status": state,
                    "refusal": refusal,
                    "usage": document["usage"],
                    "response_id": document.get("id"),
                    "wire_model": document["model"],
                    "evidence_kind": "synthetic-recording-transport",
                    "incomplete_details": document.get("incomplete_details"),
                },
            )
        except PreflightStop as error:
            record["error"] = str(error)
            raise
        except (ValueError, TypeError, RecursionError, UnicodeError):
            record["error"] = "INVALID_RESPONSE"
            self.budget.fail("INVALID_RESPONSE")
        except Exception:
            record["error"] = "TRANSPORT_UNCERTAIN"
            self.budget.fail("TRANSPORT_UNCERTAIN")
        finally:
            record["accounting"] = cast(
                JsonObject, asdict(self.budget) | {"plan": asdict(self.plan)}
            )
            self.records.append(Record.capture(record))


def preflight_provider(
    transport: RecordingResponsesTransport, *, kind: str = "fixture"
) -> LifeResponsesPreflightProvider:
    if kind != "fixture":
        raise PermissionError("live execution requires separately approved composition")
    return LifeResponsesPreflightProvider(transport)


def synthetic_response(text: str, *, status: str = "completed", refusal: bool = False) -> str:
    """Protocol fixture numbers, never a token measurement or behavioral result."""
    return canonical_json(
        {
            "id": "synthetic-life-response",
            "model": MODEL,
            "service_tier": "default",
            "status": status,
            "usage": {
                "input_tokens": 1000,
                "output_tokens": 100,
                "total_tokens": 1100,
                "input_tokens_details": {"cached_tokens": 200},
                "output_tokens_details": {"reasoning_tokens": 60},
            },
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "refusal", "refusal": text}
                        if refusal
                        else {"type": "output_text", "text": text}
                    ],
                }
            ],
            "incomplete_details": {"reason": "max_output_tokens"}
            if status == "incomplete"
            else None,
        }
    )
