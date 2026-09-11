"""Live Game authority and research tracing; engine replay bypasses this layer."""

from dataclasses import dataclass, replace

from journeymap.application.contracts import normalize_request, request_fingerprint
from journeymap.application.observations import ObservationHistory, ObservationPipeline
from journeymap.application.perception import PerceptionExtension, perceive
from journeymap.application.reasons import PRIVATE_PURCHASE_REASONS, VISIBLE_DOMAIN_REASONS
from journeymap.application.social import validate_claim
from journeymap.core.controller import ControllerActionResult, GamePort, GameSubmissionError
from journeymap.core.entities import get_entity
from journeymap.core.handlers import (
    ActionHandlerNotFoundError,
    ActionRequest,
    ActionResult,
    ActionStatus,
    ActionValidationError,
)
from journeymap.core.kernel import SimulationKernel
from journeymap.core.observations import Observation
from journeymap.modules.knowledge import KnowledgeLedger
from journeymap.modules.knowledge.projection import KnowledgeProjection, KnowledgeProjector
from journeymap.modules.social.contracts import EVENT_TYPES, validate_payload
from journeymap.modules.social.knowledge import project_informed_knowledge
from journeymap.modules.social.perception import perceive_social


@dataclass(frozen=True, slots=True)
class ActionTrace:
    """One live submission attempt, including denials and interrupted execution.

    Retries have no new engine result. engine_submitted distinguishes live
    attempts from engine inputs; an interrupted engine call may have no result.
    """

    attempt_sequence: int
    request: ActionRequest
    result: ActionResult | None
    controller_result: ControllerActionResult | None
    boundary_reason: str | None = None
    error_type: str | None = None
    engine_submitted: bool = False
    retry_of_attempt: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request", self.request.detached())

    def detached(self) -> "ActionTrace":
        return replace(self)


@dataclass(slots=True)
class _RequestEntry:
    actor_id: str
    fingerprint: str
    attempt_sequence: int
    receipt: ControllerActionResult | None = None
    error_code: str = "ENGINE_ERROR"


class SimulationApplication:
    """Trusted owner; grant only game_for(actor), never this object, to Controllers."""

    def __init__(
        self,
        kernel: SimulationKernel,
        knowledge: KnowledgeLedger,
        pipeline: ObservationPipeline,
        *,
        knowledge_projector: KnowledgeProjector | None = None,
        perception_extension: PerceptionExtension | None = None,
        social: bool = False,
    ) -> None:
        if (
            knowledge.run_id != kernel.manifest.run_id
            or knowledge.initial_time != kernel.manifest.start_time
        ):
            raise ValueError("initial knowledge does not match the run manifest")
        for record in knowledge.history():
            if get_entity(kernel.state_snapshot, record.actor_id) is None:
                raise ValueError("knowledge owner is not a run entity")
        self._kernel = kernel
        self._knowledge = knowledge
        self._knowledge_projector = knowledge_projector
        self._perception_extension = perception_extension
        self._social = social
        self._pipeline = pipeline
        self._observations = ObservationHistory(kernel.manifest.run_id)
        self._traces: list[ActionTrace] = []
        self._busy = False
        self._requests: dict[str, _RequestEntry] = {}

    def game_for(self, actor_id: str) -> GamePort:
        """Trusted composition-time grant; the returned port cannot select an actor."""
        if get_entity(self._kernel.state_snapshot, actor_id) is None:
            raise ValueError("unknown Game actor")

        def observe() -> Observation:
            return self._observe(actor_id)

        def submit(request: ActionRequest) -> ControllerActionResult:
            return self._submit(actor_id, request)

        return GamePort(observe, submit)

    @property
    def observation_history(self) -> tuple[Observation, ...]:
        return self._observations.records

    def knowledge_snapshot(self) -> KnowledgeLedger | KnowledgeProjection:
        """Research/trusted read, reconstructed independently of Event delivery."""
        if self._knowledge_projector is None and not self._social:
            return self._knowledge
        return KnowledgeProjection(
            self._knowledge,
            self._kernel.events,
            self._knowledge_projector,
            event_rules=(project_informed_knowledge,) if self._social else (),
        )

    @property
    def action_traces(self) -> tuple[ActionTrace, ...]:
        return tuple(trace.detached() for trace in self._traces)

    def _enter(self) -> None:
        if self._busy or not self._kernel.is_booted:
            raise GameSubmissionError("GAME_UNAVAILABLE")
        self._busy = True

    def _observe(self, actor_id: str) -> Observation:
        self._enter()
        try:
            context = perceive(
                run_id=self._kernel.manifest.run_id,
                actor_id=actor_id,
                simulation_time=self._kernel.simulation_time,
                world=self._kernel.state_snapshot,
                knowledge=self.knowledge_snapshot(),
                extension=self._perception_extension,
            )
            if self._social:
                if "social" in context.perceived:
                    raise ValueError("social perception scope is reserved")
                context = replace(
                    context,
                    perceived={
                        **context.perceived,
                        "social": perceive_social(self._kernel.events, actor_id),
                    },
                )
            return self._observations.generate(context, self._pipeline)
        except Exception:
            raise GameSubmissionError("OBSERVATION_UNAVAILABLE") from None
        finally:
            self._busy = False

    def _authority_reason(self, actor_id: str, request: ActionRequest) -> str | None:
        if request.run_id != self._kernel.manifest.run_id:
            return "WRONG_RUN"
        if request.actor_id != actor_id:
            return "WRONG_ACTOR"
        observation = self._observations.get(request.based_on_observation_id)
        if observation is None:
            # Foreign-run IDs are deliberately treated as unknown local records;
            # no ID-prefix parsing or cross-run lookup grants authority.
            return "UNKNOWN_OBSERVATION"
        if observation.run_id != request.run_id or observation.actor_id != actor_id:
            return "UNAUTHORIZED_OBSERVATION"
        if request.submitted_at != self._kernel.simulation_time:
            return "INVALID_SUBMISSION_TIME"
        return None

    def _receipt(
        self, actor_id: str, request: ActionRequest, reason: str
    ) -> ControllerActionResult:
        return ControllerActionResult(
            request.action_request_id,
            self._kernel.manifest.run_id,
            actor_id,
            ActionStatus.REJECTED,
            reason,
            self._kernel.simulation_time,
            self._kernel.simulation_time,
        )

    def _submit(self, actor_id: str, request: ActionRequest) -> ControllerActionResult:
        if type(request) is not ActionRequest:
            raise TypeError("Game accepts only ActionRequest")
        self._enter()
        try:
            request = normalize_request(request)
            fingerprint = request_fingerprint(request)
            previous = self._requests.get(request.action_request_id)
            sequence = len(self._traces) + 1
            if previous is not None:
                if previous.actor_id != actor_id or previous.fingerprint != fingerprint:
                    visible = self._receipt(actor_id, request, "REQUEST_ID_CONFLICT")
                    self._traces.append(
                        ActionTrace(
                            sequence,
                            request,
                            None,
                            visible,
                            "REQUEST_ID_CONFLICT",
                            retry_of_attempt=previous.attempt_sequence,
                        )
                    )
                    return visible
                self._traces.append(
                    ActionTrace(
                        sequence,
                        request,
                        None,
                        previous.receipt,
                        boundary_reason=(
                            previous.receipt.reason_code if previous.receipt else None
                        ),
                        error_type=None if previous.receipt else "IndeterminateRequest",
                        retry_of_attempt=previous.attempt_sequence,
                    )
                )
                if previous.receipt is not None:
                    return previous.receipt
                raise GameSubmissionError(previous.error_code) from None
            # Reserve before authority reads or engine entry. No automatic retry
            # when an exception leaves no result, even after independent commits.
            entry = _RequestEntry(actor_id, fingerprint, sequence)
            self._requests[request.action_request_id] = entry
            try:
                visible = self._execute(actor_id, request)
            except GameSubmissionError as error:
                entry.error_code = str(error)
                trace = self._traces[-1] if len(self._traces) >= sequence else None
                if trace is not None and trace.result is not None:
                    entry.receipt = self._public_result(actor_id, request, trace.result)
                elif (
                    entry.error_code == "KNOWLEDGE_UNAVAILABLE"
                    and trace is not None
                    and not trace.engine_submitted
                ):
                    # A failed authority read never entered the engine. Preserve
                    # M5's safe retry after projection recovery; no finalized
                    # denial/result exists and no execution is indeterminate.
                    del self._requests[request.action_request_id]
                raise
            entry.receipt = visible
            return visible
        finally:
            self._busy = False

    def _execute(self, actor_id: str, request: ActionRequest) -> ControllerActionResult:
        sequence = len(self._traces) + 1
        reason = self._authority_reason(actor_id, request)
        if reason is None and request.action_type in EVENT_TYPES and request.schema_version == 1:
            try:
                if not self._social:
                    raise ActionValidationError("SOCIAL_UNAVAILABLE")
                validate_payload(request.action_type, request.payload)
            except ActionValidationError as error:
                reason = error.reason_code
            if reason is None and request.action_type == "INFORM":
                try:
                    owned = self.knowledge_snapshot().for_actor(actor_id)
                except Exception as error:
                    # A projection defect may itself be an ActionValidationError;
                    # it is a failed authority read, not a rejected actor payload.
                    self._traces.append(
                        ActionTrace(sequence, request, None, None, error_type=type(error).__name__)
                    )
                    raise GameSubmissionError("KNOWLEDGE_UNAVAILABLE") from None
                try:
                    observation = self._observations.get(request.based_on_observation_id)
                    assert observation is not None
                    validate_claim(
                        request,
                        observation,
                        owned,
                        self._kernel.events,
                    )
                except ActionValidationError as error:
                    reason = error.reason_code
                except Exception as error:
                    self._traces.append(
                        ActionTrace(sequence, request, None, None, error_type=type(error).__name__)
                    )
                    raise GameSubmissionError("KNOWLEDGE_UNAVAILABLE") from None
        if reason is not None:
            visible = self._receipt(actor_id, request, reason)
            self._traces.append(ActionTrace(sequence, request, None, visible, reason))
            return visible
        result_offset = len(self._kernel.action_results)
        try:
            result = self._kernel.submit_action(request)
        except ActionHandlerNotFoundError:
            # Never fallback to the system registry.
            visible = self._receipt(actor_id, request, "UNKNOWN_ACTION")
            self._traces.append(
                ActionTrace(
                    sequence, request, None, visible, "UNKNOWN_ACTION", engine_submitted=True
                )
            )
            return visible
        except Exception as error:
            # Publication can fail after a successful commit/result append.
            results = self._kernel.action_results[result_offset:]
            committed = results[0] if results else None
            self._traces.append(
                ActionTrace(
                    sequence,
                    request,
                    committed,
                    None,
                    error_type=type(error).__name__,
                    engine_submitted=True,
                )
            )
            raise GameSubmissionError("ENGINE_ERROR") from None
        visible = self._public_result(actor_id, request, result)
        self._traces.append(ActionTrace(sequence, request, result, visible, engine_submitted=True))
        return visible

    @staticmethod
    def _public_result(
        actor_id: str, request: ActionRequest, result: ActionResult
    ) -> ControllerActionResult:
        public_reason = result.reason_code
        if public_reason is not None and (
            public_reason not in VISIBLE_DOMAIN_REASONS
            or (request.action_type == "BUY" and public_reason in PRIVATE_PURCHASE_REASONS)
        ):
            public_reason = f"ACTION_{result.status.value}"
        visible = ControllerActionResult(
            request.action_request_id,
            request.run_id,
            actor_id,
            result.status,
            public_reason,
            result.started_at,
            result.resolved_at,
        )
        return visible
