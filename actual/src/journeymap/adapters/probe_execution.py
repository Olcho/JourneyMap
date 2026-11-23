"""Frozen paired-decision execution contract; proposals never grant permission."""

from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import cast
from uuid import UUID

from journeymap.adapters.life_admission import source_identity
from journeymap.adapters.life_responses import MODEL, Usage, response_text
from journeymap.adapters.llm import strict_json
from journeymap.adapters.probe_diagnostics import DIAGNOSTIC_VERSION
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json, state_digest

PROTOCOL = "life-reasoning-probe-execution-2"
ENDPOINT = "https://api.openai.com/v1/responses"
CONTINUE = {"READY", "INCOMPLETE", "REFUSAL"}


def object_(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError("expected object")
    return value


def rows(value: JsonValue) -> list[JsonObject]:
    if not isinstance(value, list):
        raise ValueError("expected array")
    return [object_(row) for row in value]


def same(left: JsonValue, right: JsonValue) -> bool:
    return canonical_json(left) == canonical_json(right)


def uuid_(value: str) -> None:
    if type(value) is not str or str(UUID(value)) != value:
        raise ValueError("canonical UUID required")


@dataclass(frozen=True, slots=True)
class ProbePlan:
    model: str = MODEL
    service_tier: str = "default"
    request_limit: int = 72
    max_output_tokens: int = 8192
    context_input_reserve: int = 400_000
    total_input_tokens: int = 500_000
    total_output_tokens: int = 589_824
    cost_limit_nano_usd: int = 1_000_000_000
    input_rate: int = 750
    cached_rate: int = 75
    output_rate: int = 4500
    timeout_ms: int = 120_000
    retries: int = 0
    max_response_bytes: int = 2_000_000

    def __post_init__(self) -> None:
        if any(
            type(getattr(self, f.name)) is not type(f.default) or getattr(self, f.name) != f.default
            for f in fields(self)
        ):
            raise ValueError("unsupported probe plan; review a new version")

    @property
    def data(self) -> JsonObject:
        return cast(JsonObject, asdict(self))

    @property
    def reservation(self) -> int:
        return (
            self.context_input_reserve * self.input_rate + self.max_output_tokens * self.output_rate
        )


def binding(run_id: str, registry: Path, corpus: JsonObject) -> JsonObject:
    uuid_(run_id)
    if registry.name != "probe-runs.sqlite3":
        raise ValueError("dedicated probe-runs.sqlite3 registry required")
    if corpus.get("source_identity") != source_identity():
        raise ValueError("source binding mismatch")
    requests = rows(corpus["requests"])
    if len(requests) != ProbePlan().request_limit:
        raise ValueError("unexpected probe allocation count")
    return {
        "protocol": PROTOCOL,
        "transport_diagnostic_version": DIAGNOSTIC_VERSION,
        "run_id": run_id,
        "registry": str(registry.resolve()),
        "source_digest": source_identity(),
        "corpus_digest": corpus["corpus_digest"],
        "schedule_digest": state_digest({"requests": corpus["requests"]}),
        "plan": ProbePlan().data,
        "endpoint": ENDPOINT,
        "allocation_policy": (
            "frozen-corpus-order; independent branches; no retries or replacements"
        ),
        "failure_policy": "continue-ready-incomplete-refusal; otherwise-stop",
    }


@dataclass(frozen=True, slots=True)
class ProbeApproval:
    """Trusted local operator assertion, not cryptographic human authentication."""

    approval_id: str
    approved_by: str
    decision: str
    scope: tuple[str, ...]
    bound: JsonObject

    def validate(self, expected: JsonObject, *, synthetic: bool) -> None:
        try:
            uuid_(self.approval_id)
            if (
                self.approved_by != ("offline-fixture" if synthetic else "human-user")
                or self.decision
                != ("synthetic-probe-only" if synthetic else "approve-one-paid-probe")
                or self.scope
                != (("recording-only",) if synthetic else ("read-openai-api-key", "paid-responses"))
                or not same(self.bound, expected)
            ):
                raise ValueError
        except (ValueError, TypeError, AttributeError, RecursionError):
            raise PermissionError("absent, synthetic or mismatched probe approval") from None


def load_approval(path: Path) -> ProbeApproval:
    try:
        if path.stat().st_size > 65_536:
            raise ValueError
        value = strict_json(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != {
            "approval_id",
            "approved_by",
            "decision",
            "scope",
            "bound",
        }:
            raise ValueError
        if any(type(value[k]) is not str for k in ("approval_id", "approved_by", "decision")):
            raise ValueError
        if not isinstance(value["scope"], list) or any(type(v) is not str for v in value["scope"]):
            raise ValueError
        return ProbeApproval(
            str(value["approval_id"]),
            str(value["approved_by"]),
            str(value["decision"]),
            tuple(cast(list[str], value["scope"])),
            object_(value["bound"]),
        )
    except (OSError, ValueError, TypeError, RecursionError):
        raise PermissionError("invalid probe approval file") from None


def classify(packet: JsonObject, seen: set[str]) -> JsonObject:
    """Only matching model/tier with valid bounded usage can release a reservation."""
    result: JsonObject = {
        "classification": "INVALID_RESPONSE",
        "usage": None,
        "cost_nano_usd": None,
        "response_id": None,
    }
    raw = packet["raw_response"]
    if raw is None:
        return result | {"classification": "TRANSPORT_UNCERTAIN"}
    try:
        if not isinstance(raw, str) or len(raw.encode()) > ProbePlan().max_response_bytes:
            return result | {"classification": "RESPONSE_TOO_LARGE"}
        doc = strict_json(raw)
        if not isinstance(doc, dict):
            return result
        usage = Usage.parse(doc.get("usage"))
        result["usage"] = cast(JsonObject, asdict(usage))
        if packet["http_status"] != 200:
            return result | {"classification": "HTTP_ERROR"}
        if doc.get("model") != MODEL or doc.get("service_tier") != "default":
            return result | {"classification": "IDENTITY_MISMATCH"}
        if usage.input_tokens > 400_000 or usage.output_tokens > 8192:
            return result | {"classification": "USAGE_EXCEEDS_RESERVATION"}
        rid = doc.get("id")
        if type(rid) is not str or not rid:
            return result | {"classification": "INVALID_RESPONSE_ID"}
        if rid in seen:
            return result | {"classification": "DUPLICATE_RESPONSE_ID"}
        seen.add(rid)
        result["response_id"] = rid
        result["cost_nano_usd"] = (
            (usage.input_tokens - usage.cached_tokens) * 750
            + usage.cached_tokens * 75
            + usage.output_tokens * 4500
        )
        # Completion time, not poll time. Late usage is priced but no action is delivered.
        if cast(int, packet["elapsed_ms"]) > ProbePlan().timeout_ms:
            return result | {"classification": "TIMEOUT"}
        if doc.get("status") == "incomplete":
            return result | {"classification": "INCOMPLETE"}
        if doc.get("status") != "completed":
            return result | {"classification": "RESPONSE_FAILED"}
        text, refusal = response_text(doc)
        if len(text.encode()) > 16_384:
            return result | {"classification": "OUTPUT_TOO_LARGE"}
        return result | {"classification": "REFUSAL" if refusal else "READY"}
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return result


@dataclass(slots=True)
class Ledger:
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    known_cost_nano_usd: int = 0
    reserved_cost_nano_usd: int = 0

    @property
    def data(self) -> JsonObject:
        return cast(JsonObject, asdict(self))

    def blocked(self) -> str | None:
        p = ProbePlan()
        if self.reserved_cost_nano_usd:
            return "UNRESOLVED_RESERVATION"
        if self.attempts >= p.request_limit:
            return "REQUEST_LIMIT"
        if self.input_tokens + p.context_input_reserve > p.total_input_tokens:
            return "INPUT_TOKEN_LIMIT"
        if self.output_tokens + p.max_output_tokens > p.total_output_tokens:
            return "OUTPUT_TOKEN_LIMIT"
        if self.known_cost_nano_usd + p.reservation > p.cost_limit_nano_usd:
            return "COST_LIMIT"
        return None

    def reserve(self) -> None:
        if self.blocked():
            raise ValueError(self.blocked())
        self.attempts += 1
        self.reserved_cost_nano_usd = ProbePlan().reservation

    def settle(self, result: JsonObject) -> None:
        if not self.reserved_cost_nano_usd:
            raise ValueError("missing reservation")
        cost = result["cost_nano_usd"]
        if cost is not None:
            usage = object_(result["usage"])
            self.input_tokens += cast(int, usage["input_tokens"])
            self.output_tokens += cast(int, usage["output_tokens"])
            self.known_cost_nano_usd += cast(int, cost)
            self.reserved_cost_nano_usd = 0
