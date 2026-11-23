"""Domain-neutral, detached records at the perception/Controller boundary."""

from collections.abc import Callable
from dataclasses import dataclass, field

from journeymap.core.canonical import JsonObject, canonical_json, clone_json_object, state_digest


@dataclass(frozen=True, slots=True)
class PerceptionContext:
    """Only actor-allowed JSON, never a world/query/service handle.

    Containers are detached on construction and for each contributor invocation.
    The application owns filtering; contributors only assemble these inputs.
    """

    run_id: str
    actor_id: str
    simulation_time: int
    perceived: JsonObject
    known: JsonObject

    def __post_init__(self) -> None:
        _scope(self.run_id, self.actor_id, self.simulation_time)
        object.__setattr__(self, "perceived", clone_json_object(self.perceived))
        object.__setattr__(self, "known", clone_json_object(self.known))

    def detached(self) -> "PerceptionContext":
        return PerceptionContext(
            self.run_id, self.actor_id, self.simulation_time, self.perceived, self.known
        )


type ObservationContributor = Callable[[PerceptionContext], JsonObject]


@dataclass(frozen=True, slots=True, init=False)
class Observation:
    """Immutable envelope; JSON access returns a fresh value every time."""

    observation_id: str
    run_id: str
    actor_id: str
    observation_sequence: int
    simulation_time: int
    schema_version: int
    content_digest: str
    _content_json: str = field(repr=False)

    def __init__(
        self,
        run_id: str,
        actor_id: str,
        observation_sequence: int,
        simulation_time: int,
        content: JsonObject,
        schema_version: int = 1,
    ) -> None:
        _scope(run_id, actor_id, simulation_time)
        for number in (observation_sequence, schema_version):
            if type(number) is not int or number <= 0:
                raise ValueError("sequence and schema_version must be positive integers")
        detached = clone_json_object(content)
        for name, value in (
            ("run_id", run_id),
            ("actor_id", actor_id),
            ("observation_sequence", observation_sequence),
            ("simulation_time", simulation_time),
            ("schema_version", schema_version),
            ("observation_id", f"{run_id}:observation:{observation_sequence:08d}"),
            ("content_digest", state_digest(detached)),
            ("_content_json", canonical_json(detached)),
        ):
            object.__setattr__(self, name, value)

    @property
    def content(self) -> JsonObject:
        import json

        return clone_json_object(json.loads(self._content_json))


def _scope(run_id: str, actor_id: str, simulation_time: int) -> None:
    if any(type(value) is not str or not value for value in (run_id, actor_id)):
        raise ValueError("run_id and actor_id must be non-empty strings")
    if type(simulation_time) is not int or simulation_time < 0:
        raise ValueError("simulation_time must be a non-negative integer")


def validate_observation_v1(observation: Observation) -> None:
    """Reader contract; unsupported versions are never implicitly interpreted.

    Generic storage can preserve future envelopes, but current adapters only
    consume v1 sections. This validates structure, not truth or authorization.
    """
    if (
        type(observation) is not Observation
        or type(observation.schema_version) is not int
        or observation.schema_version != 1
    ):
        raise ValueError("unsupported Observation version")
    content = observation.content
    sections = content.get("sections")
    if set(content) != {"sections"} or not isinstance(sections, list):
        raise ValueError("invalid Observation sections")
    identities: set[tuple[str, str]] = set()
    for section in sections:
        if not isinstance(section, dict) or set(section) != {
            "module_id",
            "contributor_id",
            "content",
        }:
            raise ValueError("invalid Observation section")
        module, contributor = section["module_id"], section["contributor_id"]
        if (
            type(module) is not str
            or not module
            or type(contributor) is not str
            or not contributor
            or not isinstance(section["content"], dict)
        ):
            raise ValueError("invalid Observation section identity/content")
        identity = (module, contributor)
        if identity in identities:
            raise ValueError("duplicate Observation section identity")
        identities.add(identity)
