"""One bounded decision worker; canonical work stays on the owner thread."""

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from time import monotonic
from typing import Any, cast

from journeymap.adapters.life_http import LifeHTTPProvider, PaidFailure, paid_body, paid_request
from journeymap.adapters.life_llm import (
    LifeLLMConfig,
    life_body,
    life_candidate,
    life_request,
    offline_provider,
    raw_record,
)
from journeymap.adapters.llm import Record
from journeymap.adapters.provider import (
    Provider,
    ProviderFailure,
    ProviderRequest,
    RawModelResponse,
    provider_identity,
    response_identity_matches,
)
from journeymap.application.concurrent import ConcurrentApplication, ConcurrentReceipt
from journeymap.core.canonical import JsonObject, canonical_json
from journeymap.core.handlers import ActionRequest, ActionValidationError
from journeymap.core.observations import Observation


@dataclass(frozen=True, slots=True)
class LifeCall:
    sequence: int
    tick: int
    actor_id: str
    observation_id: str
    request: ProviderRequest
    body: JsonObject
    sent: bool
    raw: Record | None = None
    candidate: JsonObject | None = None
    action: ActionRequest | None = None
    receipt: ConcurrentReceipt | None = None
    final_result: ConcurrentReceipt | None = None
    status: str = "PENDING"
    error: str | None = None
    discarded_late: bool = False


@dataclass(slots=True)
class LifeLLMState:
    calls: list[LifeCall] = field(default_factory=list)
    requests_started: int = 0
    fixture_position: int = 0
    invalid_streak: int = 0
    status: str = "IDLE"
    observation: Observation | None = None
    ready: Record | None = None


class LifeDecisionService:
    def __init__(
        self,
        config: LifeLLMConfig,
        provider: Provider | None = None,
        *,
        horizon: int = 24,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._live = type(provider) is LifeHTTPProvider
        if self._live:
            assert isinstance(provider, LifeHTTPProvider)
            plan = provider.plan
            if (
                not provider.admitted
                or provider.closed
                or provider.session_attached
                or horizon != plan.horizon
                or config.actor != plan.actor
                or config.hold_responses
                or config.request_limit != plan.request_limit
                or config.max_output_tokens != plan.max_output_tokens
                or config.max_input_bytes != plan.max_input_bytes
                or config.max_output_bytes != plan.max_output_bytes
                or config.invalid_limit != plan.invalid_limit
                or config.timeout_seconds != plan.timeout_seconds
            ):
                raise PermissionError("approved life plan/session mismatch")
            provider.journal.assert_current()
            provider.session_attached = True
        elif provider is not None and provider_identity(provider)["kind"] != "fixture":
            raise PermissionError("only offline or approved life providers are authorized")
        self.config = config
        self.horizon = horizon
        self._clock = clock
        self.state = LifeLLMState()
        self._override = provider
        self._queue: queue.SimpleQueue[tuple[int, float, RawModelResponse | None, str | None]] = (
            queue.SimpleQueue()
        )
        self._mailbox_lock = threading.Lock()
        self._signal = threading.Event()
        self._gate = threading.Event()
        self._started_at = 0.0
        self._completed_at: float | None = None
        self._unsettled = False
        self._closed = False

    @property
    def in_flight(self) -> bool:
        return self._unsettled

    def can_begin(self) -> bool:
        if self._closed or self.state.status in {"PAUSED_ERROR", "LIMIT_REACHED"}:
            return False
        if self.state.requests_started >= self.config.request_limit:
            self.state.status = "LIMIT_REACHED"
            return False
        return True

    def _update(self, **changes: Any) -> None:
        self.state.calls[-1] = replace(self.state.calls[-1], **changes)

    def begin(self, observation: Observation) -> None:
        if self.state.status != "IDLE" or self.in_flight or not self.can_begin():
            raise ValueError("decision opportunity is unavailable")
        if isinstance(self._override, LifeHTTPProvider):
            request = paid_request(observation, self._override.plan)
            body = paid_body(request)
        else:
            request = life_request(observation, self.config.max_output_tokens, self.horizon)
            body = life_body(request)
        sequence = len(self.state.calls) + 1
        sent = len(canonical_json(body).encode("utf-8")) <= self.config.max_input_bytes
        self.state.calls.append(
            LifeCall(
                sequence,
                observation.simulation_time,
                observation.actor_id,
                observation.observation_id,
                request,
                body,
                sent,
            )
        )
        if not sent:
            self._update(status="ERROR", error="INPUT_TOO_LARGE")
            self.state.status = "PAUSED_ERROR"
            return
        if not self._live and self.state.fixture_position >= len(self.config.responses):
            self._update(sent=False, status="ERROR", error="FIXTURE_EXHAUSTED")
            self.state.status = "PAUSED_ERROR"
            return
        if self.config.hold_responses:
            self._gate.clear()
        if self._override is not None:
            provider = self._override
        else:
            response = self.config.responses[self.state.fixture_position]
            provider = offline_provider(
                response, gate=self._gate if self.config.hold_responses else None
            )
        self.state.requests_started += 1
        self.state.fixture_position += 1
        self.state.observation = observation
        self.state.status = "WAITING_FOR_DECISION"
        self._started_at = self._clock()
        if isinstance(provider, LifeHTTPProvider):
            provider.decision_deadline = self._started_at + self.config.timeout_seconds
        self._completed_at = None
        self._unsettled = True
        self._signal.clear()

        def worker() -> None:
            result, error = None, None
            try:
                result = provider.generate(request)
                if not response_identity_matches(
                    provider_identity(provider), request.model, result.metadata
                ):
                    result, error = None, "IDENTITY_MISMATCH"
            except ProviderFailure as failure:
                error = failure.kind
            except PaidFailure as failure:
                error = failure.reason
            except Exception:
                error = "TRANSPORT_ERROR"
            # Completion means the finalized Provider result is published.
            # Timestamp + publication are atomic relative to an empty timeout
            # poll, so a timely completion cannot be hidden between the two.
            with self._mailbox_lock:
                completed_at = self._clock()
                self._queue.put((sequence, completed_at, result, error))
                self._signal.set()

        threading.Thread(target=worker, name="life-offline-decision", daemon=True).start()

    def _fail(self, error: str, *, raw: Record | None = None, late: bool = False) -> None:
        self._update(
            status="ERROR",
            error=error,
            raw=raw if raw is not None else self.state.calls[-1].raw,
            discarded_late=late or self.state.calls[-1].discarded_late,
        )
        self.state.observation, self.state.ready = None, None
        self.state.status = "PAUSED_ERROR"

    def poll(self) -> None:
        if self._closed:
            return
        deadline = self._started_at + self.config.timeout_seconds
        with self._mailbox_lock:
            try:
                sequence, completed_at, response, error = self._queue.get_nowait()
            except queue.Empty:
                if (
                    self._unsettled
                    and self.state.status == "WAITING_FOR_DECISION"
                    and self._clock() > deadline
                ):
                    self._fail("TIMEOUT")
                return
        self._unsettled = False
        self._completed_at = completed_at
        raw = raw_record(response) if response is not None else None
        if sequence != len(self.state.calls) or self.state.status != "WAITING_FOR_DECISION":
            self._update(raw=raw, discarded_late=True)
            return
        # A completion exactly at the deadline is timely. Owner poll time
        # cannot invalidate a timely queued result or admit a late one.
        if completed_at > deadline:
            self._fail("TIMEOUT", raw=raw, late=True)
            return
        if error is not None or response is None:
            self._fail(error or "TRANSPORT_ERROR")
            return
        self.state.ready = raw_record(response)
        self._update(raw=self.state.ready, status="READY")
        self.state.status = "READY"

    def resolve(self) -> ActionRequest | None:
        if (
            self.state.status != "READY"
            or self.state.ready is None
            or self.state.observation is None
        ):
            raise ValueError("no received decision to process")
        observation, raw = self.state.observation, self.state.ready.data
        metadata = cast(JsonObject, raw["metadata"])
        error = None
        text = str(raw["text"])
        if metadata.get("refusal") is True:
            error = "REFUSAL"
        elif metadata.get("status", "completed") != "completed":
            error = "INCOMPLETE"
        try:
            if len(text.encode("utf-8")) > self.config.max_output_bytes:
                error = "OUTPUT_TOO_LARGE"
            if error is not None:
                raise ValueError(error)
            candidate, request = life_candidate(text, observation)
        except RecursionError:
            self._fail("JSON_DEPTH_EXCEEDED")
            return None
        except (ValueError, TypeError, ActionValidationError):
            self._fail(error or "INVALID_CANDIDATE")
            return None
        self.state.observation, self.state.ready = None, None
        self._update(candidate=candidate, action=request, status="PARSED")
        self.state.status = "IDLE"
        return request

    def accepted(self, receipt: ConcurrentReceipt) -> None:
        self._update(receipt=receipt, status=receipt.status)
        if receipt.status == "REJECTED":
            self._invalid()

    def _invalid(self) -> None:
        self.state.invalid_streak += 1
        if self.state.invalid_streak >= self.config.invalid_limit:
            self.state.status = "LIMIT_REACHED"

    def refresh_results(self, app: ConcurrentApplication) -> None:
        for index, call in enumerate(self.state.calls):
            if call.status != "ACCEPTED" or call.action is None:
                continue
            result = app.game_for(call.actor_id).result(call.action.action_request_id)
            if result is not None and result.status in {"SUCCEEDED", "FAILED"}:
                self.state.calls[index] = replace(call, final_result=result, status=result.status)
                if result.status == "FAILED":
                    self._invalid()
                else:
                    self.state.invalid_streak = 0

    def checkpoint_ready(self) -> None:
        if self.in_flight or self._override is not None or self._closed:
            raise ValueError(
                "external request in flight/uncertain or custom provider is not checkpointable"
            )

    def wait(self) -> None:
        self._signal.wait(0.01)

    def close(self) -> None:
        self._closed = True
        self._gate.set()

    def release_response(self) -> None:
        if not self.config.hold_responses or not self.in_flight:
            raise ValueError("no held offline response")
        self._gate.set()
