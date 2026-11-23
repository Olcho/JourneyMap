"""Minimal replay harness for recorded M1 inputs."""

from collections.abc import Callable
from dataclasses import dataclass

from journeymap.core.canonical import JsonObject
from journeymap.core.events import EventEnvelope
from journeymap.core.handlers import ActionRequest, ActionResult, SystemEventOutcome
from journeymap.core.kernel import SimulationKernel
from journeymap.core.scheduler import ScenarioSchedule


@dataclass(frozen=True, slots=True)
class ReplayInput:
    """Versioned schedule and recorded actor intent stream."""

    schedule: ScenarioSchedule
    actions: tuple[ActionRequest, ...]
    advance_to: int

    def __post_init__(self) -> None:
        if self.advance_to < 0:
            raise ValueError("advance_to must be non-negative")


@dataclass(frozen=True, slots=True)
class ReplayReport:
    action_results: tuple[ActionResult, ...]
    system_event_outcomes: tuple[SystemEventOutcome, ...]
    events: tuple[EventEnvelope, ...]
    final_state: JsonObject
    final_state_digest: str
    final_simulation_time: int
    rng_draw_count: int


class ReplayHarness:
    """Build a fresh kernel and apply replay inputs in recorded order."""

    def __init__(self, kernel_factory: Callable[[], SimulationKernel]) -> None:
        self._kernel_factory = kernel_factory

    def run(self, replay_input: ReplayInput) -> ReplayReport:
        kernel = self._kernel_factory()
        manifest = kernel.manifest
        schedule = replay_input.schedule
        if (
            manifest.scenario_id != schedule.scenario_id
            or manifest.scenario_version != schedule.scenario_version
        ):
            raise ValueError("scenario schedule does not match the run manifest")

        kernel.boot()
        try:
            for event in schedule.events:
                kernel.schedule(event)
            for action in replay_input.actions:
                kernel.submit_action(action)
            kernel.advance_to(replay_input.advance_to)
            return ReplayReport(
                action_results=kernel.action_results,
                system_event_outcomes=kernel.system_event_outcomes,
                events=kernel.events,
                final_state=kernel.state_snapshot,
                final_state_digest=kernel.state_digest,
                final_simulation_time=kernel.simulation_time,
                rng_draw_count=kernel.rng_snapshot.draw_count,
            )
        finally:
            kernel.close()
