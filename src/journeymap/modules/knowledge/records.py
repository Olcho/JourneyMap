"""Explicit, source-aware initial knowledge; no inference or runtime writer."""

from collections.abc import Iterable
from dataclasses import dataclass, replace

from journeymap.core.canonical import JsonObject, JsonValue, clone_json_value
from journeymap.core.observations import PerceptionContext


@dataclass(frozen=True, slots=True)
class KnowledgeRecord:
    knowledge_record_id: str
    run_id: str
    actor_id: str
    subject_ref: str
    predicate: str
    value: JsonValue
    source_kind: str
    source_ref: str
    learned_at: int
    supersedes_id: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        for value in (
            self.knowledge_record_id,
            self.run_id,
            self.actor_id,
            self.subject_ref,
            self.predicate,
            self.source_kind,
            self.source_ref,
        ):
            if type(value) is not str or not value:
                raise ValueError("knowledge identities and source must be non-empty strings")
        if type(self.learned_at) is not int or self.learned_at < 0:
            raise ValueError("learned_at must be a non-negative integer")
        if type(self.schema_version) is not int or self.schema_version <= 0:
            raise ValueError("schema_version must be a positive integer")
        if self.supersedes_id is not None and (
            type(self.supersedes_id) is not str or not self.supersedes_id
        ):
            raise ValueError("supersedes_id must be a non-empty string or None")
        object.__setattr__(self, "value", clone_json_value(self.value))

    def detached(self) -> "KnowledgeRecord":
        return replace(self)

    def to_json(self) -> JsonObject:
        return {
            "knowledge_record_id": self.knowledge_record_id,
            "run_id": self.run_id,
            "actor_id": self.actor_id,
            "subject_ref": self.subject_ref,
            "predicate": self.predicate,
            "value": clone_json_value(self.value),
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "learned_at": self.learned_at,
            "supersedes_id": self.supersedes_id,
            "schema_version": self.schema_version,
        }


class ActorKnowledgeView:
    """Detached actor-only snapshot with no actor/run selector or ledger handle."""

    __slots__ = ("_records",)

    def __init__(self, records: tuple[KnowledgeRecord, ...]) -> None:
        if len({(record.run_id, record.actor_id) for record in records}) > 1:
            raise ValueError("knowledge view must have a single actor/run scope")
        self._records = tuple(record.detached() for record in records)

    def history(self) -> tuple[KnowledgeRecord, ...]:
        return tuple(record.detached() for record in self._records)

    def query(self, subject_ref: str, predicate: str) -> tuple[KnowledgeRecord, ...]:
        """Empty means UNKNOWN; preserve contradictions and correction history."""
        return tuple(
            record.detached()
            for record in self._records
            if record.subject_ref == subject_ref and record.predicate == predicate
        )


class KnowledgeLedger:
    """Frozen initial first-class ledger, separate from the kernel state digest.

    IDs and sources are explicit versioned scenario inputs, never inferred from
    truth. Runtime append must later participate in a validated transaction;
    no subscriber, Observation builder or Controller receives a write API here.
    """

    def __init__(
        self,
        run_id: str,
        initial_time: int,
        records: Iterable[KnowledgeRecord] = (),
    ) -> None:
        if type(run_id) is not str or not run_id:
            raise ValueError("run_id must be a non-empty string")
        if type(initial_time) is not int or initial_time < 0:
            raise ValueError("initial_time must be a non-negative integer")
        copied = tuple(record.detached() for record in records)
        by_id: dict[str, KnowledgeRecord] = {}
        for record in copied:
            if record.run_id != run_id or record.learned_at > initial_time:
                raise ValueError("initial knowledge must belong to this run and its past")
            if record.knowledge_record_id in by_id:
                raise ValueError("duplicate knowledge_record_id")
            by_id[record.knowledge_record_id] = record
        for record in copied:
            if record.supersedes_id is not None:
                previous = by_id.get(record.supersedes_id)
                if previous is None or (
                    previous.actor_id != record.actor_id
                    or previous.subject_ref != record.subject_ref
                    or previous.predicate != record.predicate
                    or previous.learned_at > record.learned_at
                ):
                    raise ValueError("invalid knowledge supersession scope or chronology")
            seen = {record.knowledge_record_id}
            current = record
            while current.supersedes_id is not None:
                if current.supersedes_id in seen:
                    raise ValueError("cyclic knowledge supersession")
                seen.add(current.supersedes_id)
                previous = by_id.get(current.supersedes_id)
                if previous is None:
                    raise ValueError("unknown knowledge supersession")
                current = previous
        self._records = tuple(
            sorted(copied, key=lambda record: (record.learned_at, record.knowledge_record_id))
        )
        self.run_id = run_id
        self.initial_time = initial_time

    def for_actor(self, actor_id: str) -> ActorKnowledgeView:
        return ActorKnowledgeView(
            tuple(record for record in self._records if record.actor_id == actor_id)
        )

    def history(self) -> tuple[KnowledgeRecord, ...]:
        return tuple(record.detached() for record in self._records)


def contribute_knowledge(context: PerceptionContext) -> JsonObject:
    return context.known
