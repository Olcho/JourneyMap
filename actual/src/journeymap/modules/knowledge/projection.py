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
type KnowledgeEventRule = Callable[
    [EventEnvelope, tuple[EventEnvelope, ...], KnowledgeLedger], tuple[KnowledgeRecord, ...]
]


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
        projector: KnowledgeProjector | None = None,
        *,
        event_rules: tuple[KnowledgeEventRule, ...] = (),
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
        acquired = projector(tuple(event.detached() for event in committed)) if projector else ()
        for record in acquired:
            source = by_id.get(record.source_ref)
            if source is None or record.learned_at != source.simulation_time:
                raise ValueError("runtime knowledge must reference its committed source Event")
        # Reuse the record scope/identity/supersession validator on this snapshot.
        # The original initial ledger remains frozen at the manifest start tick.
        snapshot = KnowledgeLedger(initial.run_id, initial.initial_time, initial.history())
        for index, event in enumerate(committed):
            direct = tuple(record for record in acquired if record.source_ref == event.event_id)
            snapshot = KnowledgeLedger(
                initial.run_id, event.simulation_time, (*snapshot.history(), *direct)
            )
            for rule in event_rules:
                # Each rule receives isolated evidence and the accumulated prefix.
                added = rule(
                    event.detached(),
                    tuple(previous.detached() for previous in committed[:index]),
                    KnowledgeLedger(initial.run_id, event.simulation_time, snapshot.history()),
                )
                if any(
                    record.source_ref != event.event_id
                    or record.learned_at != event.simulation_time
                    for record in added
                ):
                    raise ValueError("event rule must reference its current committed source Event")
                snapshot = KnowledgeLedger(
                    initial.run_id, event.simulation_time, (*snapshot.history(), *added)
                )
        self._snapshot = snapshot

    @property
    def run_id(self) -> str:
        return self._snapshot.run_id

    def for_actor(self, actor_id: str) -> ActorKnowledgeView:
        return self._snapshot.for_actor(actor_id)

    def history(self) -> tuple[KnowledgeRecord, ...]:
        return self._snapshot.history()
