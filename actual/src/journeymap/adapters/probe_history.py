"""Read-only complete history intake, verified by the matching frozen runtime."""

import hashlib
import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from journeymap.adapters.probe_continuation import PRIOR_KNOWN, PRIOR_UNRESOLVED
from journeymap.adapters.probe_execution import object_, rows, same
from journeymap.core.canonical import (
    JsonObject,
    JsonValue,
    clone_json_object,
    state_digest,
)

HISTORY_VERSION = "probe-imported-history-1"
_AUDITED: set[tuple[str, str]] = set()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_identity(source: Path) -> str:
    package = source.resolve() / "journeymap"
    files = sorted(package.rglob("*.py"))
    if not files:
        raise ValueError("frozen runtime missing")
    d = hashlib.sha256()
    for p in files:
        d.update(p.relative_to(package).as_posix().encode())
        d.update(p.read_bytes().replace(b"\r\n", b"\n"))
    return d.hexdigest()


def no_sqlite_sidecars(path: Path) -> None:
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise PermissionError("SQLite sidecar present; stop and inspect history")


@dataclass(frozen=True, slots=True)
class HistorySpec:
    registry: Path
    reports: tuple[Path, ...]
    frozen_source: Path
    expected_registry_sha256: str
    expected_runtime_identity: str


_AUDIT_PROGRAM = r"""
import hashlib,http.client,json,pathlib,socket,sys
def forbidden(*a,**k): raise AssertionError('external access forbidden')
socket.socket=socket.getaddrinfo=socket.create_connection=forbidden
http.client.HTTPSConnection=forbidden
try:
 p=json.load(sys.stdin); src=pathlib.Path(p['source']).resolve();sys.path.insert(0,str(src))
 from journeymap.adapters import life_http
 from journeymap.adapters.life_admission import source_identity
 from journeymap.experiments import paid_probe
 assert pathlib.Path(paid_probe.__file__).resolve().is_relative_to(src)
 assert source_identity()==p['identity']
 life_http.real_credential=life_http.real_connection=forbidden
 paid_probe.real_credential=paid_probe.real_connection=forbidden
 for text in p['reports']:
  result=paid_probe.audit_report(json.loads(text))
  assert result['data_only_equal'] is True and result['provider_recalled'] is False
 print(json.dumps({'source':source_identity(),'reports_audited':len(p['reports']),'provider_recalled':False}))
except BaseException:
 print('frozen history audit failed',file=sys.stderr);sys.exit(1)
"""


def _frozen_audit(bundle: JsonObject, source: Path) -> None:
    identity = runtime_identity(source)
    if identity != bundle["runtime_identity"]:
        raise PermissionError("history runtime identity mismatch")
    key = (state_digest(bundle), identity)
    if key not in _AUDITED:
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-c", _AUDIT_PROGRAM],
            input=json.dumps(
                {
                    "source": str(source.resolve()),
                    "identity": identity,
                    "reports": [r["text"] for r in rows(bundle["reports"])],
                }
            ),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise PermissionError("frozen history audit failed")
        if runtime_identity(source) != identity:
            raise PermissionError("history runtime changed during verification")
        _AUDITED.add(key)


def verify_bundle(bundle: JsonObject, frozen_source: Path) -> JsonObject:
    """Portable verification of a captured complete inventory and original report bytes.

    Physical inventory completeness is additionally checked from SQLite at intake/claim.
    An internally consistent bundle does not authenticate an external database by itself.
    """
    if (
        set(bundle)
        != {
            "version",
            "registry_path",
            "registry_sha256",
            "runtime_path",
            "runtime_identity",
            "inventory",
            "reports",
        }
        or bundle["version"] != HISTORY_VERSION
    ):
        raise ValueError("invalid history bundle")
    inventory = object_(bundle["inventory"])
    if set(inventory) != {"protocol", "runs"} or inventory["protocol"] not in {
        "life-reasoning-probe-execution-1",
        "life-reasoning-probe-execution-2",
    }:
        raise ValueError("unsupported history protocol")
    old_runs, reports = rows(inventory["runs"]), rows(bundle["reports"])
    if len(old_runs) != 1 or len(reports) != 1:
        raise PermissionError("limited policy requires exactly the reviewed prior probe history")
    parsed: dict[str, JsonObject] = {}
    hashes: list[JsonValue] = []
    for item in reports:
        if set(item) != {"path", "sha256", "text"} or type(item["text"]) is not str:
            raise ValueError("invalid history report reference")
        text = item["text"]
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != item["sha256"]:
            raise ValueError("history report hash mismatch")
        report = object_(json.loads(text))
        journal = object_(report["journal"])
        rid = str(object_(journal["binding"])["run_id"])
        if rid in parsed:
            raise ValueError("duplicate history report")
        parsed[rid] = report
        hashes.append({"run_id": rid, "sha256": item["sha256"]})
    if set(parsed) != {str(r["run_id"]) for r in old_runs}:
        raise ValueError("history inventory/report list mismatch")
    consumed: list[JsonValue] = []
    target: JsonObject | None = None
    for old in old_runs:
        if set(old) != {
            "run_id",
            "approval_id",
            "binding",
            "status",
            "entry_count",
            "tail_digest",
            "entries",
        }:
            raise ValueError("invalid original run inventory")
        report = parsed[str(old["run_id"])]
        j = object_(report["journal"])
        expected: JsonObject = {
            "binding": old["binding"],
            "approval_id": old["approval_id"],
            "entries": old["entries"],
            "snapshot": {
                "status": old["status"],
                "entry_count": old["entry_count"],
                "tail_digest": old["tail_digest"],
            },
        }
        if not same(j, expected) or report["protocol"] != inventory["protocol"]:
            raise ValueError("original journal/report mismatch")
        if object_(j["binding"])["source_digest"] != bundle["runtime_identity"]:
            raise ValueError("original source mismatch")
        previous = "0" * 64
        for index, entry in enumerate(rows(old["entries"]), 1):
            if (
                entry.get("ordinal") != index
                or entry.get("previous") != previous
                or entry.get("run_id") != old["run_id"]
            ):
                raise ValueError("original journal sequence mismatch")
            previous = state_digest(entry)
        if previous != old["tail_digest"] or len(rows(old["entries"])) != old["entry_count"]:
            raise ValueError("original journal tail mismatch")
        account = object_(report["accounting"])
        ledger = object_(account["ledger"])
        unknown = [
            r for r in rows(object_(report["evaluation"])["rows"]) if r["cost_nano_usd"] is None
        ]
        if (
            old["status"] != "UNCERTAIN"
            or account["status"] != "UNCERTAIN"
            or account["total_cost_nano_usd"] is not None
            or account["attempted_sends"] != 17
            or old["entry_count"] != 70
            or ledger["known_cost_nano_usd"] != PRIOR_KNOWN
            or ledger["reserved_cost_nano_usd"] != PRIOR_UNRESOLVED
            or len(unknown) != 1
        ):
            raise PermissionError("prior history is outside the reviewed one-followup policy")
        request = [
            r
            for r in rows(object_(report["corpus"])["requests"])
            if r["request_id"] == unknown[0]["request_id"]
        ]
        if len(request) != 1 or unknown[0]["classification"] != "TRANSPORT_UNCERTAIN":
            raise ValueError("invalid prior uncertain target")
        target = {
            "run_id": old["run_id"],
            "request_id": request[0]["request_id"],
            "body_digest": request[0]["body_digest"],
            "protocol": inventory["protocol"],
            "source": bundle["runtime_identity"],
            "entry_count": old["entry_count"],
            "tail_digest": old["tail_digest"],
            "status": "UNCERTAIN",
        }
        consumed.extend(
            [
                {"kind": "run", "id": old["run_id"]},
                {"kind": "paid-approval", "id": old["approval_id"]},
            ]
        )
    if len({str(object_(v)["id"]) for v in consumed}) != len(consumed):
        raise ValueError("duplicate original consumed ID")
    _frozen_audit(bundle, frozen_source)  # Never accepts a caller's audit=true assertion.
    return {
        "version": HISTORY_VERSION,
        "registry_path": bundle["registry_path"],
        "registry_sha256": bundle["registry_sha256"],
        "runtime_identity": bundle["runtime_identity"],
        "inventory_digest": state_digest(inventory),
        "report_hashes": hashes,
        "consumed_ids": consumed,
        "uncertain_target": target,
        "prior_known_cost_nano_usd": PRIOR_KNOWN,
        "prior_unresolved_nano_usd": PRIOR_UNRESOLVED,
        "prior_total_cost_nano_usd": None,
    }


def verify_history(spec: HistorySpec) -> tuple[JsonObject, JsonObject]:
    """No creation, recovery, writes, hidden subset import, or ignored SQLite sidecars."""
    path = spec.registry.resolve()
    no_sqlite_sidecars(path)
    if not path.is_file() or file_digest(path) != spec.expected_registry_sha256:
        raise PermissionError("original registry missing or changed")
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    inventory_runs: list[JsonValue] = []
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        versions = db.execute("SELECT version FROM probe_meta").fetchall()
        if len(versions) != 1:
            raise ValueError("invalid original registry marker")
        for rid, aid, b, status, count, tail in db.execute(
            "SELECT run_id,approval_id,binding,status,n,tail FROM probe_runs ORDER BY run_id"
        ):
            entries: list[JsonValue] = []
            for ordinal, payload, digest in db.execute(
                "SELECT ordinal,payload,digest FROM probe_entries WHERE run_id=? ORDER BY ordinal",
                (rid,),
            ):
                entry = object_(json.loads(payload))
                if entry.get("ordinal") != ordinal or state_digest(entry) != digest:
                    raise ValueError("original stored entry digest mismatch")
                entries.append(entry)
            inventory_runs.append(
                {
                    "run_id": rid,
                    "approval_id": aid,
                    "binding": object_(json.loads(b)),
                    "status": status,
                    "entry_count": count,
                    "tail_digest": tail,
                    "entries": entries,
                }
            )
        if db.execute("SELECT COUNT(*) FROM probe_entries").fetchone()[0] != sum(
            len(rows(object_(r)["entries"])) for r in inventory_runs
        ):
            raise ValueError("orphan original journal entries")
    finally:
        db.close()
    report_inputs: list[JsonValue] = []
    for report_path in spec.reports:
        data = report_path.read_bytes()
        report_inputs.append(
            {
                "path": str(report_path.resolve()),
                "sha256": hashlib.sha256(data).hexdigest(),
                "text": data.decode("utf-8"),
            }
        )
    bundle: JsonObject = {
        "version": HISTORY_VERSION,
        "registry_path": str(path),
        "registry_sha256": spec.expected_registry_sha256,
        "runtime_path": str(spec.frozen_source.resolve()),
        "runtime_identity": spec.expected_runtime_identity,
        "inventory": {"protocol": versions[0][0], "runs": inventory_runs},
        "reports": report_inputs,
    }
    manifest = verify_bundle(bundle, spec.frozen_source)
    no_sqlite_sidecars(path)
    if file_digest(path) != spec.expected_registry_sha256:
        raise PermissionError("history changed while being audited")
    for item in rows(bundle["reports"]):
        if file_digest(Path(str(item["path"]))) != item["sha256"]:
            raise PermissionError("history report changed while being audited")
    return clone_json_object(bundle), manifest


def spec_from_bundle(bundle: JsonObject) -> HistorySpec:
    return HistorySpec(
        Path(str(bundle["registry_path"])),
        tuple(Path(str(r["path"])) for r in rows(bundle["reports"])),
        Path(str(bundle["runtime_path"])),
        str(bundle["registry_sha256"]),
        str(bundle["runtime_identity"]),
    )
