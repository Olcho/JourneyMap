"""MemoryReplica task contract over the existing Actual approval and nano-USD ledger."""

import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path, PureWindowsPath
from typing import Any, cast

from journeymap.adapters.probe_continuation import (
    Anchor,
    ContinuationApproval,
    timestamp,
)
from journeymap.adapters.probe_execution import ENDPOINT, Ledger, ProbePlan, uuid_
from journeymap.adapters.probe_history import runtime_identity
from journeymap.core.canonical import state_digest

PROTOCOL = "memoryreplica-actual-dynamic-1"
RETENTION_PROTOCOL = "memoryreplica-actual-outcome-retention-1"
RETENTION_WIRE = "memoryreplica-outcome-retention-1"
APPROVAL = "memoryreplica-actual-approval-1"
CASE_ORDER = ("none", "recent", "full", "recent", "full", "none", "full", "none", "recent")


def digest(value: Any) -> str:
    return state_digest(value)


def sources(root: Path) -> dict[str, str]:
    paths = [*sorted((root / "src").rglob("*.py")), root / "pyproject.toml"]
    if len(paths) < 2 or not paths[-1].is_file():
        raise ValueError("complete source root required")
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths
    }


def runtime() -> dict[str, Any]:
    return {
        "python": str(Path(sys.executable).resolve()),
        "version": list(sys.version_info[:3]),
        "sha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
    }


@dataclass(frozen=True)
class TaskPlan(ProbePlan):
    request_limit: int = 63
    total_input_tokens: int = 25_200_000
    total_output_tokens: int = 516_096
    cost_limit_nano_usd: int = 22_000_000_000
    plan_version: str = PROTOCOL


@dataclass(frozen=True)
class RetentionPlan(TaskPlan):
    """Fixed study allocation; zero default budget never inherits an old approval."""

    phase: str = "development"
    request_limit: int = 210
    total_input_tokens: int = 84_000_000
    total_output_tokens: int = 1_720_320
    cost_limit_nano_usd: int = 0
    plan_version: str = RETENTION_PROTOCOL

    def __post_init__(self) -> None:
        count = {"development": 210, "main": 2100}.get(self.phase)
        if count is None:
            raise ValueError("unknown retention phase")
        variable = {
            "phase",
            "request_limit",
            "total_input_tokens",
            "total_output_tokens",
            "cost_limit_nano_usd",
        }
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not type(field.default):
                raise ValueError("invalid retention plan type")
            if field.name not in variable and value != field.default:
                raise ValueError("fixed model/rates/transport contract changed")
        if (
            self.request_limit != count
            or self.total_input_tokens != count * self.context_input_reserve
            or self.total_output_tokens != count * self.max_output_tokens
            or self.cost_limit_nano_usd < 0
        ):
            raise ValueError("retention allocation differs from frozen protocol")

    @classmethod
    def for_phase(cls, phase: str, cost_limit_nano_usd: int = 0) -> "RetentionPlan":
        count = {"development": 210, "main": 2100}[phase]
        return cls(
            phase=phase,
            request_limit=count,
            total_input_tokens=count * 400_000,
            total_output_tokens=count * 8192,
            cost_limit_nano_usd=cost_limit_nano_usd,
        )


def plan_from_binding(bound: dict[str, Any]) -> TaskPlan:
    plan: TaskPlan
    if bound.get("protocol") == RETENTION_PROTOCOL:
        retention_plan = RetentionPlan(**bound["plan"])
        if retention_plan.phase != bound.get("phase"):
            raise ValueError("phase/plan mismatch")
        plan = retention_plan
    elif bound.get("protocol") == PROTOCOL:
        plan = TaskPlan()
    else:
        raise ValueError("unknown MemoryReplica protocol")
    if digest(plan.data) != digest(bound["plan"]):
        raise ValueError("not a fixed MemoryReplica plan")
    return plan


def episode_key(case: dict[str, Any]) -> str:
    return str(case.get("episode_id", case["case_id"]))


@dataclass
class TaskLedger(Ledger):
    """Same settlement/reservation implementation and units, with a task-specific cap."""

    def blocked(self, plan: TaskPlan | None = None) -> str | None:
        p = plan or TaskPlan()
        if self.reserved_cost_nano_usd:
            return "UNRESOLVED_RESERVATION"
        if self.attempts >= p.request_limit:
            return "REQUEST_LIMIT"
        if self.input_tokens + p.context_input_reserve > p.total_input_tokens:
            return "INPUT_TOKEN_LIMIT"
        if self.output_tokens + p.max_output_tokens > p.total_output_tokens:
            return "OUTPUT_TOKEN_LIMIT"
        if self.known_cost_nano_usd + p.reservation > p.cost_limit_nano_usd:
            return "COST_LIMIT"
        return None

    def reserve(self, plan: TaskPlan | None = None) -> None:
        p = plan or TaskPlan()
        blocked = self.blocked(p)
        if blocked:
            raise ValueError(blocked)
        self.attempts += 1
        self.reserved_cost_nano_usd = p.reservation


_GUARD = r"""
import http.client,os,socket
def forbidden(*a,**k): raise AssertionError('external access forbidden')
socket.socket.connect=socket.socket.connect_ex=socket.create_connection=socket.getaddrinfo=forbidden
http.client.HTTPSConnection=forbidden
old=os.environ.__class__.__getitem__
def getitem(self,key):
 if key=='OPENAI_API_KEY': return forbidden()
 return old(self,key)
os.environ.__class__.__getitem__=getitem
"""
_RESEARCH = (
    _GUARD
    + r"""
import hashlib,json,pathlib,sys
p=json.load(sys.stdin);root=pathlib.Path(p['root']);sys.path.insert(0,str(root/'src'))
from journeymap_memoryreplica.probe import source_fingerprint,validate_imports
from journeymap_memoryreplica.dynamic import advance_case,audit_case
validate_imports(root)
assert source_fingerprint(root)==p['hashes']
result=advance_case(p['case']) if p['mode']=='prefix' else audit_case(p['case'])
assert source_fingerprint(root)==p['hashes']
print(json.dumps(result,ensure_ascii=True,allow_nan=False))
"""
)
_RETENTION_RESEARCH = (
    _GUARD
    + r"""
import contextlib,hashlib,io,json,pathlib,runpy,sys
p=json.load(sys.stdin);root=pathlib.Path(p['root']);package=pathlib.Path(p['package'])
def fingerprint(base, names):
 return {n:hashlib.sha256((base/n).read_bytes()).hexdigest() for n in names}
assert fingerprint(root,p['hashes'])==p['hashes']
assert fingerprint(package,p['package_hashes'])==p['package_hashes']
sys.argv=[str(package/'run_v1.py'),'--source-src',str(root/'src'),
 '--protocol-dir',str(package/'protocol'),'--phase',p['phase'],p['command']]
sys.stdin=io.StringIO(json.dumps(p['case']))
with contextlib.redirect_stdout(io.StringIO()) as output:
 runpy.run_path(str(package/'run_v1.py'),run_name='__main__')
assert fingerprint(root,p['hashes'])==p['hashes']
assert fingerprint(package,p['package_hashes'])==p['package_hashes']
print(output.getvalue(),end='')
"""
)
_PARENT = (
    _GUARD
    + r"""
import json,pathlib,sys
p=json.load(sys.stdin);src=pathlib.Path(p['source']);sys.path.insert(0,str(src))
from journeymap.adapters.probe_lineage import check_snapshot
from journeymap.adapters.probe_history import runtime_identity
assert runtime_identity(src)==p['identity']
a,h,r=check_snapshot(p['snapshot'],pathlib.Path(p['history_source']))
assert len(p['snapshot']['runs'])==1 and r is not None and r['status']=='FINISHED'
assert r['ledger']['attempts']==72 and r['ledger']['reserved_cost_nano_usd']==0
assert runtime_identity(src)==p['identity']
print(json.dumps({'anchor':a.data,'history':h,'ledger':r['ledger']},ensure_ascii=True))
"""
)
_PARENTS: dict[str, dict[str, Any]] = {}
_PLANS: dict[str, dict[str, Any]] = {}


def isolated(program: str, value: dict[str, Any]) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-X", "utf8", "-c", program],
        input=json.dumps(value, ensure_ascii=True),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=120,
    )
    if result.returncode:
        raise ValueError("isolated source-bound reconstruction failed")
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, dict):
        raise ValueError("invalid isolated result")
    return parsed


def parent_check(snapshot: dict[str, Any], parent_source: Path) -> dict[str, Any]:
    """Verify a fresh genesis or a completed predecessor against its frozen source."""
    from journeymap.adapters.probe_lineage import fresh_task_anchor, fresh_task_bundle

    if fresh_task_bundle(snapshot) is not None:
        return {
            "anchor": fresh_task_anchor(snapshot).data,
            "history": {"prior_known_cost_nano_usd": 0, "prior_unresolved_nano_usd": 0},
            "ledger": TaskLedger().data,
        }
    identity = runtime_identity(parent_source)
    claims = [e for e in snapshot["events"] if e["event"]["kind"] == "MEMORYREPLICA_CLAIMED"]
    if claims:
        last = claims[-1]["event"]["data"]["binding"]
        if last["actual_source"] != sources(parent_source.parent):
            raise PermissionError("latest completed task frozen source required")
        key = digest({"snapshot": snapshot, "identity": identity})
        if key not in _PARENTS:
            from journeymap.adapters.memoryreplica_journal import check, split

            parent, _, _ = split(snapshot)
            prior_source = Path(last["parent_source"])
            verified = parent_check(parent, prior_source)
            anchor, reduced = check(snapshot, prior_source, actual_root=parent_source.parent)
            ledger = reduced["ledger"]
            # Only the replay-validated, separately authorized retransmissions can carry
            # stopped-request exposure into a research-complete predecessor.
            carried = reduced.get("carried_main_reservation_nano_usd", 0)
            expected_status = "UNCERTAIN" if carried else "FINISHED"
            if (
                reduced["status"] != expected_status
                or reduced["stop_reason"] != "ALL_CASES_COMPLETED"
                or ledger["reserved_cost_nano_usd"] != 0
            ):
                raise PermissionError("completed predecessor without a new reservation required")
            _PARENTS[key] = {
                "anchor": anchor.data,
                "history": verified["history"],
                "ledger": ledger,
                "known_before_next_nano_usd": last["prior_known_nano_usd"]
                + ledger["known_cost_nano_usd"],
                "unresolved_before_next_nano_usd": last["prior_unresolved_nano_usd"] + carried,
            }
        return _PARENTS[key]
    runs = snapshot["runs"]
    if len(runs) != 1 or runs[0]["binding"]["source_digest"] != identity:
        raise PermissionError("completed B runtime/source required")
    key = digest({"snapshot": snapshot, "identity": identity})
    if key not in _PARENTS:
        history_source = snapshot["meta"]["history_bundle"]["runtime_path"]
        _PARENTS[key] = isolated(
            _PARENT,
            {
                "source": str(parent_source.resolve()),
                "identity": identity,
                "history_source": history_source,
                "snapshot": snapshot,
            },
        )
    return _PARENTS[key]


@dataclass(frozen=True)
class Research:
    root: Path
    hashes: dict[str, str]
    package: Path | None = None
    package_hashes: dict[str, str] | None = None
    phase: str | None = None

    @classmethod
    def capture(cls, root: Path) -> "Research":
        current = sources(root)
        if "src/journeymap_memoryreplica/dynamic.py" not in current:
            raise ValueError("dynamic research source required")
        return cls(root.resolve(), current)

    @property
    def data(self) -> dict[str, Any]:
        data: dict[str, Any] = {"root": str(self.root), "hashes": self.hashes}
        if self.package is not None:
            data.update(
                package=str(self.package), package_hashes=self.package_hashes, phase=self.phase
            )
        return data

    @classmethod
    def capture_retention(cls, root: Path, package: Path, phase: str) -> "Research":
        legacy = cls.capture(root)
        if phase not in {"development", "main"}:
            raise ValueError("unknown phase")
        names = [
            package / "run_v1.py",
            *sorted((package / "memoryreplica_v1").glob("*.py")),
            *sorted(p for p in (package / "protocol").iterdir() if p.is_file()),
        ]
        hashes = {
            p.relative_to(package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in names
        }
        if not all(
            n in hashes
            for n in ("run_v1.py", "memoryreplica_v1/adapter.py", "protocol/freeze_manifest.json")
        ):
            raise ValueError("complete v1 package required")
        result = cls(legacy.root, legacy.hashes, package.resolve(), hashes, phase)
        result.prepared()
        return result

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "Research":
        return cls(
            Path(data["root"]),
            data["hashes"],
            Path(data["package"]) if "package" in data else None,
            data.get("package_hashes"),
            data.get("phase"),
        )

    def prepared(self) -> dict[str, Any]:
        self.check()
        if self.package is None:
            raise ValueError("retention package required")
        key = digest(self.data)
        if key not in _PLANS:
            raw = isolated(_RETENTION_RESEARCH, {**self.data, "case": {}, "command": "prepare"})
            _PLANS[key] = {
                "phase": raw["phase"],
                "episodes": raw["episodes"],
                "max_requests": raw["max_requests"],
                "cases": [
                    {k: c[k] for k in ("case_id", "episode_id", "policy", "input_case_sha256")}
                    for c in raw["plan"]
                ],
            }
        return cast(dict[str, Any], json.loads(json.dumps(_PLANS[key])))

    def check(self) -> None:
        if sources(self.root) != self.hashes:
            raise PermissionError("research source changed")
        if self.package is not None:
            if not self.package_hashes or self.phase not in {"development", "main"}:
                raise ValueError("invalid retention source binding")
            actual = {
                n: hashlib.sha256((self.package / n).read_bytes()).hexdigest()
                for n in self.package_hashes
            }
            if actual != self.package_hashes:
                raise PermissionError("retention source or frozen inputs changed")

    def call(self, case: dict[str, Any], mode: str = "prefix") -> dict[str, Any]:
        self.check()
        if self.package is None:
            result = isolated(_RESEARCH, {**self.data, "case": case, "mode": mode})
        else:
            result = isolated(_RETENTION_RESEARCH, {**self.data, "case": case, "command": "bridge"})
        self.check()
        return result


def binding(
    path: Path,
    output: Path,
    anchor: Anchor,
    parent: dict[str, Any],
    research: Research,
    parent_source: Path,
    run_id: str,
    approval_id: str,
    risk_id: str,
    plan: TaskPlan | None = None,
) -> dict[str, Any]:
    return _binding(
        path,
        output,
        anchor,
        parent,
        research,
        parent_source,
        run_id,
        approval_id,
        risk_id,
        plan,
        actual_root=Path(__file__).resolve().parents[3],
        execution_runtime=runtime(),
    )


def recorded_runtime(value: Any) -> dict[str, Any]:
    """Validate the recorded descriptor. Its original value is bound by the journal."""
    if not isinstance(value, dict) or set(value) != {"python", "version", "sha256"}:
        raise ValueError("invalid recorded runtime descriptor")
    python, version, sha256 = value["python"], value["version"], value["sha256"]
    if (
        type(python) is not str
        or not (Path(python).is_absolute() or PureWindowsPath(python).is_absolute())
        or type(version) is not list
        or len(version) != 3
        or any(type(part) is not int or part < 0 for part in version)
        or type(sha256) is not str
        or len(sha256) != 64
        or any(char not in "0123456789abcdef" for char in sha256)
    ):
        raise ValueError("invalid recorded runtime descriptor")
    return value


def _binding(
    path: Path,
    output: Path,
    anchor: Anchor,
    parent: dict[str, Any],
    research: Research,
    parent_source: Path,
    run_id: str,
    approval_id: str,
    risk_id: str,
    plan: TaskPlan | None,
    *,
    actual_root: Path,
    execution_runtime: dict[str, Any],
) -> dict[str, Any]:
    from journeymap.adapters.probe_lineage import fresh_task_bundle

    for value in (run_id, approval_id, risk_id):
        uuid_(value)
    if len({run_id, approval_id, risk_id}) != 3:
        raise PermissionError("distinct task IDs required")
    verified = parent_check(parent, parent_source)
    if digest(verified["anchor"]) != digest(anchor.data) or str(path.resolve()) != anchor.path:
        raise PermissionError("latest completed B anchor required")
    research.check()
    selected_plan = plan or TaskPlan()
    fresh = fresh_task_bundle(parent)
    if fresh is not None and (
        fresh["run_id"] != run_id or not isinstance(selected_plan, RetentionPlan)
    ):
        raise PermissionError("independent retention authority does not match the run")
    if research.package is not None:
        if not isinstance(selected_plan, RetentionPlan) or selected_plan.phase != research.phase:
            raise ValueError("explicit phase-bound retention plan required")
    elif type(selected_plan) is not TaskPlan:
        raise ValueError("legacy research needs legacy plan")
    result = {
        "protocol": selected_plan.plan_version,
        "run_id": run_id,
        "paid_approval_id": approval_id,
        "risk_acceptance_id": risk_id,
        "authority_checkpoint": anchor.data,
        "parent_digest": digest(parent),
        "parent_source": str(parent_source.resolve()),
        "parent_identity": runtime_identity(parent_source),
        "research": research.data,
        "actual_source": sources(actual_root),
        "runtime": execution_runtime,
        "endpoint": ENDPOINT,
        "output": str(output.resolve()),
        "plan": cast(dict[str, Any], selected_plan.data),
        "cases": [
            {"case_id": f"m{i + 1:02}", "memory_policy": p} for i, p in enumerate(CASE_ORDER)
        ],
        "task_version": "memoryreplica-task-offline-1",
        "wire_version": "memoryreplica-responses-preparation-1",
        "dynamic_version": "memoryreplica-dynamic-saved-frames-1",
        "prior_known_nano_usd": verified["history"]["prior_known_cost_nano_usd"]
        + verified["ledger"]["known_cost_nano_usd"],
        "prior_unresolved_nano_usd": verified["history"]["prior_unresolved_nano_usd"],
        "lineage_total_cost_nano_usd": None,
    }
    if research.package is not None:
        prepared = research.prepared()
        if prepared["max_requests"] != selected_plan.request_limit:
            raise ValueError("plan/request allocation mismatch")
        result.update(
            cases=prepared["cases"],
            phase=research.phase,
            task_version=RETENTION_WIRE,
            wire_version=RETENTION_WIRE,
            dynamic_version=RETENTION_WIRE,
            accounting_rate_basis="existing_ledger_rates_not_current_quote",
        )
    if "known_before_next_nano_usd" in verified:
        result["prior_known_nano_usd"] = verified["known_before_next_nano_usd"]
        result["prior_unresolved_nano_usd"] = verified["unresolved_before_next_nano_usd"]
    if fresh is not None:
        result["independent_authority"] = True
    return result


@dataclass(frozen=True)
class TaskApproval(ContinuationApproval):
    @property
    def data(self) -> dict[str, Any]:
        return {**super().data, "schema": APPROVAL}

    def validate(self, expected: dict[str, Any], *, synthetic: bool, now: str) -> None:
        for value in (self.approval_id, self.risk_acceptance_id):
            uuid_(value)
        if (
            self.data["schema"] != APPROVAL
            or digest(self.bound) != digest(expected)
            or (
                self.approval_id != expected["paid_approval_id"]
                or self.risk_acceptance_id != expected["risk_acceptance_id"]
                or self.approved_by != ("offline-fixture" if synthetic else "human-user")
                or self.decision
                != (
                    "synthetic-memoryreplica-only"
                    if synthetic
                    else "approve-one-memoryreplica-batch"
                )
                or self.scope
                != (
                    ("recording-only", "preserve-prior-uncertainty")
                    if synthetic
                    else ("preserve-prior-uncertainty", "read-openai-api-key", "paid-responses")
                )
            )
        ):
            raise PermissionError("explicit MemoryReplica approval required")
        if not timestamp(self.valid_from) <= timestamp(now) < timestamp(self.expires_at):
            raise PermissionError("MemoryReplica approval expired")

    @classmethod
    def parse(cls, value: Any) -> "TaskApproval":
        if not isinstance(value, dict) or value.get("schema") != APPROVAL:
            raise PermissionError("not a MemoryReplica approval")
        old = ContinuationApproval.parse({**value, "schema": "probe-continuation-approval-1"})
        return cls(**asdict(old))
