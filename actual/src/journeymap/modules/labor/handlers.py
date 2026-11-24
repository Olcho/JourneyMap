"""Timed labor: current resource checks and one atomic production/wage plan."""

from journeymap.core.actions import validate_actor
from journeymap.core.canonical import JsonObject
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
from journeymap.modules.inventory.transitions import transform_candidate
from journeymap.modules.labor.models import Job, get_job, occupied_slots
from journeymap.modules.movement.perception import perceive_position
from journeymap.modules.survival.models import SurvivalState, actor_survival, survival_candidate
from journeymap.modules.trade.models import payment_candidate


def validate_work_payload(payload: JsonObject) -> str:
    if set(payload) != {"job_id"} or type(payload["job_id"]) is not str or not payload["job_id"]:
        raise ActionValidationError("INVALID_PAYLOAD")
    return str(payload["job_id"])


def validate_work(request: ActionRequest, context: ValidationContext) -> Job:
    validate_actor(request, context)
    job = get_job(context.state, validate_work_payload(request.payload))
    if request.actor_id not in job.workers:
        raise ActionValidationError("NOT_EMPLOYED")
    if get_entity(context.state, job.employer_id) is None:
        raise ActionValidationError("UNKNOWN_EMPLOYER")
    if perceive_position(context.state, request.actor_id).get("location_id") != job.location_id:
        raise ActionValidationError("NOT_AT_WORKPLACE")
    need = actor_survival(context.state, request.actor_id)
    if need.hunger >= 90 or need.fatigue >= 80:
        raise ActionValidationError("WORK_NEEDS_LIMIT")
    try:
        transform_candidate(
            context.state,
            job.employer_id,
            {job.input_item: job.input_quantity},
            {job.output_item: job.output_quantity},
        )
        payment_candidate(context.state, job.employer_id, request.actor_id, job.wage)
    except ActionValidationError as error:
        # Shared BUY diagnostics refer to the buyer there, but the employer here.
        # Keep precise research evidence without exporting private capital details.
        raise ActionValidationError(f"EMPLOYER_{error.reason_code}") from None
    return job


class WorkHandler:
    handler_id = "labor.work.v1"

    def validate(self, request: ActionRequest, context: ValidationContext) -> None:
        validate_work(request, context)

    def prepare(self, request: ActionRequest, context: ValidationContext) -> ActionTiming:
        job = get_job(context.state, validate_work_payload(request.payload))
        if occupied_slots(context.state, job.job_id) >= job.capacity:
            raise ActionValidationError("WORKPLACE_FULL")
        return ActionTiming(job.duration, job.to_json())

    def validate_completion(
        self, request: ActionRequest, timing: ActionTiming, context: ValidationContext
    ) -> None:
        job = get_job(context.state, validate_work_payload(request.payload))
        if timing.data != job.to_json():
            raise ActionValidationError("JOB_CHANGED")
        if (
            occupied_slots(context.state, job.job_id, exclude_actor=request.actor_id)
            >= job.capacity
        ):
            raise ActionValidationError("WORKPLACE_FULL")
        validate_work(request, context)

    def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
        job = validate_work(
            request, ValidationContext(context.run_id, context.simulation_time, context.state)
        )
        inventory = transform_candidate(
            context.state,
            job.employer_id,
            {job.input_item: job.input_quantity},
            {job.output_item: job.output_quantity},
        )
        trade = payment_candidate(context.state, job.employer_id, request.actor_id, job.wage)
        before = actor_survival(context.state, request.actor_id)
        after = SurvivalState(before.hunger, min(100, before.fatigue + job.fatigue_cost))
        return TransitionPlan(
            set_values={
                "inventory": inventory,
                "trade": trade,
                "survival": survival_candidate(context.state, request.actor_id, after),
            },
            events=(
                EventDraft(
                    "WorkCompleted",
                    1,
                    {
                        "worker_id": request.actor_id,
                        "employer_id": job.employer_id,
                        "job_id": job.job_id,
                        "location_id": job.location_id,
                        "input_item": job.input_item,
                        "input_quantity": job.input_quantity,
                        "output_item": job.output_item,
                        "output_quantity": job.output_quantity,
                        "wage": job.wage,
                        "fatigue_before": before.fatigue,
                        "fatigue_after": after.fatigue,
                    },
                ),
            ),
        )
