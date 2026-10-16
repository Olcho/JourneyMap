"""Trusted local approval binding and durable, single-use paid-run journal."""

import hashlib
import sqlite3
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from journeymap.adapters.life_llm import PROMPT, PROMPT_VERSION, life_response_format
from journeymap.adapters.life_responses import LifeExecutionPlan
from journeymap.adapters.llm import strict_json
from journeymap.core.canonical import JsonObject, canonical_json

PROTOCOL = "life-paid-responses-2"
PACKAGE = Path(__file__).resolve().parents[1]
REGISTRY = PACKAGE.parents[1] / "artifacts/phase4_3/runs.sqlite3"


def source_identity() -> str:
    digest = hashlib.sha256()
    for path in sorted(PACKAGE.rglob("*.py")):
        digest.update(path.relative_to(PACKAGE).as_posix().encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def protocol_identity() -> str:
    return hashlib.sha256(
        canonical_json(
            {
                "version": PROTOCOL,
                "prompt_version": PROMPT_VERSION,
                "prompt": PROMPT,
                "format": life_response_format(),
            }
        ).encode()
    ).hexdigest()


def binding(run_id: str, registry: Path = REGISTRY) -> JsonObject:
    if str(UUID(run_id)) != run_id:
        raise ValueError("canonical UUID run ID required")
    return {
        "run_id": run_id,
        "plan_digest": LifeExecutionPlan().digest,
        "source_digest": source_identity(),
        "protocol_digest": protocol_identity(),
        "registry": str(registry.resolve()),
    }


@dataclass(frozen=True, slots=True)
class ExecutionApproval:
    """Operator-supplied human approval record, not a digest-derived permission.

    The local operator and filesystem are trusted. This does not authenticate a
    human cryptographically; the agent must obtain explicit human authorization
    before creating or using such a record. Tests use clearly synthetic records.
    """

    approval_id: str
    approved_by: str
    decision: str
    scope: tuple[str, ...]
    bound: JsonObject

    def validate(self, expected: JsonObject) -> None:
        try:
            canonical_id = str(UUID(self.approval_id))
        except (ValueError, TypeError, AttributeError):
            raise PermissionError("invalid approval ID") from None
        if (
            canonical_id != self.approval_id
            or self.approved_by != "human-user"
            or self.decision != "approve-one-paid-life-run"
            or self.scope != ("read-openai-api-key", "paid-responses")
            or self.bound != expected
        ):
            raise PermissionError("approval is absent, invalid or mismatched")


def load_approval(path: Path) -> ExecutionApproval:
    try:
        value = strict_json(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != {
            "approval_id",
            "approved_by",
            "decision",
            "scope",
            "bound",
        }:
            raise ValueError
        if not isinstance(value["bound"], dict) or not isinstance(value["scope"], list):
            raise ValueError
        if any(type(value[k]) is not str for k in ("approval_id", "approved_by", "decision")):
            raise ValueError
        return ExecutionApproval(
            cast(str, value["approval_id"]),
            cast(str, value["approved_by"]),
            cast(str, value["decision"]),
            tuple(cast(list[str], value["scope"])),
            value["bound"],
        )
    except (OSError, ValueError, TypeError, RecursionError):
        raise PermissionError("approval file is invalid") from None


class JournalFailure(RuntimeError):
    pass


class RunJournal:
    """SQLite FULL transactions; claimed IDs are never reopened or reset.

    This is a local durable admission registry, not live checkpoint recovery.
    Operators must preserve the registry; deleting it is outside this contract.
    """

    def __init__(self, path: Path, bound: JsonObject, approval: ExecutionApproval) -> None:
        approval.validate(bound)
        if bound != binding(str(bound["run_id"]), path):
            raise PermissionError("current source/protocol binding mismatch")
        self.path, self.bound = path, bound.copy()
        self.run_id = str(bound["run_id"])
        self._lock = threading.RLock()
        self._ordinal = 0
        self.provider_claimed = False
        self._previous = "0" * 64
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        try:
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.execute("PRAGMA journal_mode=DELETE")
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, binding TEXT NOT NULL, "
                "approval_id TEXT UNIQUE NOT NULL, status TEXT NOT NULL)"
            )
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS entries (run_id TEXT, ordinal INTEGER, payload TEXT, "
                "digest TEXT, PRIMARY KEY(run_id, ordinal))"
            )
            with self._db:
                self._db.execute(
                    "INSERT INTO runs VALUES (?, ?, ?, 'CLAIMED')",
                    (self.run_id, canonical_json(bound), approval.approval_id),
                )
        except sqlite3.Error:
            self._db.close()
            raise PermissionError(
                "run or approval already consumed, or registry unavailable"
            ) from None
        self.append("RUN_CLAIMED", {"binding": bound, "approval_id": approval.approval_id})

    def assert_current(self) -> None:
        if binding(self.run_id, self.path) != self.bound:
            raise PermissionError("current source/protocol binding mismatch")
        with self._lock:
            row = self._db.execute(
                "SELECT binding, status FROM runs WHERE run_id=?", (self.run_id,)
            ).fetchone()
        if row != (canonical_json(self.bound), "CLAIMED"):
            raise PermissionError("run is no longer open or its binding changed")

    def append(self, stage: str, data: JsonObject) -> None:
        with self._lock:
            payload = canonical_json(
                {
                    "ordinal": self._ordinal + 1,
                    "run_id": self.run_id,
                    "stage": stage,
                    "data": data,
                    "previous": self._previous,
                }
            )
            digest = hashlib.sha256(payload.encode()).hexdigest()
            try:
                with self._db:
                    self._db.execute(
                        "INSERT INTO entries VALUES (?, ?, ?, ?)",
                        (self.run_id, self._ordinal + 1, payload, digest),
                    )
            except sqlite3.Error:
                raise JournalFailure("JOURNAL_UNAVAILABLE") from None
            self._ordinal += 1
            self._previous = digest

    def finish(self, uncertain: bool) -> None:
        try:
            with self._lock:
                self.append("RUN_UNCERTAIN" if uncertain else "RUN_FINISHED", {})
                with self._db:
                    self._db.execute(
                        "UPDATE runs SET status=? WHERE run_id=?",
                        ("UNCERTAIN" if uncertain else "FINISHED", self.run_id),
                    )
        except sqlite3.Error:
            raise JournalFailure("JOURNAL_UNAVAILABLE") from None

    def records(self) -> list[JsonObject]:
        with self._lock:
            rows = self._db.execute(
                "SELECT payload, digest FROM entries WHERE run_id=? ORDER BY ordinal",
                (self.run_id,),
            ).fetchall()
        previous = "0" * 64
        records: list[JsonObject] = []
        for payload, digest in rows:
            row = cast(JsonObject, strict_json(payload))
            if (
                row["previous"] != previous
                or hashlib.sha256(payload.encode()).hexdigest() != digest
            ):
                raise JournalFailure("JOURNAL_INTEGRITY")
            records.append(row)
            previous = digest
        return records

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def snapshot(self) -> tuple[list[JsonObject], JsonObject]:
        with self._lock:
            records = self.records()
            status = self._db.execute(
                "SELECT status FROM runs WHERE run_id=?", (self.run_id,)
            ).fetchone()[0]
            tail = (
                hashlib.sha256(canonical_json(records[-1]).encode()).hexdigest()
                if records
                else "0" * 64
            )
            return records, {"entry_count": len(records), "tail_digest": tail, "run_status": status}


def proposal(run_id: str, registry: Path = REGISTRY) -> dict[str, Any]:
    return {
        "authorized": False,
        "plan": asdict(LifeExecutionPlan()),
        "binding": binding(run_id, registry),
        "protocol": PROTOCOL,
        "approval_template": {
            "approval_id": "SUPPLY-A-NEW-UUID-AFTER-HUMAN-APPROVAL",
            "approved_by": "human-user",
            "decision": "NOT_APPROVED",
            "scope": ["read-openai-api-key", "paid-responses"],
            "bound": binding(run_id, registry),
        },
    }
