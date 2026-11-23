"""Fixed-path one-writer authority and probe journal, with a conservative attempt fence."""

import json
import os
import socket
import sqlite3
import sys
from pathlib import Path
from typing import BinaryIO

from journeymap.adapters.probe_continuation import (
    AUTHORITY_VERSION,
    POLICY,
    PROTOCOL,
    Anchor,
    ContinuationApproval,
    FollowupLedger,
    FollowupPlan,
    candidate_binding,
    exposure,
    timestamp,
    utc_now,
)
from journeymap.adapters.probe_execution import object_, rows, same, uuid_
from journeymap.adapters.probe_history import (
    HistorySpec,
    no_sqlite_sidecars,
    spec_from_bundle,
    verify_bundle,
    verify_history,
)
from journeymap.adapters.probe_journal import ProbeJournal, reduce_with_ledger
from journeymap.core.canonical import (
    JsonObject,
    JsonValue,
    canonical_json,
    clone_json_object,
    state_digest,
)

ZERO = "0" * 64
FRESH_TASK = "memoryreplica-independent-authority-1"


def device_id() -> str:
    # An operator binding, not cryptographic device authentication or a DNS lookup.
    return socket.gethostname()


class WriterLease:
    def __init__(self, path: Path) -> None:
        self.path = Path(str(path) + ".writer.lock")
        self.stream: BinaryIO | None = None

    def acquire(self) -> None:
        stream = self.path.open("a+b")
        try:
            if stream.seek(0, 2) == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            stream.close()
            raise PermissionError("authority writer is unavailable") from None
        self.stream = stream

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()  # OS releases the lock on close/process death.
            self.stream = None


def _meta(db: sqlite3.Connection) -> JsonObject:
    records = db.execute("SELECT payload FROM lineage_meta").fetchall()
    if len(records) != 1:
        raise ValueError("missing authority metadata")
    d = object_(json.loads(records[0][0]))
    if (
        set(d)
        != {"version", "authority_id", "path", "device", "history_digest", "history_bundle", "mode"}
        or d["version"] != AUTHORITY_VERSION
    ):
        raise ValueError("not a continuation authority")
    return d


def _anchor(meta: JsonObject, sequence: int, tail: str) -> Anchor:
    return Anchor.parse(
        {
            "authority_id": meta["authority_id"],
            "path": meta["path"],
            "device": meta["device"],
            "history_digest": meta["history_digest"],
            "sequence": sequence,
            "tail": tail,
        }
    )


def _event(
    db: sqlite3.Connection, sequence: int, previous: str, kind: str, data: JsonObject
) -> str:
    event: JsonObject = {"sequence": sequence, "previous": previous, "kind": kind, "data": data}
    digest = state_digest(event)
    db.execute(
        "INSERT INTO lineage_events VALUES (?,?,?)", (sequence, canonical_json(event), digest)
    )
    return digest


def _fence(authority_id: str, approval: ContinuationApproval) -> JsonObject:
    return {
        "authority_id": authority_id,
        "run_id": approval.bound["run_id"],
        "paid_approval_id": approval.approval_id,
        "risk_acceptance_id": approval.risk_acceptance_id,
        "approval_digest": state_digest(approval.data),
    }


def _bootstrap_bound(path: Path, authority_id: str, device: str, history: JsonObject) -> JsonObject:
    uuid_(authority_id)
    return {
        "policy_version": POLICY,
        "authority_id": authority_id,
        "path": str(path.resolve()),
        "device": device,
        "history_digest": state_digest(history),
        "registry_sha256": history["registry_sha256"],
        "inventory_digest": history["inventory_digest"],
    }


def _validate_bootstrap(
    permit: JsonObject, expected: JsonObject, synthetic: bool, now: str
) -> None:
    if set(permit) != {"schema", "approved_by", "decision", "valid_from", "expires_at", "bound"}:
        raise PermissionError("separate history import authorization required")
    if (
        permit["schema"] != "probe-history-import-approval-1"
        or permit["approved_by"] != ("offline-fixture" if synthetic else "human-user")
        or permit["decision"]
        != ("synthetic-history-import-only" if synthetic else "approve-probe-history-import")
        or not same(permit["bound"], expected)
    ):
        raise PermissionError("history import authorization mismatch")
    if (
        not timestamp(str(permit["valid_from"]))
        <= timestamp(now)
        < timestamp(str(permit["expires_at"]))
    ):
        raise PermissionError("history import authorization expired")


def bootstrap_binding(path: Path, authority_id: str, device: str, spec: HistorySpec) -> JsonObject:
    _, manifest = verify_history(spec)
    return _bootstrap_bound(path, authority_id, device, manifest)


def _create_authority_tables(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA journal_mode=DELETE")
    db.executescript(
        "BEGIN IMMEDIATE;"
        "CREATE TABLE lineage_meta(payload TEXT NOT NULL);"
        "CREATE TABLE lineage_events(seq INTEGER PRIMARY KEY,"
        "payload TEXT NOT NULL,digest TEXT NOT NULL);"
        "CREATE TABLE lineage_consumed(id TEXT PRIMARY KEY,"
        "kind TEXT NOT NULL,event_seq INTEGER NOT NULL);"
        "CREATE TABLE probe_meta(version TEXT NOT NULL);"
        "CREATE TABLE probe_runs(run_id TEXT PRIMARY KEY,"
        "approval_id TEXT UNIQUE NOT NULL,binding TEXT NOT NULL,"
        "status TEXT NOT NULL,n INTEGER NOT NULL,tail TEXT NOT NULL,corpus TEXT NOT NULL);"
        "CREATE TABLE probe_entries(run_id TEXT NOT NULL,"
        "ordinal INTEGER NOT NULL,payload TEXT NOT NULL,"
        "digest TEXT NOT NULL,PRIMARY KEY(run_id,ordinal));"
    )
    db.execute("INSERT INTO probe_meta VALUES (?)", (PROTOCOL,))


def fresh_task_parent(
    path: Path,
    authority_id: str,
    run_id: str,
    device: str,
    provenance_sha256: str,
    *,
    synthetic: bool,
) -> JsonObject:
    """Deterministic genesis for a single MemoryReplica retention run."""
    uuid_(authority_id)
    uuid_(run_id)
    if (
        authority_id == run_id
        or len(provenance_sha256) != 64
        or any(char not in "0123456789abcdef" for char in provenance_sha256)
    ):
        raise ValueError("invalid independent task identity")
    bundle: JsonObject = {
        "schema": FRESH_TASK,
        "run_id": run_id,
        "provenance_sha256": provenance_sha256,
    }
    meta: JsonObject = {
        "version": AUTHORITY_VERSION,
        "authority_id": authority_id,
        "path": str(path.resolve()),
        "device": device,
        "history_digest": state_digest(bundle),
        "history_bundle": bundle,
        "mode": "synthetic" if synthetic else "operator-asserted",
    }
    event: JsonObject = {
        "sequence": 1,
        "previous": ZERO,
        "kind": "INDEPENDENT_TASK_INITIALIZED",
        "data": meta,
    }
    return {
        "meta": meta,
        "events": [{"event": event, "digest": state_digest(event)}],
        "consumed": [],
        "runs": [],
    }


def fresh_task_bundle(snapshot: JsonObject) -> JsonObject | None:
    meta = snapshot.get("meta")
    if isinstance(meta, dict):
        bundle = meta.get("history_bundle")
        if isinstance(bundle, dict) and bundle.get("schema") == FRESH_TASK:
            return bundle
    return None


def fresh_task_anchor(snapshot: JsonObject) -> Anchor:
    meta = object_(snapshot["meta"])
    bundle = fresh_task_bundle(snapshot)
    if bundle is None or snapshot != fresh_task_parent(
        Path(str(meta["path"])),
        str(meta["authority_id"]),
        str(bundle["run_id"]),
        str(meta["device"]),
        str(bundle["provenance_sha256"]),
        synthetic=meta["mode"] == "synthetic",
    ):
        raise PermissionError("independent task authority must have a fresh genesis")
    genesis = rows(snapshot["events"])[0]
    return _anchor(meta, 1, str(genesis["digest"]))


def initialize_fresh_task_authority(path: Path, snapshot: JsonObject) -> Anchor:
    """Provision the exact empty authority. TaskJournal still requires a separate approval."""
    anchor = fresh_task_anchor(snapshot)
    path = path.resolve()
    if str(path) != anchor.path or device_id() != anchor.device:
        raise PermissionError("independent authority path/device mismatch")
    no_sqlite_sidecars(path)
    with path.open("xb"):
        pass
    db = sqlite3.connect(path, timeout=0)
    try:
        _create_authority_tables(db)
        meta = object_(snapshot["meta"])
        db.execute("INSERT INTO lineage_meta VALUES (?)", (canonical_json(meta),))
        _event(db, 1, ZERO, "INDEPENDENT_TASK_INITIALIZED", meta)
        db.commit()
    finally:
        db.close()
    return anchor


def bootstrap_authority(
    path: Path,
    spec: HistorySpec,
    authority_id: str,
    device: str,
    permit: JsonObject,
    *,
    synthetic: bool,
) -> Anchor:
    """Explicit provisioning only. Execution never invokes this or creates a missing DB."""
    bundle, history = verify_history(spec)
    path = path.resolve()
    now = utc_now()
    _validate_bootstrap(
        permit, _bootstrap_bound(path, authority_id, device, history), synthetic, now
    )
    if not path.parent.is_dir() or path.exists():
        raise PermissionError("authority must be explicitly provisioned at a new approved path")
    no_sqlite_sidecars(path)
    with path.open("xb"):
        pass
    db = sqlite3.connect(path, timeout=0)
    try:
        _create_authority_tables(db)
        mode = "synthetic" if synthetic else "operator-asserted"
        meta: JsonObject = {
            "version": AUTHORITY_VERSION,
            "authority_id": authority_id,
            "path": str(path),
            "device": device,
            "history_digest": state_digest(history),
            "history_bundle": bundle,
            "mode": mode,
        }
        db.execute("INSERT INTO lineage_meta VALUES (?)", (canonical_json(meta),))
        tail = _event(
            db,
            1,
            ZERO,
            "HISTORY_IMPORTED",
            {
                "history_manifest": history,
                "permit": permit,
                "created_at": now,
                "authority": {k: v for k, v in meta.items() if k != "history_bundle"},
            },
        )
        for consumed in rows(history["consumed_ids"]):
            db.execute(
                "INSERT INTO lineage_consumed VALUES (?,?,1)", (consumed["id"], consumed["kind"])
            )
        db.commit()
        return _anchor(meta, 1, tail)
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def snapshot_db(db: sqlite3.Connection) -> JsonObject:
    meta = _meta(db)
    if db.execute("SELECT version FROM probe_meta").fetchall() != [(PROTOCOL,)]:
        raise ValueError("invalid continuation marker")
    events: list[JsonValue] = []
    for seq, text, digest in db.execute(
        "SELECT seq,payload,digest FROM lineage_events ORDER BY seq"
    ):
        event = object_(json.loads(text))
        if event.get("sequence") != seq or state_digest(event) != digest:
            raise ValueError("authority event digest mismatch")
        events.append({"event": event, "digest": digest})
    runs: list[JsonValue] = []
    total_entries = 0
    for rid, aid, bound, status, n, tail, corpus in db.execute(
        "SELECT run_id,approval_id,binding,status,n,tail,corpus FROM probe_runs ORDER BY run_id"
    ):
        entries: list[JsonValue] = []
        for ordinal, text, digest in db.execute(
            "SELECT ordinal,payload,digest FROM probe_entries WHERE run_id=? ORDER BY ordinal",
            (rid,),
        ):
            entry = object_(json.loads(text))
            if entry.get("ordinal") != ordinal or state_digest(entry) != digest:
                raise ValueError("continuation entry digest mismatch")
            entries.append(entry)
        total_entries += len(entries)
        runs.append(
            {
                "run_id": rid,
                "approval_id": aid,
                "binding": object_(json.loads(bound)),
                "status": status,
                "n": n,
                "tail": tail,
                "corpus": object_(json.loads(corpus)),
                "entries": entries,
            }
        )
    if db.execute("SELECT COUNT(*) FROM probe_entries").fetchone()[0] != total_entries:
        raise ValueError("orphan continuation entries")
    consumed: list[JsonValue] = [
        {"id": r[0], "kind": r[1], "event_seq": r[2]}
        for r in db.execute("SELECT id,kind,event_seq FROM lineage_consumed ORDER BY id")
    ]
    return {"meta": meta, "events": events, "consumed": consumed, "runs": runs}


def read_snapshot(path: Path) -> JsonObject:
    path = path.resolve()
    no_sqlite_sidecars(path)
    if not path.is_file():
        raise PermissionError("approved authority is missing; no initialization fallback")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        return snapshot_db(db)
    finally:
        db.close()


def reduce_candidate_trace(
    bound: JsonObject, corpus: JsonObject, entries: list[JsonObject]
) -> JsonObject:
    if bound.get("protocol") != PROTOCOL or not same(bound.get("plan"), FollowupPlan().data):
        raise ValueError("invalid continuation plan or protocol")
    return reduce_with_ledger(bound, corpus, entries, FollowupLedger())


def check_snapshot(
    snapshot: JsonObject, frozen_source: Path
) -> tuple[Anchor, JsonObject, JsonObject | None]:
    """Pure snapshot replay. It neither checks current writer absence nor grants execution."""
    if set(snapshot) != {"meta", "events", "consumed", "runs"}:
        raise ValueError("invalid authority snapshot")
    meta = object_(snapshot["meta"])
    if (
        set(meta)
        != {"version", "authority_id", "path", "device", "history_digest", "history_bundle", "mode"}
        or meta["version"] != AUTHORITY_VERSION
    ):
        raise ValueError("invalid authority metadata")
    if meta["mode"] not in {"synthetic", "operator-asserted"}:
        raise ValueError("invalid authority mode")
    history = verify_bundle(object_(meta["history_bundle"]), frozen_source)
    if state_digest(history) != meta["history_digest"]:
        raise ValueError("imported history digest mismatch")
    events, runs = rows(snapshot["events"]), rows(snapshot["runs"])
    if not events or len(runs) > 1:
        raise ValueError("missing import or multiple successors")
    consumed: list[JsonValue] = []
    previous, claimed, count = ZERO, False, 0
    reduced: JsonObject | None = None
    run = runs[0] if runs else None
    if run is not None and set(run) != {
        "run_id",
        "approval_id",
        "binding",
        "status",
        "n",
        "tail",
        "corpus",
        "entries",
    }:
        raise ValueError("invalid successor row schema")
    if run is not None:
        from journeymap.experiments import life_reasoning_probe as probe

        if not same(object_(run["corpus"]), probe.prepare()):
            raise ValueError("continuation source/corpus mismatch")
    for sequence, wrapper in enumerate(events, 1):
        if set(wrapper) != {"event", "digest"}:
            raise ValueError("invalid event wrapper")
        event = object_(wrapper["event"])
        if (
            set(event) != {"sequence", "previous", "kind", "data"}
            or type(event["sequence"]) is not int
            or event["sequence"] != sequence
            or event["previous"] != previous
            or state_digest(event) != wrapper["digest"]
        ):
            raise ValueError("authority event sequence mismatch")
        data = object_(event["data"])
        kind = event["kind"]
        if sequence == 1 and kind == "HISTORY_IMPORTED":
            if set(data) != {"history_manifest", "permit", "created_at", "authority"} or not same(
                data["history_manifest"], history
            ):
                raise ValueError("history import event mismatch")
            if not same(
                data["authority"], {k: v for k, v in meta.items() if k != "history_bundle"}
            ):
                raise ValueError("authority identity mismatch")
            expected_bootstrap = {
                "policy_version": POLICY,
                "authority_id": meta["authority_id"],
                "path": meta["path"],
                "device": meta["device"],
                "history_digest": meta["history_digest"],
                "registry_sha256": history["registry_sha256"],
                "inventory_digest": history["inventory_digest"],
            }
            _validate_bootstrap(
                object_(data["permit"]),
                expected_bootstrap,
                meta["mode"] == "synthetic",
                str(data["created_at"]),
            )
            consumed.extend(
                {"id": c["id"], "kind": c["kind"], "event_seq": 1}
                for c in rows(history["consumed_ids"])
            )
        elif kind == "SUCCESSOR_CLAIMED" and sequence == 2 and not claimed and run is not None:
            if set(data) != {"approval", "claimed_at", "binding", "fence"}:
                raise ValueError("invalid successor claim")
            a = ContinuationApproval.parse(data["approval"])
            bound = object_(data["binding"])
            expected = candidate_binding(
                str(run["run_id"]),
                a.approval_id,
                a.risk_acceptance_id,
                object_(run["corpus"]),
                _anchor(meta, 1, previous),
                history,
            )
            if (
                not same(bound, expected)
                or not same(run["binding"], expected)
                or run["approval_id"] != a.approval_id
            ):
                raise ValueError("successor binding mismatch")
            synthetic = a.approved_by == "offline-fixture"
            if not synthetic and meta["mode"] != "operator-asserted":
                raise PermissionError("synthetic authority cannot authorize HTTP")
            a.validate(expected, synthetic=synthetic, now=str(data["claimed_at"]))
            if data["fence"] != state_digest(_fence(str(meta["authority_id"]), a)):
                raise ValueError("attempt fence binding mismatch")
            for value, role in (
                (run["run_id"], "run"),
                (a.approval_id, "paid-approval"),
                (a.risk_acceptance_id, "risk-acceptance"),
            ):
                if value in {object_(c)["id"] for c in consumed}:
                    raise ValueError("consumed lineage ID reused")
                consumed.append({"id": value, "kind": role, "event_seq": sequence})
            claimed = True
        elif kind == "JOURNAL_COMMITTED" and claimed and run is not None:
            count += 1
            entries = rows(run["entries"])
            if count > len(entries) or set(data) != {
                "run_id",
                "ordinal",
                "entry_digest",
                "status",
                "exposure",
            }:
                raise ValueError("journal/authority link missing")
            entry = entries[count - 1]
            if (
                data["run_id"] != run["run_id"]
                or type(data["ordinal"]) is not int
                or data["ordinal"] != count
                or data["entry_digest"] != state_digest(entry)
            ):
                raise ValueError("journal/authority link mismatch")
            reduced = reduce_candidate_trace(
                object_(run["binding"]), object_(run["corpus"]), entries[:count]
            )
            if data["status"] != reduced["status"] or not same(
                data["exposure"], exposure(object_(reduced["ledger"]))
            ):
                raise ValueError("lineage accounting mismatch")
            if count == 1:
                assert a is not None
                if not same(
                    object_(entry["data"])["approval"],
                    {
                        "approval_id": a.approval_id,
                        "approved_by": a.approved_by,
                        "decision": a.payment_projection(synthetic=synthetic).decision,
                        "scope": list(a.payment_projection(synthetic=synthetic).scope),
                    },
                ):
                    raise ValueError("payment and risk approval projection mismatch")
        else:
            raise ValueError("unsupported authority transition")
        previous = str(wrapper["digest"])
    if not same(sorted(consumed, key=lambda c: str(object_(c)["id"])), snapshot["consumed"]):
        raise ValueError("consumed ID index differs from authority events")
    if run is not None:
        if not claimed or reduced is None or count != len(rows(run["entries"])):
            raise ValueError("unlinked successor prefix")
        snap = object_(reduced["snapshot"])
        if not same(
            {"status": run["status"], "entry_count": run["n"], "tail_digest": run["tail"]}, snap
        ):
            raise ValueError("successor snapshot differs from replay")
    elif claimed:
        raise ValueError("missing successor")
    return _anchor(meta, len(events), previous), history, reduced


class ContinuationJournal(ProbeJournal):
    """No DB fallback. One-shot fence precedes atomic IDs+claim+journal transaction."""

    def __init__(
        self,
        path: Path,
        anchor: Anchor,
        approval: ContinuationApproval,
        corpus: JsonObject,
        *,
        synthetic: bool,
    ) -> None:
        self.lease = WriterLease(path.resolve())
        self._claim_committed = False
        self.synthetic = synthetic
        if str(path.resolve()) != anchor.path or device_id() != anchor.device:
            raise PermissionError("authority path or operating device mismatch")
        read_snapshot(
            path
        )  # Missing/empty/old-version authority rejected before creating any lease.
        self.lease.acquire()
        try:
            self.db = sqlite3.connect(path.resolve(), timeout=0)
        except BaseException:
            self.lease.close()
            raise
        self.path = path.resolve()
        try:
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("BEGIN IMMEDIATE")
            snapshot = snapshot_db(self.db)
            meta = object_(snapshot["meta"])
            current, history, _ = check_snapshot(
                snapshot, Path(str(object_(meta["history_bundle"])["runtime_path"]))
            )
            fresh_bundle, fresh_history = verify_history(
                spec_from_bundle(object_(meta["history_bundle"]))
            )
            if not same(fresh_bundle, meta["history_bundle"]) or not same(fresh_history, history):
                raise PermissionError("original history changed")
            if not same(current.data, anchor.data) or snapshot["runs"]:
                raise PermissionError("stale checkpoint or the one successor already consumed")
            if not synthetic and meta["mode"] != "operator-asserted":
                raise PermissionError("synthetic authority cannot authorize HTTP")
            bound = candidate_binding(
                str(approval.bound["run_id"]),
                approval.approval_id,
                approval.risk_acceptance_id,
                corpus,
                anchor,
                history,
            )
            claimed_at = utc_now()
            approval.validate(bound, synthetic=synthetic, now=claimed_at)
            ids = (str(bound["run_id"]), approval.approval_id, approval.risk_acceptance_id)
            if any(
                self.db.execute("SELECT 1 FROM lineage_consumed WHERE id=?", (value,)).fetchone()
                for value in ids
            ):
                raise PermissionError("lineage ID already consumed")
            if FollowupLedger().blocked() is not None:
                raise PermissionError("candidate cannot reserve its first request")
            if any(self.path.parent.glob(self.path.name + ".attempt-*")):
                raise FileExistsError("an earlier authority claim attempt is already fenced")
            fence = _fence(anchor.authority_id, approval)
            fence_path = Path(str(self.path) + ".attempt-" + approval.risk_acceptance_id)
            # An orphan fence is conservative evidence of an attempted claim, never permission.
            with fence_path.open("xb") as stream:
                stream.write(canonical_json(fence).encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            if sys.platform != "win32":
                directory_fd = os.open(str(fence_path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            tail = _event(
                self.db,
                2,
                anchor.tail,
                "SUCCESSOR_CLAIMED",
                {
                    "approval": approval.data,
                    "claimed_at": claimed_at,
                    "binding": bound,
                    "fence": state_digest(fence),
                },
            )
            for value, role in zip(ids, ("run", "paid-approval", "risk-acceptance"), strict=True):
                self.db.execute("INSERT INTO lineage_consumed VALUES (?,?,2)", (value, role))
            self.bound, self.corpus = clone_json_object(bound), clone_json_object(corpus)
            self.entries: list[JsonObject] = []
            self.history_bundle = clone_json_object(fresh_bundle)
            self.history = clone_json_object(history)
            self.tip = _anchor(meta, 2, tail)
            self.db.execute(
                "INSERT INTO probe_runs VALUES (?,?,?,'OPEN',0,?,?)",
                (
                    bound["run_id"],
                    approval.approval_id,
                    canonical_json(bound),
                    ZERO,
                    canonical_json(corpus),
                ),
            )
            payment = approval.payment_projection(synthetic=synthetic)
            self._append(
                "RUN_CLAIMED",
                {
                    "binding": bound,
                    "evidence_kind": "synthetic" if synthetic else "operator-approved-http",
                    "approval": {
                        "approval_id": payment.approval_id,
                        "approved_by": payment.approved_by,
                        "decision": payment.decision,
                        "scope": list(payment.scope),
                    },
                },
            )
            self._claim_committed = True
        except BaseException:
            self.db.rollback()
            self.db.close()
            self.lease.close()
            raise

    @property
    def permits_http(self) -> bool:
        return self._claim_committed and not self.synthetic and self.lease.stream is not None

    def assert_current(self) -> None:
        from journeymap.adapters.life_admission import source_identity

        if self.lease.stream is None:
            raise PermissionError("authority writer lease no longer held")
        if source_identity() != self.bound["source_digest"]:
            raise PermissionError("source changed; continuation remains open")
        bundle, history = verify_history(spec_from_bundle(self.history_bundle))
        if not same(bundle, self.history_bundle) or not same(history, self.history):
            raise PermissionError("prior history changed; continuation remains open")
        last = self.db.execute(
            "SELECT seq,digest FROM lineage_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        if last != (self.tip.sequence, self.tip.tail):
            raise PermissionError("authority checkpoint changed")

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
        reduced = reduce_candidate_trace(self.bound, self.corpus, new)
        if not self.db.in_transaction:
            self.db.execute("BEGIN IMMEDIATE")
        with self.db:
            if self.db.execute(
                "SELECT seq,digest FROM lineage_events ORDER BY seq DESC LIMIT 1"
            ).fetchone() != (self.tip.sequence, self.tip.tail):
                raise ValueError("authority writer changed")
            if self.db.execute(
                "SELECT status,n,tail FROM probe_runs WHERE run_id=?", (self.bound["run_id"],)
            ).fetchone() != ("OPEN", len(self.entries), previous):
                raise ValueError("continuation journal changed or closed")
            digest = state_digest(entry)
            self.db.execute(
                "INSERT INTO probe_entries VALUES (?,?,?,?)",
                (self.bound["run_id"], len(new), canonical_json(entry), digest),
            )
            self.db.execute(
                "UPDATE probe_runs SET status=?,n=?,tail=? WHERE run_id=?",
                (reduced["status"], len(new), digest, self.bound["run_id"]),
            )
            tail = _event(
                self.db,
                self.tip.sequence + 1,
                self.tip.tail,
                "JOURNAL_COMMITTED",
                {
                    "run_id": self.bound["run_id"],
                    "ordinal": len(new),
                    "entry_digest": digest,
                    "status": reduced["status"],
                    "exposure": exposure(object_(reduced["ledger"])),
                },
            )
        self.entries = new
        self.tip = Anchor(
            self.tip.authority_id,
            self.tip.path,
            self.tip.device,
            self.tip.history_digest,
            self.tip.sequence + 1,
            tail,
        )

    def close(self) -> None:
        self.db.close()
        self.lease.close()


def journal_from_run(run: JsonObject) -> JsonObject:
    return {
        "binding": run["binding"],
        "approval_id": run["approval_id"],
        "entries": run["entries"],
        "snapshot": {"status": run["status"], "entry_count": run["n"], "tail_digest": run["tail"]},
    }
