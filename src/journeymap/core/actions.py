"""Domain-neutral WAIT action, explicitly registered by composition."""

from journeymap.core.canonical import JsonObject
from journeymap.core.entities import get_entity
from journeymap.core.handlers import (
    ActionRequest,
    ActionTiming,
    ActionValidationError,
    ResolutionContext,
    TransitionPlan,
    ValidationContext,
)


def validate_actor(request: ActionRequest, context: ValidationContext) -> None:
    try:
        entity = get_entity(context.state, request.actor_id)
    except ValueError as error:
        raise ActionValidationError("INVALID_ENTITY") from error
    if entity is None:
        raise ActionValidationError("UNKNOWN_ACTOR")


def validate_wait_payload(payload: JsonObject) -> int:
    if set(payload) != {"duration"}:
        raise ActionValidationError("INVALID_PAYLOAD")
    duration = payload["duration"]
    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
        raise ActionValidationError("INVALID_DURATION")
    return duration


class WaitHandler:
    handler_id = "core.wait.v1"

    def validate(self, request: ActionRequest, context: ValidationContext) -> None:
        self.prepare(request, context)

    def prepare(self, request: ActionRequest, context: ValidationContext) -> ActionTiming:
        duration = validate_wait_payload(request.payload)
        validate_actor(request, context)
        return ActionTiming(duration)

    def validate_completion(
        self, request: ActionRequest, timing: ActionTiming, context: ValidationContext
    ) -> None:
        validate_actor(request, context)

    def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
        return TransitionPlan()
