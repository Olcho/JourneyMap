"""Separate read capability for authorized in-process research clients."""

from journeymap.application.session import ActionTrace, SimulationApplication
from journeymap.core.canonical import JsonObject
from journeymap.core.events import EventEnvelope
from journeymap.core.handlers import ActionResult, SystemEventOutcome
from journeymap.core.kernel import SimulationKernel
from journeymap.core.observations import Observation
from journeymap.core.run import RngSnapshot, RunManifest
from journeymap.core.scheduler import ScheduledEvent
from journeymap.modules.knowledge import KnowledgeLedger, KnowledgeRecord


class ResearchView:
    """Read current truth and histories; no scenario editing or mutation methods."""

    def __init__(
        self,
        kernel: SimulationKernel,
        application: SimulationApplication,
        knowledge: KnowledgeLedger,
    ) -> None:
        self._kernel = kernel
        self._application = application
        self._knowledge = knowledge

    @property
    def world_snapshot(self) -> JsonObject:
        return self._kernel.state_snapshot

    @property
    def manifest(self) -> RunManifest:
        return self._kernel.manifest

    @property
    def simulation_time(self) -> int:
        return self._kernel.simulation_time

    @property
    def state_digest(self) -> str:
        return self._kernel.state_digest

    @property
    def rng_snapshot(self) -> RngSnapshot:
        return self._kernel.rng_snapshot

    @property
    def observations(self) -> tuple[Observation, ...]:
        return self._application.observation_history

    def knowledge_history(self, actor_id: str) -> tuple[KnowledgeRecord, ...]:
        return self._knowledge.for_actor(actor_id).history()

    @property
    def action_traces(self) -> tuple[ActionTrace, ...]:
        return self._application.action_traces

    @property
    def action_results(self) -> tuple[ActionResult, ...]:
        return self._kernel.action_results

    @property
    def events(self) -> tuple[EventEnvelope, ...]:
        return self._kernel.events

    @property
    def pending_scheduled_events(self) -> tuple[ScheduledEvent, ...]:
        return self._kernel.pending_scheduled_events

    @property
    def system_event_outcomes(self) -> tuple[SystemEventOutcome, ...]:
        return self._kernel.system_event_outcomes
