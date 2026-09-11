"""Pure, recoverable research views over complete committed Event histories."""

from collections.abc import Callable
from typing import Protocol

from journeymap.core.events import EventEnvelope
from journeymap.modules.knowledge.records import (
    ActorKnowledgeView,
    KnowledgeLedger,
    KnowledgeRecord,
)


class KnowledgeReader(Protocol):
    @property
    def run_id(self) -> str: ...

    def for_actor(self, actor_id: str) -> ActorKnowledgeView: ...


type KnowledgeProjector = Callable[[tuple[EventEnvelope, ...]], tuple[KnowledgeRecord, ...]]


class KnowledgeProjection:
    """Detached validated snapshot, with no append/cursor/subscriber side effects.

    Rebuild from the complete ordered log each time. Duplicate or incomplete
    logs are errors, while repeated rebuilds are idempotent. Projection failure
    publishes no partial view and never rolls back an already committed world.
    """

    def __init__(
        self,
        initial: KnowledgeLedger,
        events: tuple[EventEnvelope, ...],
        projector: KnowledgeProjector,
    ) -> None:
        committed = tuple(event.detached() for event in events)
        by_id: dict[str, EventEnvelope] = {}
        tick = initial.initial_time
        for sequence, event in enumerate(committed, start=1):
            if (
                event.run_id != initial.run_id
                or event.event_sequence != sequence
                or event.event_id in by_id
                or event.simulation_time < tick
            ):
                raise ValueError("projection requires a complete ordered single-run Event log")
            by_id[event.event_id] = event
            tick = event.simulation_time
        # Rules receive copies, not the evidence used for provenance validation.
        acquired = projector(tuple(event.detached() for event in committed))
        for record in acquired:
            source = by_id.get(record.source_ref)
            if source is None or record.learned_at != source.simulation_time:
                raise ValueError("runtime knowledge must reference its committed source Event")
        # Reuse the record scope/identity/supersession validator on this snapshot.
        # The original initial ledger remains frozen at the manifest start tick.
        self._snapshot = KnowledgeLedger(initial.run_id, tick, (*initial.history(), *acquired))

    @property
    def run_id(self) -> str:
        return self._snapshot.run_id

    def for_actor(self, actor_id: str) -> ActorKnowledgeView:
        return self._snapshot.for_actor(actor_id)

    def history(self) -> tuple[KnowledgeRecord, ...]:
        return self._snapshot.history()
