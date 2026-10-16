"""Approved life-only HTTPS composition. No retry, redirects, proxy or SDK."""

import hashlib
import http.client
import json
import os
import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, replace
from pathlib import Path
from time import monotonic
from typing import Protocol, cast

from journeymap.adapters.life_admission import (
    PROTOCOL,
    ExecutionApproval,
    JournalFailure,
    RunJournal,
    binding,
)
from journeymap.adapters.life_llm import PROMPT, PROMPT_VERSION, life_body, life_request
from journeymap.adapters.life_responses import (
    Budget,
    LifeExecutionPlan,
    PreflightStop,
    Usage,
    response_text,
)
from journeymap.adapters.llm import strict_json
from journeymap.adapters.provider import ProviderIdentity, ProviderRequest, RawModelResponse
from journeymap.core.canonical import JsonObject, canonical_json
from journeymap.core.observations import Observation


class Response(Protocol):
    status: int

    def read1(self, size: int) -> bytes: ...
    def isclosed(self) -> bool: ...


class Connection(Protocol):
    def request(self, method: str, url: str, body: bytes, headers: dict[str, str]) -> None: ...
    def getresponse(self) -> Response: ...
    def close(self) -> None: ...


def real_credential() -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("credential unavailable")
    return key


def real_connection(host: str, timeout: float) -> Connection:
    return cast(Connection, http.client.HTTPSConnection(host, timeout=timeout))


class PaidFailure(RuntimeError):
    """Safe allowlisted reason, never an underlying exception or response body."""

    def __init__(self, reason: str) -> None:
        allowed = {
            "ADMISSION_MISMATCH",
            "JOURNAL_UNAVAILABLE",
            "INPUT_TOO_LARGE",
            "REQUEST_LIMIT",
            "INPUT_TOKEN_LIMIT",
            "OUTPUT_TOKEN_LIMIT",
            "COST_LIMIT",
            "REQUEST_UNCERTAIN",
            "CREDENTIAL_UNAVAILABLE",
            "SECRET_ECHO",
            "TRANSPORT_UNCERTAIN",
            "HTTP_ERROR",
            "RESPONSE_TOO_LARGE",
            "INVALID_RESPONSE",
            "USAGE_UNAVAILABLE",
            "INVALID_USAGE",
            "USAGE_EXCEEDS_RESERVATION",
            "IDENTITY_MISMATCH",
            "SERVICE_TIER_MISMATCH",
            "INVALID_RESPONSE_ID",
            "RESPONSE_FAILED",
            "OUTPUT_TOO_LARGE",
            "UNEXPECTED_OUTPUT",
            "RUN_CLOSED",
            "REFUSAL",
            "INCOMPLETE",
            "TIMEOUT",
        }
        self.reason = reason if reason in allowed else "INVALID_RESPONSE"
        super().__init__(self.reason)


def paid_request(observation: Observation, plan: LifeExecutionPlan) -> ProviderRequest:
    if observation.actor_id != plan.actor:
        raise ValueError("approved actor mismatch")
    return replace(
        life_request(observation, plan.max_output_tokens, plan.horizon),
        model=plan.model,
        configuration_version=PROTOCOL,
    )


def paid_body(request: ProviderRequest) -> JsonObject:
    body = life_body(request)
    body["service_tier"] = "default"
    return body


def parsed_response(document: JsonObject) -> RawModelResponse:
    text, refusal = response_text(document)
    return RawModelResponse(
        text,
        {
            "provider": "openai",
            "adapter_version": PROTOCOL,
            "model": document["model"],
            "service_tier": document["service_tier"],
            "response_id": document["id"],
            "usage": document["usage"],
            "status": document["status"],
            "refusal": refusal,
            "incomplete_details": document.get("incomplete_details"),
        },
    )


class LifeHTTPProvider:
    name = "openai"
    version = PROTOCOL

    def __init__(
        self,
        journal: RunJournal,
        *,
        credential: Callable[[], str] = real_credential,
        connection: Callable[[str, float], Connection] = real_connection,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        journal.assert_current()
        if getattr(journal, "provider_claimed", False):
            raise PermissionError("run already has a Provider")
        journal.provider_claimed = True
        self.journal = journal
        self.plan = LifeExecutionPlan()
        self.budget = Budget(self.plan)
        self._credential, self._connection, self._clock = credential, connection, clock
        self.admitted = True
        self.closed = False
        self.session_attached = False
        self.decision_deadline: float | None = None
        self._owner_stopped = threading.Event()
        self.cost_state = "NO_USAGE"
        self.reported_usage: list[JsonObject] = []

    @property
    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(self.name, self.version, "live", self.plan.timeout_seconds)

    def accounting(self) -> JsonObject:
        return cast(
            JsonObject,
            {
                **asdict(self.budget),
                "cost_state": self.cost_state,
                "reported_usage": self.reported_usage.copy(),
                "reserved_cost_is_conditional": True,
                "unknown_model_or_tier_cost_bounded": False,
                "rate_card_cost_is_invoice": False,
            },
        )

    def owner(self, stage: str, data: JsonObject) -> None:
        if stage == "OWNER_TIMEOUT":
            self._owner_stopped.set()
        try:
            self.journal.append(stage, data)
        except JournalFailure:
            self.budget.stop = "JOURNAL_UNAVAILABLE"
            raise PaidFailure("JOURNAL_UNAVAILABLE") from None

    def generate(self, request: ProviderRequest) -> RawModelResponse:
        if self.closed:
            raise PaidFailure("RUN_CLOSED")
        deadline = self.decision_deadline or self._clock() + self.plan.timeout_seconds
        try:
            self.journal.assert_current()
        except PermissionError:
            raise PaidFailure("ADMISSION_MISMATCH") from None
        if (
            request.model != self.plan.model
            or request.configuration_version != PROTOCOL
            or request.prompt_version != PROMPT_VERSION
            or not request.prompt.startswith(PROMPT + "\n")
            or request.parameters
            != {
                "max_output_tokens": self.plan.max_output_tokens,
                "reasoning": {"effort": self.plan.reasoning},
            }
        ):
            raise PaidFailure("ADMISSION_MISMATCH")
        body = paid_body(request)
        try:
            self.budget.reserve(body)
        except PreflightStop as error:
            self.owner(
                "RESERVATION_BLOCKED",
                {
                    "sequence": self.budget.attempts + 1,
                    "request_digest": hashlib.sha256(
                        canonical_json(cast(JsonObject, asdict(request))).encode()
                    ).hexdigest(),
                    "reason": str(error),
                    "accounting": self.accounting(),
                },
            )
            raise PaidFailure(str(error)) from None
        seq = self.budget.attempts
        common: JsonObject = {
            "sequence": seq,
            "request_digest": hashlib.sha256(
                canonical_json(cast(JsonObject, asdict(request))).encode()
            ).hexdigest(),
        }
        conn: Connection | None = None
        attempted = False
        try:
            # FULL transaction must commit before credentials or connection code.
            self.owner("PREPARED_NOT_SENT", common | {"accounting": self.accounting()})
            try:
                key = self._credential()
                if type(key) is not str or not key or "\r" in key or "\n" in key:
                    raise ValueError
            except Exception:
                raise PaidFailure("CREDENTIAL_UNAVAILABLE") from None
            encoded = canonical_json(body)
            if key in encoded:
                raise PaidFailure("SECRET_ECHO")
            if self.closed or self._owner_stopped.is_set() or self._clock() > deadline:
                raise PaidFailure("TIMEOUT")
            self.owner(
                "ATTEMPTED", common | {"request": cast(JsonObject, asdict(request)), "body": body}
            )
            attempted = True
            if self.closed or self._owner_stopped.is_set() or self._clock() > deadline:
                raise PaidFailure("TIMEOUT")
            started = self._clock()
            conn = self._connection("api.openai.com", self.plan.timeout_seconds)
            conn.request(
                "POST",
                "/v1/responses",
                encoded.encode("utf-8"),
                {"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            )
            response = conn.getresponse()
            # No response headers are exported. 3xx is an error, never followed.
            if response.status != 200:
                self.owner("HTTP_RECEIVED", common | {"http_status": response.status})
                raise PaidFailure("HTTP_ERROR")
            parts: list[bytes] = []
            size = 0
            while True:
                part = response.read1(min(65_536, 2_000_001 - size))
                if not part:
                    break
                parts.append(part)
                size += len(part)
                if size > 2_000_000:
                    raise PaidFailure("RESPONSE_TOO_LARGE")
                if response.isclosed():
                    break  # Fully received late replies still need usage recovery.
                if self._clock() - started > self.plan.timeout_seconds:
                    raise PaidFailure("TIMEOUT")
            received = b"".join(parts)
            self.owner(
                "RESPONSE_ENVELOPE_RECEIVED",
                common
                | {
                    "http_status": 200,
                    "bytes": len(received),
                    "sha256": hashlib.sha256(received).hexdigest(),
                },
            )
            try:
                raw = received.decode("utf-8")
            except UnicodeError:
                raise PaidFailure("INVALID_RESPONSE") from None
            # Invalid JSON is withheld: arbitrary malformed escapes cannot be
            # proven secret-free. Valid JSON is scanned after normalization.
            if key in raw or json.dumps(key)[1:-1] in raw:
                raise PaidFailure("SECRET_ECHO")
            try:
                document = strict_json(raw)
                if not isinstance(document, dict):
                    raise ValueError
                if key in canonical_json(document):
                    raise PaidFailure("SECRET_ECHO")
            except PaidFailure:
                raise
            except (ValueError, TypeError, RecursionError):
                raise PaidFailure("INVALID_RESPONSE") from None
            self.owner("RESPONSE_RECEIVED", common | {"http_status": 200, "raw": raw})
            try:
                usage = Usage.parse(document.get("usage"))
            except ValueError as error:
                raise PaidFailure(str(error)) from None
            usage_data = cast(JsonObject, asdict(usage))
            self.reported_usage.append(usage_data)
            self.owner("USAGE_CHECKED", common | {"usage": usage_data})
            # Identity must precede pricing. Tokens alone do not justify rates.
            if document.get("model") != self.plan.model:
                raise PaidFailure("IDENTITY_MISMATCH")
            if document.get("service_tier") != self.plan.service_tier:
                raise PaidFailure("SERVICE_TIER_MISMATCH")
            try:
                self.budget.settle(usage)
            except PreflightStop as error:
                raise PaidFailure(str(error)) from None
            self.cost_state = "KNOWN_RATE_CARD"
            self.owner("USAGE_PRICED", common | {"accounting": self.accounting()})
            if type(document.get("id")) is not str or not document["id"]:
                raise PaidFailure("INVALID_RESPONSE_ID")
            if document.get("status") not in {"completed", "incomplete"}:
                raise PaidFailure("RESPONSE_FAILED")
            try:
                text, refusal = response_text(document)
            except ValueError as error:
                raise PaidFailure(str(error)) from None
            if len(text.encode("utf-8")) > self.plan.max_output_bytes:
                raise PaidFailure("OUTPUT_TOO_LARGE")
            if refusal or document["status"] != "completed":
                self.budget.stop = "REFUSAL" if refusal else "INCOMPLETE"
            self.owner("RESPONSE_DELIVERY", common | {"owner_closed": self.closed})
            return parsed_response(document)
        except Exception as error:
            reason = error.reason if isinstance(error, PaidFailure) else "TRANSPORT_UNCERTAIN"
            if isinstance(error, JournalFailure):
                reason = "JOURNAL_UNAVAILABLE"
            if self.budget.reserved_cost:
                self.cost_state = "UNCERTAIN" if attempted else "NOT_SENT"
                if not attempted:
                    # The connection factory was never reached. This is known
                    # not-sent, unlike a partial send or absent response.
                    self.budget.reserved_input = self.budget.reserved_output = (
                        self.budget.reserved_cost
                    ) = 0
            self.budget.stop = reason
            # On storage failure no further write may be assumed to succeed.
            try:
                self.journal.append(
                    "REQUEST_ERROR" if attempted else "NOT_SENT",
                    common
                    | {
                        "reason": reason,
                        "accounting": self.accounting(),
                        "raw_withheld_if_unsafe": True,
                    },
                )
            except JournalFailure:
                reason = self.budget.stop = "JOURNAL_UNAVAILABLE"
            raise PaidFailure(reason) from None
        finally:
            if conn is not None:
                with suppress(Exception):
                    conn.close()


def admit_provider(
    run_id: str,
    approval: ExecutionApproval | None,
    registry: Path,
    *,
    credential: Callable[[], str] = real_credential,
    connection: Callable[[str, float], Connection] = real_connection,
    clock: Callable[[], float] = monotonic,
) -> LifeHTTPProvider:
    if approval is None:
        raise PermissionError("explicit human approval required")
    journal = RunJournal(registry, binding(run_id, registry), approval)
    return LifeHTTPProvider(journal, credential=credential, connection=connection, clock=clock)
