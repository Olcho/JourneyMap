"""Separate concurrent-v1 actor port; reuse existing Observation/claim authority."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from journeymap.application.contracts import normalize_request, request_fingerprint
from journeymap.application.session import SimulationApplication
from journeymap.application.social import validate_claim
from journeymap.core.canonical import JsonObject, clone_json_object
from journeymap.core.concurrent import ConcurrentKernel
from journeymap.core.controller import Controller, GameSubmissionError
from journeymap.core.handlers import (
    ActionHandlerNotFoundError,
    ActionRequest,
    ActionValidationError,
)
from journeymap.core.observations import Observation
from journeymap.modules.social.contracts import validate_payload


@dataclass(frozen=True, slots=True)
class ConcurrentReceipt:
    action_request_id: str
    actor_id: str
    status: str
    reason_code: str | None
    started_at: int
    completes_at: int | None
    resolved_at: int | None = None
    schema_version: int = 2


@dataclass(frozen=True, slots=True)
class ConcurrentAttempt:
    sequence: int
    request: ActionRequest
    receipt: ConcurrentReceipt | None
    error_code: str | None = None

    def detached(self) -> "ConcurrentAttempt":
        return replace(self, request=self.request.detached())


@dataclass(frozen=True, slots=True)
class ConcurrentGamePort:
    _observe: Callable[[], Observation] = field(repr=False)
    _submit: Callable[[ActionRequest], ConcurrentReceipt] = field(repr=False)
    _result: Callable[[str], ConcurrentReceipt | None] = field(repr=False)

    def observe(self) -> Observation:
        return self._observe()

    def submit(self, request: ActionRequest) -> ConcurrentReceipt:
        return self._submit(request)

    def result(self, request_id: str) -> ConcurrentReceipt | None:
        return self._result(request_id)


class ConcurrentApplication:
    """Compose exactly once with an Observation authority for this kernel.

    The synchronous authority's submit is disabled by ConcurrentKernel itself.
    Only the bound port is granted to trusted callers; policies receive Observation.
    """

    def __init__(
        self, kernel: ConcurrentKernel, authority: SimulationApplication, *, social: bool = False
    ) -> None:
        if authority._kernel is not kernel:
            raise ValueError("Observation authority belongs to another kernel")
        self._kernel = kernel
        self._authority = authority
        self._social = social
        self._requests: dict[str, tuple[str, str, ConcurrentReceipt | None]] = {}
        self._busy = False
        self._attempts: list[ConcurrentAttempt] = []
        self._intents: dict[str, ActionRequest] = {}

    @property
    def attempts(self) -> tuple[ConcurrentAttempt, ...]:
        return tuple(a.detached() for a in self._attempts)

    def game_for(self, actor: str) -> ConcurrentGamePort:
        observe = self._authority.game_for(actor).observe
        return ConcurrentGamePort(
            observe, lambda r: self._submit(actor, r), lambda rid: self._result(actor, rid)
        )

    def latest_public_result(self, actor: str) -> JsonObject | None:
        """Trusted perception query; expose only this actor's sanitized receipt."""
        attempt = next(
            (
                a
                for a in reversed(self._attempts)
                if a.receipt is not None and a.receipt.actor_id == actor
            ),
            None,
        )
        if attempt is None:
            return None
        receipt = attempt.receipt
        assert receipt is not None
        if receipt.status == "ACCEPTED":
            receipt = self._result(actor, attempt.request.action_request_id) or receipt
        return clone_json_object(
            {
                "request_id": receipt.action_request_id,
                "action_type": attempt.request.action_type,
                "payload": attempt.request.payload,
                "status": receipt.status,
                "reason_code": receipt.reason_code,
                "started_at": receipt.started_at,
                "resolved_at": receipt.resolved_at,
            }
        )

    def _result(self, actor: str, request_id: str) -> ConcurrentReceipt | None:
        entry = self._requests.get(request_id)
        if entry is None or entry[0] != actor:
            return None
        receipt = entry[2]
        if receipt is None or receipt.status != "ACCEPTED":
            return receipt
        result = next(
            (r for r in self._kernel.action_results if r.action_request_id == request_id), None
        )
        if result is None:
            return receipt
        request = self._intents[request_id]
        visible = self._authority._public_result(actor, request, result)
        return ConcurrentReceipt(
            request_id,
            actor,
            str(visible.status),
            visible.reason_code,
            visible.started_at,
            receipt.completes_at,
            visible.resolved_at,
        )

    def _submit(self, actor: str, value: ActionRequest) -> ConcurrentReceipt:
        request = normalize_request(value)
        try:
            receipt = self._submit_once(actor, request)
        except GameSubmissionError as error:
            self._attempts.append(
                ConcurrentAttempt(len(self._attempts) + 1, request, None, str(error))
            )
            raise
        self._attempts.append(ConcurrentAttempt(len(self._attempts) + 1, request, receipt))
        self._intents.setdefault(request.action_request_id, request.detached())
        return receipt

    def _submit_once(self, actor: str, value: ActionRequest) -> ConcurrentReceipt:
        if self._busy or not self._kernel.is_booted:
            raise GameSubmissionError("GAME_UNAVAILABLE")
        self._busy = True
        try:
            request = normalize_request(value)
            fingerprint = request_fingerprint(request)
            previous = self._requests.get(request.action_request_id)
            now = self._kernel.simulation_time

            def denied(reason: str) -> ConcurrentReceipt:
                return ConcurrentReceipt(
                    request.action_request_id, actor, "REJECTED", reason, now, None, now
                )

            if previous is not None:
                if previous[:2] != (actor, fingerprint):
                    return denied("REQUEST_ID_CONFLICT")
                if previous[2] is None:
                    raise GameSubmissionError("ENGINE_ERROR")
                return previous[2]
            self._requests[request.action_request_id] = (actor, fingerprint, None)
            reason = self._authority._authority_reason(actor, request)
            if reason is None and request.action_type in {"ASK", "INFORM", "REQUEST"}:
                if not self._social:
                    reason = "SOCIAL_UNAVAILABLE"
                else:
                    try:
                        validate_payload(request.action_type, request.payload)
                        if request.action_type == "INFORM":
                            observation = next(
                                o
                                for o in self._authority.observation_history
                                if o.observation_id == request.based_on_observation_id
                            )
                            validate_claim(
                                request,
                                observation,
                                self._authority.knowledge_snapshot().for_actor(actor),
                                self._kernel.events,
                            )
                    except ActionValidationError as error:
                        reason = error.reason_code
                    except Exception:
                        del self._requests[request.action_request_id]
                        raise GameSubmissionError("KNOWLEDGE_UNAVAILABLE") from None
            if reason is not None:
                receipt = denied(reason)
            else:
                try:
                    admission = self._kernel.accept_action(request)
                except ActionHandlerNotFoundError:
                    receipt = denied("UNKNOWN_ACTION")
                except Exception:
                    raise GameSubmissionError("ENGINE_ERROR") from None
                else:
                    receipt = ConcurrentReceipt(
                        admission.request_id,
                        actor,
                        admission.status,
                        admission.reason,
                        admission.started_at,
                        admission.completes_at,
                        now if admission.status == "REJECTED" else None,
                    )
                    if admission.status == "REJECTED":
                        result = self._kernel.action_results[-1]
                        public = self._authority._public_result(actor, request, result)
                        receipt = denied(public.reason_code or "ACTION_REJECTED")
            self._requests[request.action_request_id] = (actor, fingerprint, receipt)
            return receipt
        finally:
            self._busy = False


@dataclass(frozen=True, slots=True)
class Activation:
    tick: int
    actor_id: str
    request_id: str | None
    status: str


class AutonomousRunner:
    """Finite integer-tick activations, at most one decision per actor per tick."""

    def __init__(
        self,
        kernel: ConcurrentKernel,
        application: ConcurrentApplication,
        controllers: dict[str, Controller],
    ) -> None:
        self._kernel = kernel
        self._application = application
        self._controllers = dict(controllers)
        self._next_tick = {actor: kernel.simulation_time for actor in controllers}
        self._activations: list[Activation] = []
        self._cursor = 0

    @property
    def activations(self) -> tuple[Activation, ...]:
        return tuple(self._activations)

    def run_until(self, horizon: int) -> tuple[Activation, ...]:
        if type(horizon) is not int or horizon < self._kernel.simulation_time:
            raise ValueError("invalid finite horizon")
        for tick in range(self._kernel.simulation_time, horizon + 1):
            self._kernel.advance_to(tick)
            if tick == horizon:
                break
            actors = sorted(self._controllers)
            if actors:
                pivot = self._cursor % len(actors)
                actors = actors[pivot:] + actors[:pivot]
                self._cursor += 1
            busy = {f.request.actor_id for f in self._kernel.in_flight}
            for actor in actors:
                if actor in busy or self._next_tick[actor] > tick:
                    continue
                game = self._application.game_for(actor)
                request_id = None
                try:
                    observation = game.observe()
                    request = normalize_request(self._controllers[actor].decide(observation))
                    if (
                        request.run_id,
                        request.actor_id,
                        request.based_on_observation_id,
                        request.submitted_at,
                    ) != (observation.run_id, actor, observation.observation_id, tick):
                        raise ValueError("Controller output does not bind to its Observation")
                    request_id = request.action_request_id
                    receipt = game.submit(request)
                    status = receipt.status
                except Exception:
                    status = "DECISION_OR_SUBMISSION_ERROR"
                self._activations.append(Activation(tick, actor, request_id, status))
                self._next_tick[actor] = tick + 1
                # Instantaneous completions drain before the next admission.
                self._kernel.advance_to(tick)
        return self.activations
