"""Ordered assembly and append-only storage after trusted perception."""

from journeymap.core.canonical import JsonObject, JsonValue, clone_json_object
from journeymap.core.observations import Observation, ObservationContributor, PerceptionContext


class ObservationPipeline:
    def __init__(self) -> None:
        self._contributors: dict[tuple[int, str, str], ObservationContributor] = {}

    def register(
        self,
        *,
        priority: int,
        module_id: str,
        contributor_id: str,
        contributor: ObservationContributor,
    ) -> None:
        if type(priority) is not int:
            raise ValueError("contributor priority must be an integer")
        if any(type(value) is not str or not value for value in (module_id, contributor_id)):
            raise ValueError("module_id and contributor_id must be non-empty strings")
        key = (priority, module_id, contributor_id)
        if key in self._contributors:
            raise ValueError(f"duplicate contributor ordering key: {key!r}")
        self._contributors[key] = contributor

    def assemble(self, context: PerceptionContext) -> JsonObject:
        # Snapshot registration and input before invoking any extension.
        entries = sorted(self._contributors.items())
        safe = context.detached()
        sections: list[JsonValue] = []
        for (_priority, module_id, contributor_id), contributor in entries:
            output = contributor(safe.detached())
            if type(output) is not dict:
                raise ValueError("contributor output must be a canonical JSON object")
            sections.append(
                {
                    "module_id": module_id,
                    "contributor_id": contributor_id,
                    "content": clone_json_object(output),
                }
            )
        return {"sections": sections}


class ObservationHistory:
    """One run's successfully assembled records; no replace/delete/import API."""

    def __init__(self, run_id: str) -> None:
        self._run_id = run_id
        self._records: list[Observation] = []
        self._by_id: dict[str, Observation] = {}
        self._building = False

    @property
    def records(self) -> tuple[Observation, ...]:
        return tuple(self._records)

    def get(self, observation_id: str) -> Observation | None:
        return self._by_id.get(observation_id)

    def generate(self, context: PerceptionContext, pipeline: ObservationPipeline) -> Observation:
        if context.run_id != self._run_id:
            raise ValueError("Observation context belongs to another run")
        if self._building:
            raise RuntimeError("Observation generation cannot be reentered")
        self._building = True
        try:
            record = Observation(
                context.run_id,
                context.actor_id,
                len(self._records) + 1,
                context.simulation_time,
                pipeline.assemble(context),
            )
            self._records.append(record)
            self._by_id[record.observation_id] = record
            return record
        finally:
            self._building = False


def contribute_self(context: PerceptionContext) -> JsonObject:
    return {"self": context.perceived["self"]}
