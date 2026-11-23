"""Public, local work availability; never exact private capital/material tables."""

from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.modules.inventory.models import owned_quantities
from journeymap.modules.labor.models import get_job, occupied_slots
from journeymap.modules.movement.perception import perceive_position
from journeymap.modules.survival.models import actor_survival
from journeymap.modules.trade.models import wallet_balance


def perceive_jobs(world: JsonObject, actor: str) -> list[JsonValue]:
    location = perceive_position(world, actor).get("location_id")
    labor = world.get("labor")
    jobs = labor.get("jobs") if isinstance(labor, dict) else None
    if not isinstance(jobs, dict):
        raise ValueError("missing labor contracts")
    public: list[JsonValue] = []
    for job_id, row in sorted(jobs.items()):
        if not isinstance(row, dict) or row.get("location_id") != location:
            continue
        job = get_job(world, job_id)
        if actor not in job.workers:
            continue
        own = actor_survival(world, actor)
        available = (
            occupied_slots(world, job_id) < job.capacity
            and owned_quantities(world, job.employer_id).get(job.input_item, 0)
            >= job.input_quantity
            and wallet_balance(world, job.employer_id) >= job.wage
            and own.hunger < 90
            and own.fatigue < 80
        )
        public.append(
            {
                "job_id": job_id,
                "location_id": job.location_id,
                "duration": job.duration,
                "wage": job.wage,
                "capacity": job.capacity,
                "available": available,
            }
        )
    return public
