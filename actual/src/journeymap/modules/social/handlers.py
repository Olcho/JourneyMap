"""Canonical interaction checks through the existing timed action boundary."""

from journeymap.core.actions import validate_actor
from journeymap.core.entities import get_entity
from journeymap.core.events import EventDraft
from journeymap.core.handlers import (
    ActionRequest,
    ActionTiming,
    ActionValidationError,
    ResolutionContext,
    TransitionPlan,
    ValidationContext,
)
from journeymap.modules.movement.perception import perceive_position
from journeymap.modules.social.contracts import EVENT_TYPES, validate_payload


def interaction_location(request: ActionRequest, context: ValidationContext) -> str:
    validate_payload(request.action_type, request.payload)
    validate_actor(request, context)
    target = request.payload["target_actor_id"]
    assert isinstance(target, str)
    if target == request.actor_id:
        raise ActionValidationError("SELF_TARGET")
    try:
        entity = get_entity(context.state, target)
    except ValueError as error:
        raise ActionValidationError("INVALID_TARGET") from error
    if entity is None:
        raise ActionValidationError("UNKNOWN_TARGET")
    locations = []
    for actor, prefix in ((request.actor_id, ""), (target, "TARGET_")):
        try:
            position = perceive_position(context.state, actor)
        except ValueError as error:
            raise ActionValidationError(f"{prefix}INVALID_POSITION") from error
        if not position:
            raise ActionValidationError(f"{prefix}MISSING_POSITION")
        locations.append(position["location_id"])
    if locations[0] != locations[1]:
        raise ActionValidationError("OUT_OF_RANGE")
    location = locations[0]
    assert isinstance(location, str)
    return location


class SocialHandler:
    """No Knowledge reader, world mutation, inbox, automatic answer or fulfillment."""

    def __init__(self, action_type: str) -> None:
        self.handler_id = f"social.{action_type.lower()}.v1"

    def validate(self, request: ActionRequest, context: ValidationContext) -> None:
        interaction_location(request, context)

    def prepare(self, request: ActionRequest, context: ValidationContext) -> ActionTiming:
        return ActionTiming(1)

    def validate_completion(
        self, request: ActionRequest, timing: ActionTiming, context: ValidationContext
    ) -> None:
        self.validate(request, context)

    def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
        location = interaction_location(
            request, ValidationContext(context.run_id, context.simulation_time, context.state)
        )
        return TransitionPlan(
            events=(
                EventDraft(
                    EVENT_TYPES[request.action_type],
                    1,
                    {
                        **request.payload,
                        "sender_actor_id": request.actor_id,
                        "location_id": location,
                    },
                ),
            )
        )
