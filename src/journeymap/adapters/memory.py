"""Memory may select already delivered actor context; it cannot acquire facts."""

from dataclasses import dataclass
from typing import Protocol

from journeymap.core.observations import Observation, validate_observation_v1


@dataclass(frozen=True, slots=True)
class MemoryContext:
    current: Observation
    prior: tuple[Observation, ...] = ()

    def __post_init__(self) -> None:
        validate_observation_v1(self.current)
        object.__setattr__(self, "prior", tuple(self.prior))
        previous_sequence = 0
        previous_tick = 0
        for record in self.prior:
            validate_observation_v1(record)
            if (
                record.run_id != self.current.run_id
                or record.actor_id != self.current.actor_id
                or not previous_sequence
                < record.observation_sequence
                < self.current.observation_sequence
                or not previous_tick <= record.simulation_time <= self.current.simulation_time
            ):
                raise ValueError("memory requires an ordered same-actor past")
            previous_sequence = record.observation_sequence
            previous_tick = record.simulation_time


class MemoryPolicy(Protocol):
    def select(self, context: MemoryContext) -> tuple[Observation, ...]: ...


class NoMemory:
    __slots__ = ()

    def select(self, context: MemoryContext) -> tuple[Observation, ...]:
        return ()
