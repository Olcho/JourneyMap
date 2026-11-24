"""Actual lineage + isolated research, exclusively synthetic histories and transports."""

import json
import os
import socket
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from probe_policy_fixtures import fixture_case, history_fixture

from journeymap.adapters import memoryreplica, memoryreplica_journal
from journeymap.adapters.life_responses import synthetic_response
from journeymap.adapters.memoryreplica import (
    _GUARD,
    PROTOCOL,
    Research,
    RetentionPlan,
    TaskApproval,
    TaskLedger,
    TaskPlan,
    binding,
    digest,
    isolated,
    parent_check,
)
from journeymap.adapters.memoryreplica_journal import TaskJournal, check, reduce
from journeymap.adapters.probe_continuation import Anchor
from journeymap.adapters.probe_diagnostics import TransportDiagnostic
from journeymap.adapters.probe_history import file_digest
from journeymap.adapters.probe_lineage import (
    device_id,
    fresh_task_parent,
    initialize_fresh_task_authority,
    read_snapshot,
)
from journeymap.core.canonical import canonical_json
from journeymap.experiments import continued_probe
from journeymap.experiments import life_reasoning_probe as old_probe
from journeymap.experiments import memoryreplica as task
from journeymap.experiments import paid_probe as paid

RESEARCH = Path(__file__).resolve().parents[2]
ACTUAL_SOURCE = Path(__file__).resolve().parents[1] / "src"


@pytest.mark.parametrize(
    ("status", "reason", "reserved", "carried", "accepted"),
    [
        ("FINISHED", "ALL_CASES_COMPLETED", 0, 0, True),
        ("UNCERTAIN", "ALL_CASES_COMPLETED", 0, 673_728_000, True),
        ("UNCERTAIN", "ALL_CASES_COMPLETED", 336_864_000, 673_728_000, False),
        ("UNCERTAIN", "INVALID_RESPONSE", 0, 673_728_000, False),
        ("UNCERTAIN", "ALL_CASES_COMPLETED", 0, 0, False),
    ],
)
def test_completed_predecessor_retains_only_verified_carried_exposure(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    reason: str,
    reserved: int,
    carried: int,
    accepted: bool,
) -> None:
    inventory = {"src/example.py": "example"}
    bound = {
        "actual_source": inventory,
        "parent_source": "parent/src",
        "prior_known_nano_usd": 100,
        "prior_unresolved_nano_usd": 336_864_000,
    }
    snapshot = {
        "events": [{"event": {"kind": "MEMORYREPLICA_CLAIMED", "data": {"binding": bound}}}]
    }
    parent = {"events": [], "runs": [{"binding": {"source_digest": "example"}}]}
    ledger = {"known_cost_nano_usd": 200, "reserved_cost_nano_usd": reserved}
    reduced: dict[str, Any] = {"status": status, "stop_reason": reason, "ledger": ledger}
    if carried:
        reduced["carried_main_reservation_nano_usd"] = carried

    monkeypatch.setattr(memoryreplica, "runtime_identity", lambda _: "example")
    monkeypatch.setattr(memoryreplica, "sources", lambda _: inventory)
    monkeypatch.setattr(
        memoryreplica,
        "_PARENTS",
        {digest({"snapshot": parent, "identity": "example"}): {"history": {}}},
    )
    monkeypatch.setattr(memoryreplica_journal, "split", lambda _: (parent, {}, []))
    anchor = SimpleNamespace(data={"sequence": 6425})
    monkeypatch.setattr(memoryreplica_journal, "check", lambda *a, **kw: (anchor, reduced))

    if accepted:
        result = parent_check(snapshot, ACTUAL_SOURCE)
        assert result["known_before_next_nano_usd"] == 300
        assert result["unresolved_before_next_nano_usd"] == 336_864_000 + carried
    else:
        with pytest.raises(PermissionError, match="completed predecessor"):
            parent_check(snapshot, ACTUAL_SOURCE)


@pytest.fixture(autouse=True)
def no_external(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError("no actual API, DNS, credential or account access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(paid, "real_credential", forbidden)
    monkeypatch.setattr(paid, "real_connection", forbidden)
    old = os.environ.get

    def get(k: str, default: str | None = None) -> str | None:
        if k == "OPENAI_API_KEY":
            forbidden()
        return old(k, default)

    monkeypatch.setattr(os.environ, "get", get)


def reply(text: str, identity: str, variant: str = "normal") -> paid.WireReply:
    doc = json.loads(synthetic_response(text))
    doc["id"] = identity
    doc["output"][0]["status"] = "completed"
    if variant == "incomplete":
        doc["status"], doc["output"] = "incomplete", []
        doc["incomplete_details"] = {"reason": "max_output_tokens"}
    if variant == "refusal":
        doc["output"][0]["content"] = [{"type": "refusal", "refusal": "synthetic"}]
    if variant == "usage":
        doc["usage"] = None
    if variant == "cache":
        del doc["usage"]["input_tokens_details"]
    if variant == "partial":
        return paid.WireReply(
            200,
            None,
            1,
            "TRANSPORT_UNCERTAIN",
            TransportDiagnostic("FRAMING_CHECK", "NONE", "FRAMING_INCOMPLETE"),
        )
    ms = 120001 if variant == "late" else 1
    return paid.WireReply(
        200,
        canonical_json(doc),
        ms,
        diagnostic=TransportDiagnostic(
            "SECRET_SCAN", "NONE", "LATE_COMPLETE" if ms > 120000 else "COMPLETE", True
        ),
    )


def move(route: str) -> str:
    return canonical_json({"action_type": "MOVE", "payload": {"route_id": route}})


def test_independent_retention_authority_starts_at_zero_and_never_retries(tmp_path: Path) -> None:
    package = RESEARCH / "experiments/memoryreplica-v1/followup"
    research = Research.capture_retention(RESEARCH, package, "main")
    path, output = tmp_path / "probe-runs.sqlite3", tmp_path / "live-output"
    rid, aid, risk, authority_id = (str(uuid4()) for _ in range(4))
    genesis = fresh_task_parent(path, authority_id, rid, device_id(), "0" * 64, synthetic=True)
    anchor = initialize_fresh_task_authority(path, genesis)
    assert read_snapshot(path) == genesis
    now = datetime.now(UTC).replace(microsecond=0)
    plan = RetentionPlan.for_phase("main", 15_000_000_000)
    bound = binding(path, output, anchor, genesis, research, ACTUAL_SOURCE, rid, aid, risk, plan)
    bound["synthetic"] = True
    approval = TaskApproval(
        aid,
        risk,
        "offline-fixture",
        "synthetic-memoryreplica-only",
        ("recording-only", "preserve-prior-uncertainty"),
        now.isoformat(),
        (now + timedelta(hours=1)).isoformat(),
        bound,
    )
    with pytest.raises(PermissionError):
        task.execute(path, anchor, approval, research, ACTUAL_SOURCE, output)
    assert read_snapshot(path) == genesis

    mock = paid.RecordingProbeTransport((reply("", "unused", variant="partial"),))
    report = task.execute(path, anchor, approval, research, ACTUAL_SOURCE, output, transport=mock)
    prepared = json.loads((package / "protocol/prepared_requests.json").read_bytes())
    assert mock.bodies == [prepared["plan"][0]["first_reconstruction_wire"]["body"]]
    assert report["evidence_kind"] == "OFFLINE_ACTUAL_RECORDING"
    assert report["planned_cases"] == 300
    assert report["complete_scored_episodes"] == 0
    assert report["authority"]["runs"][0]["run_id"] == rid
    assert report["accounting"]["stop_reason"] == "TRANSPORT_UNCERTAIN"
    assert (
        report["accounting"]["ledger"]
        == TaskLedger(attempts=1, reserved_cost_nano_usd=plan.reservation).data
    )
    assert report["accounting"]["conditional_exposure_nano_usd"] == plan.reservation
    assert task.audit_report(report, research, ACTUAL_SOURCE)["data_only_equal"]

    with pytest.raises(PermissionError, match="fresh genesis"):
        task.execute(
            path,
            anchor,
            approval,
            research,
            ACTUAL_SOURCE,
            output,
            transport=paid.RecordingProbeTransport(()),
        )


WAIT = canonical_json({"action_type": "WAIT", "payload": {"duration": 1}})


def recordings(
    *, varied: bool = False, variant: str = "normal", duplicate: bool = False, waits: bool = False
) -> paid.RecordingProbeTransport:
    replies = []
    for i in range(9):
        path = [WAIT] * 6 if waits else [move("A-to-S"), move("S-to-B")]
        if varied and i % 3 == 1:
            path = [move("A-to-D"), move("D-to-B")]
        if varied and i % 3 == 2:
            path = [WAIT, move("A-to-S"), move("S-to-B")]
        for j, text in enumerate(['{"facts":[]}', *path]):
            rid = "mock-0-0" if duplicate and i == 1 and j == 0 else f"mock-{i}-{j}"
            replies.append(reply(text, rid, variant if i == 0 and j == 1 else "normal"))
    return paid.RecordingProbeTransport(tuple(replies))


@pytest.fixture(scope="module")
def completed_B(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    folder = tmp_path_factory.mktemp("synthetic-memoryreplica-authority")
    corpus = old_probe.prepare()
    history = history_fixture(corpus, folder)
    path = folder / "probe-runs.sqlite3"
    imported, approval, _ = fixture_case(path, corpus, synthetic=True, history=history)
    old = paid.RecordingProbeTransport(tuple(reply(old_probe.WAIT, f"B-{i}") for i in range(72)))
    report = continued_probe.execute(path, imported, approval, transport=old)
    return {
        "path": path,
        "folder": folder,
        "bytes": path.read_bytes(),
        "anchor": Anchor.parse(report["latest_recorded_checkpoint"]),
        "valid_from": approval.valid_from,
        "expires_at": approval.expires_at,
        "research": Research.capture(RESEARCH),
    }


@pytest.fixture
def prepared(tmp_path: Path, completed_B: dict[str, Any]) -> Iterator[Any]:
    # Test isolation only: restore an immutable, synthetic B fixture inside pytest temp.
    # There is no corresponding reset/restore API in the implementation.
    path = completed_B["path"]
    assert path.resolve().is_relative_to(completed_B["folder"].resolve())
    assert "synthetic-memoryreplica-authority" in str(path)
    path.write_bytes(completed_B["bytes"])
    anchor, research = completed_B["anchor"], completed_B["research"]
    output = tmp_path / "memoryreplica-output"
    rid, aid, risk = (str(uuid4()) for _ in range(3))
    bound = binding(
        path, output, anchor, read_snapshot(path), research, ACTUAL_SOURCE, rid, aid, risk
    )
    bound["synthetic"] = True
    a = TaskApproval(
        aid,
        risk,
        "offline-fixture",
        "synthetic-memoryreplica-only",
        ("recording-only", "preserve-prior-uncertainty"),
        completed_B["valid_from"],
        completed_B["expires_at"],
        bound,
    )
    yield path, anchor, a, research, output
    (tmp_path / "synthetic-authority-after.sqlite3").write_bytes(path.read_bytes())
    for fence in path.parent.glob(path.name + ".memoryreplica-attempt-*"):
        assert fence.resolve().is_relative_to(completed_B["folder"].resolve())
        fence.rename(tmp_path / fence.name)


def run(
    prepared: tuple[Path, Anchor, TaskApproval, Research, Path], wire: paid.RecordingProbeTransport
) -> dict[str, Any]:
    path, anchor, a, research, output = prepared
    return task.execute(path, anchor, a, research, ACTUAL_SOURCE, output, transport=wire)


def test_nine_dynamic_cases_share_actual_authority_and_audit(prepared: Any) -> None:
    wire = recordings(varied=True)
    report = run(prepared, wire)
    assert report["accounting"]["status"] == "FINISHED"
    assert len(wire.bodies) == 30
    results = [c["result"] for c in report["research_cases"]]
    assert [c["final"]["elapsed_ticks"] for c in results] == [4, 8, 5] * 3
    assert all(c["stop"] == "ARRIVED" for c in results)
    assert report["lineage_total_cost_nano_usd"] is None
    assert report["authority"]["runs"] and len(report["authority"]["runs"]) == 2
    assert task.audit_report(report, prepared[3], ACTUAL_SOURCE)["data_only_equal"]
    for body in wire.bodies:
        assert set(body) == {
            "model",
            "input",
            "reasoning",
            "max_output_tokens",
            "store",
            "stream",
            "service_tier",
            "text",
        }
        assert '"engine_report"' not in cast(str, body["input"])


@pytest.mark.parametrize(
    "variant,reason",
    [
        ("incomplete", "ALL_CASES_COMPLETED"),
        ("refusal", "ALL_CASES_COMPLETED"),
        ("normal", "ALL_CASES_COMPLETED"),
        ("partial", "TRANSPORT_UNCERTAIN"),
        ("usage", "INVALID_RESPONSE"),
        ("cache", "INVALID_RESPONSE"),
        ("late", "TIMEOUT"),
    ],
)
def test_response_outcomes_and_reservations(prepared: Any, variant: str, reason: str) -> None:
    wire = recordings(variant=variant)
    report = run(prepared, wire)
    assert report["accounting"]["stop_reason"] == reason
    if variant in {"partial", "usage", "cache"}:
        assert len(wire.bodies) == 2
        assert report["accounting"]["ledger"]["reserved_cost_nano_usd"] == 336864000
        assert report["accounting"]["status"] == "UNCERTAIN"
    if variant == "late":
        assert len(wire.bodies) == 2
        assert report["accounting"]["ledger"]["reserved_cost_nano_usd"] == 0
    if variant != "normal":
        assert report["research_cases"][0]["result"]["final"]["position"] == "A"


def test_sixty_three_calls_and_six_actions(prepared: Any) -> None:
    wire = recordings(waits=True)
    report = run(prepared, wire)
    assert len(wire.bodies) == 63
    assert all(c["result"]["stop"] == "DECISION_LIMIT" for c in report["research_cases"])
    assert TaskLedger(attempts=63).blocked() == "REQUEST_LIMIT"


def test_duplicate_id_is_checked_across_cases(prepared: Any) -> None:
    wire = recordings(duplicate=True)
    report = run(prepared, wire)
    assert len(wire.bodies) == 4
    assert report["accounting"]["stop_reason"] == "DUPLICATE_RESPONSE_ID"
    assert report["accounting"]["status"] == "UNCERTAIN"


@pytest.mark.parametrize("stage", ["RESPONSE", "SETTLED"])
def test_storage_failure_stops_sends_before_research_delivery(
    prepared: Any, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    original = TaskJournal._append

    def fail(self: TaskJournal, event: str, data: dict[str, Any]) -> None:
        if event == stage:
            raise OSError("synthetic disk failure")
        original(self, event, data)

    monkeypatch.setattr(TaskJournal, "_append", fail)
    wire = recordings()
    with pytest.raises(RuntimeError, match="no further sends"):
        run(prepared, wire)
    assert len(wire.bodies) == 1
    _, reduced = check(read_snapshot(prepared[0]), ACTUAL_SOURCE)
    assert reduced["status"] == "OPEN"
    assert reduced["frames"][0] == []


@pytest.mark.parametrize("wrong", ["approval", "stale", "source", "old-approval", "consumed"])
def test_admission_denied_before_transport(prepared: Any, wrong: str) -> None:
    path, anchor, a, research, output = prepared
    before = file_digest(path)
    wire = recordings()
    if wrong == "approval":
        a = None
    elif wrong == "stale":
        anchor = replace(anchor, sequence=1)
    elif wrong == "source":
        research = replace(research, hashes={})
    elif wrong == "old-approval":
        a = object()
    else:
        old_id = cast(dict[str, Any], read_snapshot(path))["consumed"][0]["id"]
        bound = {**a.bound, "risk_acceptance_id": old_id}
        a = replace(a, risk_acceptance_id=old_id, bound=bound)
    with pytest.raises((PermissionError, ValueError)):
        task.execute(path, anchor, a, research, ACTUAL_SOURCE, output, transport=wire)
    assert wire.bodies == [] and file_digest(path) == before


def test_task_cannot_be_reconsumed(prepared: Any) -> None:
    run(prepared, recordings())
    wire = recordings()
    with pytest.raises(PermissionError):
        run(prepared, wire)
    assert wire.bodies == []


def test_import_only_is_not_a_latest_B_checkpoint(tmp_path: Path) -> None:
    corpus = old_probe.prepare()
    history = history_fixture(corpus, tmp_path)
    path = tmp_path / "probe-runs.sqlite3"
    anchor, old, _ = fixture_case(path, corpus, synthetic=True, history=history)
    research = Research.capture(RESEARCH)
    with pytest.raises(PermissionError, match="completed B"):
        binding(
            path,
            tmp_path / "out",
            anchor,
            read_snapshot(path),
            research,
            ACTUAL_SOURCE,
            str(uuid4()),
            str(uuid4()),
            str(uuid4()),
        )
    wire = recordings()
    with pytest.raises(PermissionError):
        task.execute(
            path, anchor, cast(Any, old), research, ACTUAL_SOURCE, tmp_path / "out", transport=wire
        )
    assert wire.bodies == []


def test_synthetic_claim_cannot_prepare_actual_https(prepared: Any) -> None:
    path, anchor, a, research, output = prepared
    calls: list[str] = []

    def no_key() -> str:
        calls.append("credential")
        raise AssertionError("must be denied before credential")

    wire = paid.HTTPProbeTransport(credential=no_key)
    with pytest.raises(PermissionError):
        task.execute(path, anchor, a, research, ACTUAL_SOURCE, output, transport=wire)
    assert calls == []


def reseal_case(report: dict[str, Any]) -> None:
    """Test attacker rehashes the task suffix; original B evidence stays byte-identical."""
    snapshot = report["authority"]
    run_ = next(r for r in snapshot["runs"] if r["binding"]["protocol"] == PROTOCOL)
    previous = "0" * 64
    for entry in run_["entries"]:
        entry["previous"] = previous
        previous = digest(entry)
    run_["tail"] = previous
    commits = [w for w in snapshot["events"] if w["event"]["kind"] == "MEMORYREPLICA_COMMITTED"]
    for i, wrapper in enumerate(commits, 1):
        reduced = reduce(run_["binding"], run_["entries"][:i])
        wrapper["event"]["data"].update(
            entry_digest=digest(run_["entries"][i - 1]),
            status=reduced["status"],
            exposure=reduced["conditional_exposure_nano_usd"],
        )
    previous = "0" * 64
    for wrapper in snapshot["events"]:
        wrapper["event"]["previous"] = previous
        wrapper["digest"] = digest(wrapper["event"])
        previous = wrapper["digest"]


def test_fresh_process_audit_and_semantic_tamper_rejection(prepared: Any) -> None:
    from copy import deepcopy

    report = run(prepared, recordings())
    program = (
        _GUARD
        + r"""
import json,pathlib,sys
p=json.load(sys.stdin);sys.path.insert(0,p['actual'])
from journeymap.adapters.memoryreplica import Research
from journeymap.experiments.memoryreplica import audit_report
result=audit_report(p['report'],Research(pathlib.Path(p['root']),p['hashes']),pathlib.Path(p['parent']))
print(json.dumps(result))
"""
    )
    audited = isolated(
        program,
        {
            "actual": str(ACTUAL_SOURCE),
            "report": report,
            "root": str(prepared[3].root),
            "hashes": prepared[3].hashes,
            "parent": str(ACTUAL_SOURCE),
        },
    )
    assert audited["paid_journal_verified"] and audited["provider_recalled"] is False
    assert audited["current_execution_eligibility_verified"] is False
    (prepared[4] / "fresh-audit.json").write_text(json.dumps(audited, indent=2) + "\n")
    for target in ("arrival", "memory", "frame", "cost", "request", "summary"):
        changed = deepcopy(report)
        run_ = next(r for r in changed["authority"]["runs"] if r["binding"]["protocol"] == PROTOCOL)
        case = next(e["data"]["result"] for e in run_["entries"] if e["stage"] == "CASE")
        if target == "arrival":
            case["final"]["position"] = "A"
            reseal_case(changed)
        elif target == "memory":
            case["baseline"]["context_digest"] = "invented"
            reseal_case(changed)
        elif target == "frame":
            run_["entries"][3]["data"]["packet"]["raw_response"] = "{}"
        elif target == "cost":
            settled = next(e for e in run_["entries"] if e["stage"] == "SETTLED")
            settled["data"]["ledger"]["known_cost_nano_usd"] += 1
        elif target == "request":
            reserved = next(e for e in run_["entries"] if e["stage"] == "RESERVED")
            reserved["data"]["request"]["wire"]["body"]["input"] += " hidden truth"
        else:
            changed["accounting"]["ledger"]["attempts"] = True
        with pytest.raises((ValueError, PermissionError)):
            task.audit_report(changed, prepared[3], ACTUAL_SOURCE)
    (prepared[4] / "tamper-checks.json").write_text(
        json.dumps(
            {
                "rejected": ["arrival", "memory", "frame", "cost", "request", "summary"],
                "arrival_and_memory_resealed": True,
            },
            indent=2,
        )
        + "\n"
    )


@pytest.mark.parametrize("kind", ["time", "rejection"])
def test_dynamic_engine_limits_and_rejected_action_recovery(prepared: Any, kind: str) -> None:
    texts = (
        [canonical_json({"action_type": "WAIT", "payload": {"duration": 24}})]
        if kind == "time"
        else [move("unknown-route"), move("A-to-S"), move("S-to-B")]
    )
    replies = [reply(text, f"limit-{i}") for i, text in enumerate(['{"facts":[]}', *texts])]
    replies.append(reply("ignored", "next-case-uncertain", "partial"))
    report = run(prepared, paid.RecordingProbeTransport(tuple(replies)))
    result = report["research_cases"][0]["result"]
    if kind == "time":
        assert result["stop"] == "TIME_LIMIT" and result["final"]["elapsed_ticks"] == 24
    else:
        assert result["stop"] == "ARRIVED" and result["final"]["elapsed_ticks"] == 4
        assert result["action_steps"][0]["engine"]["receipt"]["status"] == "REJECTED"
    assert report["accounting"]["status"] == "UNCERTAIN"


def test_action_settlement_failure_never_delivers_action_frame(
    prepared: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_append, original_call = TaskJournal._append, Research.call
    delivered: list[int] = []

    def fail(self: TaskJournal, stage: str, data: dict[str, Any]) -> None:
        if stage == "SETTLED" and sum(e["stage"] == "RESERVED" for e in self.entries) == 2:
            raise OSError("synthetic action settlement write failure")
        original_append(self, stage, data)

    def spy(self: Research, case: dict[str, Any], mode: str = "prefix") -> dict[str, Any]:
        delivered.append(len(case["frames"]))
        return original_call(self, case, mode)

    monkeypatch.setattr(TaskJournal, "_append", fail)
    monkeypatch.setattr(Research, "call", spy)
    wire = recordings()
    with pytest.raises(RuntimeError, match="no further sends"):
        run(prepared, wire)
    assert len(wire.bodies) == 2 and delivered == [0, 1]


def test_actual_http_transport_is_bound_to_commits_with_mock_connection(tmp_path: Path) -> None:
    """Human-shaped fixtures in pytest temp are not real approvals or operator intake."""
    corpus = old_probe.prepare()
    history = history_fixture(corpus, tmp_path)
    path = tmp_path / "probe-runs.sqlite3"
    imported, old_approval, _ = fixture_case(path, corpus, synthetic=False, history=history)

    def fake_http(replies: list[str], task_mode: bool) -> tuple[paid.HTTPProbeTransport, list[str]]:
        calls: list[str] = []

        class Response:
            status, length, chunked, chunk_left = 200, 0, False, None

            def __init__(self, body: bytes) -> None:
                self.body = body

            def read1(self, size: int) -> bytes:
                body, self.body = self.body, b""
                return body

            def isclosed(self) -> bool:
                return True

        class Connection:
            raw: str = ""

            def request(self, method: str, url: str, body: bytes, headers: dict[str, str]) -> None:
                assert method == "POST" and url == "/v1/responses"
                if task_mode:
                    task_run = next(
                        r
                        for r in cast(dict[str, Any], read_snapshot(path))["runs"]
                        if r["binding"]["protocol"] == PROTOCOL
                    )
                    assert task_run["entries"][-1]["stage"] == "ATTEMPTED"
                    assert task_run["entries"][-1]["data"]["canonical_utf8"].encode() == body
                self.raw = replies.pop(0)
                calls.append("request")

            def getresponse(self) -> Response:
                return Response(self.raw.encode())

            def close(self) -> None:
                pass

        def credential() -> str:
            if task_mode:
                task_run = next(
                    r
                    for r in cast(dict[str, Any], read_snapshot(path))["runs"]
                    if r["binding"]["protocol"] == PROTOCOL
                )
                assert task_run["entries"][-1]["stage"] == "RESERVED"
            calls.append("mock-credential")
            return "synthetic-credential-never-from-environment"

        def connect(host: str, timeout: float) -> Connection:
            assert host == "api.openai.com"
            return Connection()

        return paid.HTTPProbeTransport(
            credential=credential, connection=connect, clock=lambda: 0
        ), calls

    old_replies = [cast(str, reply(old_probe.WAIT, f"http-B-{i}").raw_response) for i in range(72)]
    old_wire, _ = fake_http(old_replies, False)
    old_report = continued_probe.execute(path, imported, old_approval, transport=old_wire)
    anchor = Anchor.parse(old_report["latest_recorded_checkpoint"])
    research = Research.capture(RESEARCH)
    output = tmp_path / "task-http-mock"
    rid, aid, risk = (str(uuid4()) for _ in range(3))
    bound = binding(
        path, output, anchor, read_snapshot(path), research, ACTUAL_SOURCE, rid, aid, risk
    )
    bound["synthetic"] = False
    a = TaskApproval(
        aid,
        risk,
        "human-user",
        "approve-one-memoryreplica-batch",
        ("preserve-prior-uncertainty", "read-openai-api-key", "paid-responses"),
        old_approval.valid_from,
        old_approval.expires_at,
        bound,
    )
    new_wire, calls = fake_http(
        [cast(str, cast(paid.WireReply, r).raw_response) for r in recordings().replies], True
    )
    report = task.execute(path, anchor, a, research, ACTUAL_SOURCE, output, transport=new_wire)
    assert report["accounting"]["status"] == "FINISHED"
    assert calls.count("mock-credential") == 1 and calls.count("request") == 27
    assert new_wire._key is None
    assert all(c["result"]["stop"] == "ARRIVED" for c in report["research_cases"])


def test_phantom_arrival_without_request_response_evidence_is_rejected() -> None:
    bound = {
        "protocol": PROTOCOL,
        "plan": TaskPlan().data,
        "run_id": str(uuid4()),
        "cases": [{"case_id": "m01", "memory_policy": "none"}],
        "prior_known_nano_usd": 0,
        "prior_unresolved_nano_usd": 336864000,
    }
    claim = {
        "ordinal": 1,
        "run_id": bound["run_id"],
        "previous": "0" * 64,
        "stage": "RUN_CLAIMED",
        "data": {"binding": bound},
    }
    forged = {
        "ordinal": 2,
        "run_id": bound["run_id"],
        "previous": digest(claim),
        "stage": "CASE",
        "data": {
            "result": {"case_id": "m01", "memory_policy": "none", "frames": [], "stop": "ARRIVED"}
        },
    }
    with pytest.raises(ValueError, match="settled request/response"):
        reduce(bound, [claim, forged])
