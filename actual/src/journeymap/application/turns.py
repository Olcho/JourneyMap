"""One trusted Controller turn, without an agent loop or world capability injection."""

from dataclasses import dataclass

from journeymap.application.contracts import normalize_request, validate_action_contract
from journeymap.core.controller import (
    Controller,
    ControllerActionResult,
    GamePort,
    GameSubmissionError,
)
from journeymap.core.handlers import ActionRequest, ActionValidationError
from journeymap.core.observations import Observation, validate_observation_v1


@dataclass(frozen=True, slots=True)
class ControllerTurnResult:
    """Caller-retained research record; failures never store arbitrary raw output."""

    observation: Observation | None = None
    request: ActionRequest | None = None
    receipt: ControllerActionResult | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        if self.request is not None:
            object.__setattr__(self, "request", self.request.detached())


def run_controller_turn(game: GamePort, controller: Controller) -> ControllerTurnResult:
    try:
        observation = game.observe()
        validate_observation_v1(observation)
    except Exception:
        return ControllerTurnResult(failure_code="OBSERVATION_UNAVAILABLE")
    try:
        output = controller.decide(observation)
    except Exception:
        return ControllerTurnResult(observation, failure_code="CONTROLLER_ERROR")
    try:
        request = normalize_request(output)
    except GameSubmissionError:
        return ControllerTurnResult(observation, failure_code="INVALID_CONTROLLER_OUTPUT")
    if (
        request.run_id != observation.run_id
        or request.actor_id != observation.actor_id
        or request.based_on_observation_id != observation.observation_id
        or request.submitted_at != observation.simulation_time
    ):
        return ControllerTurnResult(observation, request, failure_code="INVALID_CONTROLLER_OUTPUT")
    try:
        validate_action_contract(request)
    except ActionValidationError as error:
        return ControllerTurnResult(observation, request, failure_code=error.reason_code)
    try:
        receipt = game.submit(request)
    except GameSubmissionError:
        # An engine failure can follow committed system/action transitions.
        # Do not confuse this with a pre-submit Controller failure or retry it.
        return ControllerTurnResult(observation, request, failure_code="SUBMISSION_ERROR")
    return ControllerTurnResult(observation, request, receipt)
