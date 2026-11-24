"""A decision-source adapter; no terminal, GamePort or Research capability."""

from collections.abc import Callable

from journeymap.core.handlers import ActionRequest
from journeymap.core.observations import Observation, validate_observation_v1


class HumanController:
    __slots__ = ("_decision_source",)

    def __init__(self, decision_source: Callable[[Observation], ActionRequest]) -> None:
        # Trusted composition must not capture world capabilities in this source.
        self._decision_source = decision_source

    def decide(self, observation: Observation) -> ActionRequest:
        validate_observation_v1(observation)
        return self._decision_source(observation)
