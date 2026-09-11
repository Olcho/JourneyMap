"""Survival actions/system inputs use the existing deterministic commit boundary."""

from journeymap.core.actions import validate_actor
from journeymap.core.events import EventDraft
from journeymap.core.handlers import (
    ActionRequest,
    ActionTiming,
    ActionValidationError,
    ResolutionContext,
    TransitionPlan,
    ValidationContext,
)
from journeymap.core.scheduler import ScheduledEvent
from journeymap.modules.inventory.models import positive_quantity
from journeymap.modules.inventory.transitions import decrement_candidate
from journeymap.modules.survival.models import (
    FATIGUE_PER_TICK,
    HUNGER_PER_TICK,
    REST_RECOVERY_PER_TICK,
    SurvivalState,
    actor_survival,
    hunger_recovery,
    survival_candidate,
)


class RestHandler:
    handler_id = "survival.rest.v1"

    def validate(self, request: ActionRequest, context: ValidationContext) -> None:
        if set(request.payload) != {"duration"}:
            raise ActionValidationError("INVALID_PAYLOAD")
        duration = request.payload["duration"]
        if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
            raise ActionValidationError("INVALID_DURATION")
        validate_actor(request, context)
        actor_survival(context.state, request.actor_id)

    def prepare(self, request: ActionRequest, context: ValidationContext) -> ActionTiming:
        duration = request.payload["duration"]
        assert isinstance(duration, int)
        return ActionTiming(duration)

    def validate_completion(
        self, request: ActionRequest, timing: ActionTiming, context: ValidationContext
    ) -> None:
        self.validate(request, context)

    def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
        before = actor_survival(context.state, request.actor_id)
        duration = request.payload["duration"]
        assert isinstance(duration, int)
        after = SurvivalState(
            before.hunger, max(0, before.fatigue - duration * REST_RECOVERY_PER_TICK)
        )
        return TransitionPlan(
            set_values={"survival": survival_candidate(context.state, request.actor_id, after)},
            events=(
                EventDraft(
                    "ActorRested",
                    1,
                    {
                        "actor_id": request.actor_id,
                        "duration": duration,
                        "fatigue_before": before.fatigue,
                        "fatigue_after": after.fatigue,
                    },
                ),
            ),
        )


def _consume(request: ActionRequest) -> tuple[str, int]:
    if set(request.payload) != {"item_id", "quantity"}:
        raise ActionValidationError("INVALID_PAYLOAD")
    item_id = request.payload["item_id"]
    if not isinstance(item_id, str) or not item_id:
        raise ActionValidationError("INVALID_PAYLOAD")
    return item_id, positive_quantity(request.payload["quantity"])


class ConsumeHandler:
    handler_id = "survival.consume.v1"

    def validate(self, request: ActionRequest, context: ValidationContext) -> None:
        item, quantity = _consume(request)
        validate_actor(request, context)
        actor_survival(context.state, request.actor_id)
        hunger_recovery(context.state, item)
        decrement_candidate(context.state, request.actor_id, item, quantity)

    def prepare(self, request: ActionRequest, context: ValidationContext) -> ActionTiming:
        return ActionTiming(1)

    def validate_completion(
        self, request: ActionRequest, timing: ActionTiming, context: ValidationContext
    ) -> None:
        self.validate(request, context)

    def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
        item, quantity = _consume(request)
        inventory = decrement_candidate(context.state, request.actor_id, item, quantity)
        before = actor_survival(context.state, request.actor_id)
        after = SurvivalState(
            max(0, before.hunger - hunger_recovery(context.state, item) * quantity), before.fatigue
        )
        survival = survival_candidate(context.state, request.actor_id, after)
        return TransitionPlan(
            set_values={"inventory": inventory, "survival": survival},
            events=(
                EventDraft(
                    "ItemConsumed",
                    1,
                    {
                        "actor_id": request.actor_id,
                        "item_id": item,
                        "quantity": quantity,
                        "hunger_before": before.hunger,
                        "hunger_after": after.hunger,
                    },
                ),
            ),
        )


class SurvivalTickHandler:
    handler_id = "survival.tick.v1"

    def validate(self, event: ScheduledEvent, context: ValidationContext) -> None:
        if event.payload != {}:
            raise ValueError("SurvivalTick v1 requires an empty payload")
        survival = context.state.get("survival")
        actors = survival.get("actors") if isinstance(survival, dict) else None
        if not isinstance(actors, dict):
            raise ValueError("invalid survival table")
        for actor in sorted(actors):
            actor_survival(context.state, actor)

    def resolve(self, event: ScheduledEvent, context: ResolutionContext) -> TransitionPlan:
        self.validate(
            event, ValidationContext(context.run_id, context.simulation_time, context.state)
        )
        candidate = context.state["survival"]
        assert isinstance(candidate, dict)
        actors = candidate["actors"]
        assert isinstance(actors, dict)
        events = []
        for actor in sorted(actors):
            before = actor_survival(context.state, actor)
            after = SurvivalState(
                min(100, before.hunger + HUNGER_PER_TICK),
                min(100, before.fatigue + FATIGUE_PER_TICK),
            )
            row = actors[actor]
            assert isinstance(row, dict)
            row.update(after.to_json())
            events.append(
                EventDraft(
                    "SurvivalAdvanced",
                    1,
                    {
                        "actor_id": actor,
                        "before": before.to_json(),
                        "after": after.to_json(),
                    },
                )
            )
        return TransitionPlan(set_values={"survival": candidate}, events=tuple(events))
