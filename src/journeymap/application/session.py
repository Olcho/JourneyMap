"""Live Game authority and research tracing; engine replay bypasses this layer."""

from dataclasses import dataclass, replace

from journeymap.application.observations import ObservationHistory, ObservationPipeline
from journeymap.application.perception import PerceptionExtension, perceive
from journeymap.core.controller import ControllerActionResult, GamePort, GameSubmissionError
from journeymap.core.entities import get_entity
from journeymap.core.handlers import (
    ActionHandlerNotFoundError,
    ActionRequest,
    ActionResult,
    ActionStatus,
)
from journeymap.core.kernel import SimulationKernel
from journeymap.core.observations import Observation
from journeymap.modules.knowledge import KnowledgeLedger
from journeymap.modules.knowledge.projection import KnowledgeProjection, KnowledgeProjector

# Only documented actor-facing M2 codes may cross the Game boundary. Extension
# diagnostics default to a generic status until an actor-facing contract exists.
_VISIBLE_REASONS = frozenset(
    {
        "INVALID_PAYLOAD",
        "INVALID_DURATION",
        "UNKNOWN_ACTOR",
        "INVALID_ENTITY",
        "MISSING_POSITION",
        "INVALID_POSITION",
        "UNKNOWN_ROUTE",
        "INVALID_ROUTE",
        "UNKNOWN_LOCATION",
        "INVALID_LOCATION",
        "WRONG_ORIGIN",
        "ROUTE_IMPASSABLE",
        "INVALID_TRAVERSAL_COST",
        "INVALID_PASSABILITY",
        "ROUTE_CHANGED",
    }
)


@dataclass(frozen=True, slots=True)
class ActionTrace:
    """One live submission attempt, including denials and interrupted execution.

    Attempt sequence disambiguates repeated request IDs without imposing M7
    idempotency policy. An engine defect need not have an ActionResult.
    """

    attempt_sequence: int
    request: ActionRequest
    result: ActionResult | None
    controller_result: ControllerActionResult | None
    boundary_reason: str | None = None
    error_type: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request", self.request.detached())

    def detached(self) -> "ActionTrace":
        return replace(self)


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
        self._pipeline = pipeline
        self._observations = ObservationHistory(kernel.manifest.run_id)
        self._traces: list[ActionTrace] = []
        self._busy = False

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
        if self._knowledge_projector is None:
            return self._knowledge
        return KnowledgeProjection(self._knowledge, self._kernel.events, self._knowledge_projector)

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
            # Core's replay envelope predates live input validation. Require
            # immutable identities here before authority lookup or trace storage.
            try:
                identities = (
                    request.action_request_id,
                    request.run_id,
                    request.actor_id,
                    request.based_on_observation_id,
                    request.action_type,
                )
                if any(type(value) is not str or not value for value in identities):
                    raise ValueError("invalid request identity")
                if request.correlation_id is not None and type(request.correlation_id) is not str:
                    raise ValueError("invalid request correlation")
                # Revalidate and detach caller-owned payloads as well.
                request = request.detached()
            except (TypeError, ValueError, RecursionError):
                raise GameSubmissionError("INVALID_REQUEST") from None
            sequence = len(self._traces) + 1
            reason = self._authority_reason(actor_id, request)
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
                self._traces.append(ActionTrace(sequence, request, None, visible, "UNKNOWN_ACTION"))
                return visible
            except Exception as error:
                # Publication can fail after a successful commit/result append.
                results = self._kernel.action_results[result_offset:]
                committed = results[0] if results else None
                self._traces.append(
                    ActionTrace(sequence, request, committed, None, error_type=type(error).__name__)
                )
                raise GameSubmissionError("ENGINE_ERROR") from None
            public_reason = result.reason_code
            if public_reason is not None and public_reason not in _VISIBLE_REASONS:
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
            self._traces.append(ActionTrace(sequence, request, result, visible))
            return visible
        finally:
            self._busy = False
