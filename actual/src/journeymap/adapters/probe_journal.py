"""Probe-only durable single-use registry and strict, data-only journal reducer."""

import sqlite3
from pathlib import Path
from typing import cast

from journeymap.adapters.llm import strict_json
from journeymap.adapters.probe_diagnostics import TRANSPORT_ERRORS, validate_diagnostic
from journeymap.adapters.probe_execution import (
    CONTINUE,
    PROTOCOL,
    Ledger,
    ProbeApproval,
    ProbePlan,
    binding,
    classify,
    object_,
    rows,
    same,
)
from journeymap.core.canonical import (
    JsonObject,
    JsonValue,
    canonical_json,
    clone_json_object,
    state_digest,
)

ZERO = "0" * 64


def reduce_trace(bound: JsonObject, corpus: JsonObject, entries: list[JsonObject]) -> JsonObject:
    if bound.get("protocol") != PROTOCOL or not same(bound.get("plan"), ProbePlan().data):
        raise ValueError("not a v2 probe trace")
    return reduce_with_ledger(bound, corpus, entries, Ledger())


def reduce_with_ledger(
    bound: JsonObject, corpus: JsonObject, entries: list[JsonObject], ledger: Ledger
) -> JsonObject:
    """Require raw-response -> settlement links even if an attacker reseals hashes."""
    requests = rows(corpus["requests"])
    seen = set[str]()
    packets: list[JsonValue] = []
    outcomes: list[JsonValue] = []
    diagnostics: list[JsonValue] = []
    phase, stop, previous = "NEW", None, ZERO
    pending: JsonObject | None = None
    result: JsonObject | None = None
    approval: JsonObject | None = None
    sends = 0
    for ordinal, entry in enumerate(entries, 1):
        if set(entry) != {"ordinal", "run_id", "previous", "stage", "data"} or not same(
            {k: entry[k] for k in ("ordinal", "run_id", "previous")},
            {"ordinal": ordinal, "run_id": bound["run_id"], "previous": previous},
        ):
            raise ValueError("invalid journal sequence")
        stage, data = entry["stage"], object_(entry["data"])
        if stage == "RUN_CLAIMED" and phase == "NEW":
            if set(data) != {"binding", "approval", "evidence_kind"} or not same(
                data["binding"], bound
            ):
                raise ValueError("claim binding mismatch")
            approval = object_(data["approval"])
            if set(approval) != {"approval_id", "approved_by", "decision", "scope"}:
                raise ValueError("invalid approval evidence")
            synthetic = data["evidence_kind"] == "synthetic"
            if data["evidence_kind"] not in {"synthetic", "operator-approved-http"}:
                raise ValueError("invalid evidence kind")
            scope = approval["scope"]
            if not isinstance(scope, list):
                raise ValueError("invalid scope")
            ProbeApproval(
                str(approval["approval_id"]),
                str(approval["approved_by"]),
                str(approval["decision"]),
                tuple(cast(list[str], scope)),
                bound,
            ).validate(bound, synthetic=synthetic)
            phase = "IDLE"
        elif stage == "RESERVED" and phase == "IDLE" and stop is None:
            index = ledger.attempts
            ledger.reserve()
            request = requests[index]
            expected: JsonObject = {
                "request_id": request["request_id"],
                "body_digest": request["body_digest"],
                "ledger": ledger.data,
            }
            if not same(data, expected):
                raise ValueError("reservation or schedule mismatch")
            phase = "RESERVED"
        elif stage == "NOT_SENT" and phase == "RESERVED":
            if set(data) != {"reason", "diagnostic"} or data["reason"] != "CREDENTIAL_UNAVAILABLE":
                raise ValueError("invalid not-sent evidence")
            validate_diagnostic(
                data["diagnostic"],
                run_id=str(bound["run_id"]),
                request=requests[ledger.attempts - 1],
                event="NOT_SENT",
                ordinal=ordinal,
                error=data["reason"],
            )
            diagnostics.append(data["diagnostic"])
            ledger.reserved_cost_nano_usd = 0
            stop, phase = "CREDENTIAL_UNAVAILABLE", "IDLE"
        elif stage == "ATTEMPTED" and phase == "RESERVED":
            request = requests[ledger.attempts - 1]
            if not same(data, {"request_id": request["request_id"], "body": request["body"]}):
                raise ValueError("outgoing body mismatch")
            sends += 1
            phase = "ATTEMPTED"
        elif stage == "RESPONSE" and phase == "ATTEMPTED":
            if set(data) != {"packet", "transport_error", "diagnostic"}:
                raise ValueError("invalid response evidence")
            pending = object_(data["packet"])
            if set(pending) != {
                "request_id",
                "body_digest",
                "http_status",
                "raw_response",
                "elapsed_ms",
            }:
                raise ValueError("invalid packet")
            request = requests[ledger.attempts - 1]
            if any(pending[k] != request[k] for k in ("request_id", "body_digest")):
                raise ValueError("packet/request mismatch")
            status, raw, elapsed = (
                pending["http_status"],
                pending["raw_response"],
                pending["elapsed_ms"],
            )
            if (
                (status is not None and (type(status) is not int or not 100 <= status <= 599))
                or (raw is not None and type(raw) is not str)
                or type(elapsed) is not int
                or elapsed < 0
            ):
                raise ValueError("invalid response envelope")
            error = data["transport_error"]
            if error is not None and (
                type(error) is not str or error not in TRANSPORT_ERRORS or raw is not None
            ):
                raise ValueError("invalid transport error")
            validate_diagnostic(
                data["diagnostic"],
                run_id=str(bound["run_id"]),
                request=request,
                event="RESPONSE",
                ordinal=ordinal,
                error=error,
                status=status,
                raw=raw,
                elapsed=elapsed,
                synthetic=synthetic,
            )
            diagnostics.append(data["diagnostic"])
            result = classify(pending, seen)
            if error is not None:
                result["classification"] = error
            phase = "RECEIVED"
        elif stage == "SETTLED" and phase == "RECEIVED":
            assert result is not None and pending is not None
            ledger.settle(result)
            if not same(data, {"result": result, "ledger": ledger.data}):
                raise ValueError("settlement mismatch")
            packets.append(pending)
            outcomes.append(result)
            if result["classification"] not in CONTINUE:
                stop = str(result["classification"])
            phase = "IDLE"
        elif stage == "RUN_STOPPED" and phase == "IDLE":
            expected_stop = stop or (
                "ALL_SLOTS_ATTEMPTED" if ledger.attempts == len(requests) else ledger.blocked()
            )
            if expected_stop is None or not same(
                data, {"reason": expected_stop, "ledger": ledger.data}
            ):
                raise ValueError("unsupported termination or accounting")
            stop, phase = expected_stop, "DONE"
        else:
            raise ValueError("invalid journal state transition")
        previous = state_digest(entry)
    status = (
        "UNCERTAIN"
        if phase == "DONE" and ledger.reserved_cost_nano_usd
        else "FINISHED"
        if phase == "DONE"
        else "OPEN"
    )
    return {
        "status": status,
        "phase": phase,
        "stop_reason": stop,
        "ledger": ledger.data,
        "packets": packets,
        "outcomes": outcomes,
        "diagnostics": diagnostics,
        "attempted_sends": sends,
        "approval": approval,
        "total_cost_nano_usd": ledger.known_cost_nano_usd
        if phase == "DONE" and not ledger.reserved_cost_nano_usd
        else None,
        "snapshot": {"entry_count": len(entries), "tail_digest": previous, "status": status},
    }


class ProbeJournal:
    """Every event, tail and status commit atomically under SQLite FULL.

    One active run per registry. IDs remain consumed after interruption; no
    resume/reset API. Filesystem and operator are trusted, as for life admission.
    """

    def __init__(
        self,
        path: Path,
        bound: JsonObject,
        approval: ProbeApproval,
        corpus: JsonObject,
        *,
        synthetic: bool,
    ) -> None:
        approval.validate(bound, synthetic=synthetic)
        if not same(bound, binding(str(bound["run_id"]), path, corpus)):
            raise PermissionError("current source/plan binding mismatch")
        if path.name != "probe-runs.sqlite3" or str(path.resolve()) != bound["registry"]:
            raise PermissionError("registry binding mismatch")
        self.bound, self.corpus = clone_json_object(bound), clone_json_object(corpus)
        self.entries: list[JsonObject] = []
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        created = False
        try:
            with path.open("xb"):
                created = True
        except FileExistsError:
            pass
        self.db = sqlite3.connect(path, timeout=0)
        try:
            if not created:
                marker = self.db.execute("SELECT version FROM probe_meta").fetchall()
                if marker != [(PROTOCOL,)]:
                    raise ValueError("not a probe registry")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("PRAGMA journal_mode=DELETE")
            if created:
                self.db.executescript(
                    "CREATE TABLE probe_meta(version TEXT NOT NULL);"
                    "CREATE TABLE probe_runs(run_id TEXT PRIMARY KEY, "
                    "approval_id TEXT UNIQUE NOT NULL, binding TEXT NOT NULL, "
                    "status TEXT NOT NULL, n INTEGER NOT NULL, tail TEXT NOT NULL);"
                    "CREATE TABLE probe_entries(run_id TEXT NOT NULL, ordinal INTEGER NOT NULL, "
                    "payload TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(run_id,ordinal));"
                )
                with self.db:
                    self.db.execute("INSERT INTO probe_meta VALUES (?)", (PROTOCOL,))
            self.db.execute("BEGIN IMMEDIATE")
            if self.db.execute("SELECT 1 FROM probe_runs WHERE status != 'FINISHED'").fetchone():
                raise PermissionError("registry contains an open or uncertain run")
            self.db.execute(
                "INSERT INTO probe_runs VALUES (?, ?, ?, 'OPEN', 0, ?)",
                (bound["run_id"], approval.approval_id, canonical_json(bound), ZERO),
            )
            self._append(
                "RUN_CLAIMED",
                {
                    "binding": bound,
                    "evidence_kind": "synthetic" if synthetic else "operator-approved-http",
                    "approval": {
                        "approval_id": approval.approval_id,
                        "approved_by": approval.approved_by,
                        "decision": approval.decision,
                        "scope": list(approval.scope),
                    },
                },
            )
        except BaseException:
            self.db.rollback()
            self.db.close()
            raise

    def _append(self, stage: str, data: JsonObject) -> None:
        previous = state_digest(self.entries[-1]) if self.entries else ZERO
        entry: JsonObject = {
            "ordinal": len(self.entries) + 1,
            "run_id": self.bound["run_id"],
            "previous": previous,
            "stage": stage,
            "data": clone_json_object(data),
        }
        new = [*self.entries, entry]
        reduced = reduce_trace(self.bound, self.corpus, new)
        with self.db:
            stored = self.db.execute(
                "SELECT binding,status,n,tail FROM probe_runs WHERE run_id=?",
                (self.bound["run_id"],),
            ).fetchone()
            if stored != (canonical_json(self.bound), "OPEN", len(self.entries), previous):
                raise ValueError("registry changed or run closed")
            self.db.execute(
                "INSERT INTO probe_entries VALUES (?, ?, ?, ?)",
                (self.bound["run_id"], len(new), canonical_json(entry), state_digest(entry)),
            )
            self.db.execute(
                "UPDATE probe_runs SET status=?,n=?,tail=? WHERE run_id=?",
                (reduced["status"], len(new), state_digest(entry), self.bound["run_id"]),
            )
        self.entries = new

    def append(self, stage: str, data: JsonObject) -> None:
        try:
            self._append(stage, data)
        except (OSError, sqlite3.Error):
            # Stop with a visible fixed error, never storage exception text/args.
            # A commit may already have succeeded; do not compensate or resume.
            raise RuntimeError("probe journal write failed; no further sends") from None

    def assert_current(self) -> None:
        if not same(self.bound, binding(str(self.bound["run_id"]), self.path, self.corpus)):
            raise PermissionError("source changed; run remains open")

    def close(self) -> None:
        self.db.close()


def read_journal(path: Path, run_id: str) -> JsonObject:
    """Read-only transaction also works after a crash; never marks a run finished."""
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("BEGIN")
        if db.execute("SELECT version FROM probe_meta").fetchall() != [(PROTOCOL,)]:
            raise ValueError("not a probe registry")
        run = db.execute(
            "SELECT approval_id,binding,status,n,tail FROM probe_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if run is None:
            raise ValueError("unknown run")
        entries: list[JsonValue] = []
        for ordinal, payload, digest in db.execute(
            "SELECT ordinal,payload,digest FROM probe_entries WHERE run_id=? ORDER BY ordinal",
            (run_id,),
        ):
            entry = object_(strict_json(payload))
            if entry.get("ordinal") != ordinal or state_digest(entry) != digest:
                raise ValueError("journal integrity mismatch")
            entries.append(entry)
        return {
            "binding": object_(strict_json(run[1])),
            "approval_id": run[0],
            "entries": entries,
            "snapshot": {"status": run[2], "entry_count": run[3], "tail_digest": run[4]},
        }
    finally:
        db.close()
