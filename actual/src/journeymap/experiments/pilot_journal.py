"""New append-only preflight evidence; never opens an existing file for writing."""

import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from journeymap.adapters.llm import Record, strict_json
from journeymap.core.canonical import JsonObject

JOURNAL_VERSION = "memory-horizon-attempt-journal-1"


def digest(value: JsonObject) -> str:
    # Escapes preserve malformed raw text, including unpaired surrogates.
    return sha256(Record.capture(value).serialized.encode("utf-8")).hexdigest()


class AttemptJournal:
    def __init__(self, path: Path) -> None:
        self._file = path.open("x", encoding="utf-8", newline="\n")
        self._sequence = 0
        self._previous = "0" * 64
        self.failed = False

    def append(self, kind: str, data: JsonObject) -> None:
        if self.failed:
            raise OSError("journal persistence already failed")
        row: JsonObject = {
            "journal_version": JOURNAL_VERSION,
            "sequence": self._sequence + 1,
            "previous_sha256": self._previous,
            "kind": kind,
            "data": data,
        }
        seal = digest(row)
        try:
            self._file.write(Record.capture({**row, "sha256": seal}).serialized + "\n")
            self._file.flush()
            os.fsync(self._file.fileno())
        except OSError:
            self.failed = True
            raise OSError("journal persistence failed; inspect durable prefix") from None
        self._sequence += 1
        self._previous = seal

    def close(self) -> None:
        self._file.close()


@dataclass(frozen=True)
class JournalRead:
    rows: list[JsonObject]
    status: str
    error_sequence: int | None = None


def inspect_journal(path: Path) -> JournalRead:
    """Read-only durable prefix. Only an unterminated FINAL record may be torn.

    A complete corrupt line (even the last one) is never recovered as a torn tail.
    Hashes establish internal consistency, not signatures or physical durability.
    """
    payload = path.read_bytes()
    previous = "0" * 64
    rows: list[JsonObject] = []
    for sequence, line in enumerate(payload.splitlines(keepends=True), 1):
        terminated = line.endswith(b"\n")
        try:
            row = strict_json(line.decode("utf-8"))
        except (UnicodeError, ValueError):
            return JournalRead(rows, "CORRUPT_RECORD" if terminated else "TORN_TAIL", sequence)
        if not isinstance(row, dict) or set(row) != {
            "journal_version",
            "sequence",
            "previous_sha256",
            "kind",
            "data",
            "sha256",
        }:
            return JournalRead(rows, "CORRUPT_RECORD", sequence)
        seal = row.pop("sha256")
        if (
            row["journal_version"] != JOURNAL_VERSION
            or type(seal) is not str
            or type(row["sequence"]) is not int
            or row["sequence"] != sequence
            or row["previous_sha256"] != previous
            or seal != digest(row)
        ):
            return JournalRead(rows, "CHAIN_BREAK", sequence)
        if type(row["kind"]) is not str or not isinstance(row["data"], dict):
            return JournalRead(rows, "CORRUPT_RECORD", sequence)
        if not terminated:
            return JournalRead(rows, "TORN_TAIL", sequence)
        previous = seal
        rows.append(row)
    return JournalRead(rows, "COMPLETE")


def read_journal(path: Path) -> list[JsonObject]:
    result = inspect_journal(path)
    if result.status != "COMPLETE" or not result.rows:
        raise ValueError(f"empty, torn or corrupt journal: {result.status}")
    return result.rows


def write_snapshot(path: Path, data: JsonObject) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(Record.capture(data).serialized + "\n")
        stream.flush()
        os.fsync(stream.fileno())
