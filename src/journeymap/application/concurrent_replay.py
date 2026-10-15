"""Live-input replay, including Observation authority and effect-free denials."""

from collections.abc import Callable
from dataclasses import dataclass, replace

from journeymap.application.concurrent import (
    ConcurrentApplication,
    ConcurrentAttempt,
    ConcurrentReceipt,
)
from journeymap.core.canonical import JsonObject
from journeymap.core.concurrent import ConcurrentKernel
from journeymap.core.handlers import ActionRequest
from journeymap.core.observations import Observation
from journeymap.core.scheduler import ScheduledEventSpec


@dataclass(frozen=True, slots=True)
class LiveInput:
    kind: str
    tick: int
    actor: str | None = None
    request: ActionRequest | None = None
    schedule: ScheduledEventSpec | None = None
    observation_id: str | None = None
    observation_digest: str | None = None
    receipt: ConcurrentReceipt | None = None

    def __post_init__(self) -> None:
        if self.request is not None:
            object.__setattr__(self, "request", self.request.detached())
        if self.schedule is not None:
            object.__setattr__(self, "schedule", replace(self.schedule))


@dataclass(frozen=True, slots=True)
class LiveReplayReport:
    attempts: tuple[ConcurrentAttempt, ...]
    observations: tuple[Observation, ...]
    final_execution: JsonObject


def replay_live_inputs(
    factory: Callable[[], tuple[ConcurrentKernel, ConcurrentApplication]],
    inputs: tuple[LiveInput, ...],
) -> LiveReplayReport:
    kernel, app = factory()
    kernel.boot()
    observed = []
    try:
        for entry in inputs:
            if entry.kind == "advance":
                kernel.advance_to(entry.tick)
                continue
            if entry.tick != kernel.simulation_time:
                raise ValueError("live replay tick mismatch")
            if entry.kind == "schedule" and entry.schedule is not None:
                kernel.schedule(entry.schedule)
            elif entry.kind == "observe" and entry.actor is not None:
                observation = app.game_for(entry.actor).observe()
                if (observation.observation_id, observation.content_digest) != (
                    entry.observation_id,
                    entry.observation_digest,
                ):
                    raise ValueError("live replay Observation mismatch")
                observed.append(observation)
            elif entry.kind == "submit" and entry.actor is not None and entry.request is not None:
                if app.game_for(entry.actor).submit(entry.request) != entry.receipt:
                    raise ValueError("live replay receipt mismatch")
            else:
                raise ValueError("unsupported live replay input")
        return LiveReplayReport(app.attempts, tuple(observed), kernel.execution_snapshot)
    finally:
        kernel.close()
