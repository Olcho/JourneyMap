"""Versioned work contract and trusted capacity/resource queries."""

from dataclasses import dataclass

from journeymap.core.canonical import JsonObject
from journeymap.core.handlers import ActionValidationError


@dataclass(frozen=True, slots=True)
class Job:
    job_id: str
    employer_id: str
    location_id: str
    workers: tuple[str, ...]
    duration: int = 3
    wage: int = 6
    capacity: int = 2
    input_item: str = "flour"
    input_quantity: int = 1
    output_item: str = "bread"
    output_quantity: int = 4
    fatigue_cost: int = 4

    def __post_init__(self) -> None:
        if any(
            type(s) is not str or not s
            for s in (
                self.job_id,
                self.employer_id,
                self.location_id,
                self.input_item,
                self.output_item,
            )
        ):
            raise ValueError("invalid job identity")
        if not self.workers or len(set(self.workers)) != len(self.workers):
            raise ValueError("workers must be nonempty and unique")
        if any(type(w) is not str or not w or w == self.employer_id for w in self.workers):
            raise ValueError("invalid employee")
        if any(
            type(v) is not int or v <= 0
            for v in (
                self.duration,
                self.wage,
                self.capacity,
                self.input_quantity,
                self.output_quantity,
            )
        ):
            raise ValueError("invalid job quantity")
        if type(self.fatigue_cost) is not int or not 0 <= self.fatigue_cost <= 100:
            raise ValueError("invalid fatigue cost")

    def to_json(self) -> JsonObject:
        return {
            "job_id": self.job_id,
            "employer_id": self.employer_id,
            "location_id": self.location_id,
            "workers": list(self.workers),
            "duration": self.duration,
            "wage": self.wage,
            "capacity": self.capacity,
            "input_item": self.input_item,
            "input_quantity": self.input_quantity,
            "output_item": self.output_item,
            "output_quantity": self.output_quantity,
            "fatigue_cost": self.fatigue_cost,
        }


def get_job(world: JsonObject, job_id: str) -> Job:
    labor = world.get("labor")
    jobs = labor.get("jobs") if isinstance(labor, dict) else None
    row = jobs.get(job_id) if isinstance(jobs, dict) else None
    if not isinstance(row, dict) or row.get("job_id") != job_id:
        raise ActionValidationError("UNKNOWN_JOB")
    try:
        names = (
            "job_id",
            "employer_id",
            "location_id",
            "workers",
            "duration",
            "wage",
            "capacity",
            "input_item",
            "input_quantity",
            "output_item",
            "output_quantity",
            "fatigue_cost",
        )
        if set(row) != set(names) or not isinstance(row["workers"], list):
            raise ValueError("invalid contract fields")
        # Runtime type validation is performed by Job, not coerced from JSON.
        return Job(**{**row, "workers": tuple(row["workers"])})  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ActionValidationError("INVALID_JOB") from None


def occupied_slots(world: JsonObject, job_id: str, *, exclude_actor: str | None = None) -> int:
    runtime = world.get("_concurrent")
    actions = runtime.get("active_actions", []) if isinstance(runtime, dict) else []
    assert isinstance(actions, list)
    count = 0
    for a in actions:
        if not isinstance(a, dict) or a.get("action_type") != "WORK":
            continue
        payload = a.get("payload")
        if (
            a.get("actor_id") != exclude_actor
            and isinstance(payload, dict)
            and payload.get("job_id") == job_id
        ):
            count += 1
    return count
