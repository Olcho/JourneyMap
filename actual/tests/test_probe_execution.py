"""Admission, crash boundaries, raw-linked accounting and no-recall replay.

Every approval in this file is a synthetic test object, including the human-shaped
objects used with in-process fake HTTPS connections. No credential files are used.
"""

import socket
import sqlite3
import subprocess
import sys
from dataclasses import replace
from http.client import HTTPResponse
from io import BytesIO
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from probe_policy_fixtures import audit_fixture_report, execute_http_fixture, read_fixture_journal

from journeymap.adapters.life_responses import synthetic_response
from journeymap.adapters.llm import strict_json
from journeymap.adapters.probe_execution import (
    Ledger,
    ProbeApproval,
    ProbePlan,
    binding,
    object_,
    rows,
)
from journeymap.adapters.probe_journal import ProbeJournal, read_journal, reduce_trace
from journeymap.core.canonical import (
    JsonObject,
    JsonValue,
    canonical_json,
    clone_json_object,
    state_digest,
)
from journeymap.experiments import life_reasoning_probe as probe
from journeymap.experiments import paid_probe as paid


@pytest.fixture(autouse=True)
def no_external_access(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError("network or real credential access forbidden")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(paid, "real_credential", forbidden)
    monkeypatch.setattr(paid, "real_connection", forbidden)


@pytest.fixture(scope="module")
def corpus() -> JsonObject:
    return probe.prepare()


def approval(
    path: Path,
    corpus: JsonObject,
    *,
    run_id: str | None = None,
    approval_id: str | None = None,
    synthetic: bool = True,
) -> ProbeApproval:
    return ProbeApproval(
        approval_id or str(uuid4()),
        "offline-fixture" if synthetic else "human-user",
        "synthetic-probe-only" if synthetic else "approve-one-paid-probe",
        ("recording-only",) if synthetic else ("read-openai-api-key", "paid-responses"),
        binding(run_id or str(uuid4()), path, corpus),
    )


def transport(corpus: JsonObject) -> paid.RecordingProbeTransport:
    return paid.RecordingProbeTransport(
        tuple(
            paid.WireReply(
                cast(int, p["http_status"]),
                cast(str, p["raw_response"]),
                cast(int, p["elapsed_ms"]),
            )
            for p in rows(probe.mock_packets(corpus)["records"])
        )
    )


def run(path: Path, corpus: JsonObject, wire: paid.RecordingProbeTransport) -> JsonObject:
    a = approval(path, corpus)
    return paid.execute(str(a.bound["run_id"]), path, a, transport=wire)


@pytest.fixture(scope="module")
def full(tmp_path_factory: pytest.TempPathFactory, corpus: JsonObject) -> JsonObject:
    return run(
        tmp_path_factory.mktemp("full-probe") / "probe-runs.sqlite3", corpus, transport(corpus)
    )


def raw(**changes: JsonValue) -> str:
    doc = object_(strict_json(synthetic_response(probe.WAIT)))
    doc.update(changes)
    return canonical_json(doc)


def test_full_frozen_order_and_accounting(full: JsonObject, corpus: JsonObject) -> None:
    account = object_(full["accounting"])
    assert account["status"] == "FINISHED"
    assert account["stop_reason"] == "ALL_SLOTS_ATTEMPTED"
    assert account["attempted_sends"] == 72
    assert object_(account["ledger"])["reserved_cost_nano_usd"] == 0
    journal = object_(full["journal"])
    bodies = [
        object_(row["data"])["body"]
        for row in rows(journal["entries"])
        if row["stage"] == "ATTEMPTED"
    ]
    assert bodies == [r["body"] for r in rows(corpus["requests"])]
    groups = object_(object_(full["evaluation"])["conditions"])
    assert len(groups) == 3
    for group in groups.values():
        assert object_(group)["classification_counts"] == {"READY": 22, "INCOMPLETE": 2}
    assert paid.audit_report(full)["data_only_equal"] is True
    assert paid.audit_report(full)["provider_recalled"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("model", "other"),
        ("request_limit", 73),
        ("cost_limit_nano_usd", 2_000_000_000),
        ("retries", 1),
    ],
)
def test_plan_does_not_silently_expand(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="new version"):
        replace(ProbePlan(), **{field: value})  # type: ignore[arg-type]


def test_proposal_is_not_approval_and_does_not_create_registry(tmp_path: Path) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    p = paid.proposal(str(uuid4()), path)
    assert p["authorized"] is False
    assert not path.exists()
    assert p["max_output_only_nano_usd"] == 2_654_208_000
    assert p["conditional_per_request_reservation_nano_usd"] == 336_864_000


@pytest.mark.parametrize(
    "wrong", ["source", "plan", "corpus", "registry", "decision", "scope", "actor", "absent"]
)
def test_bad_approval_precedes_registry_and_keys(
    tmp_path: Path, corpus: JsonObject, wrong: str
) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    a = approval(path, corpus, synthetic=False)
    run_id = str(a.bound["run_id"])
    if wrong in {"source", "plan", "corpus", "registry"}:
        bound = clone_json_object(a.bound)
        bound[
            {
                "source": "source_digest",
                "plan": "plan",
                "corpus": "corpus_digest",
                "registry": "registry",
            }[wrong]
        ] = "wrong"
        a = replace(a, bound=bound)
    elif wrong == "decision":
        a = replace(a, decision="approve-one-paid-life-run")
    elif wrong == "scope":
        a = replace(a, scope=("paid-responses",))
    elif wrong == "actor":
        a = replace(a, approved_by="offline-fixture")
    with pytest.raises(PermissionError):
        paid.execute(run_id, path, None if wrong == "absent" else a)
    assert not path.exists()


def test_synthetic_approval_cannot_enter_http(tmp_path: Path, corpus: JsonObject) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    a = approval(path, corpus)
    with pytest.raises(PermissionError):
        paid.execute(str(a.bound["run_id"]), path, a)
    assert not path.exists()


@pytest.mark.parametrize("which", ["run", "approval"])
def test_consumed_ids_cannot_be_reused(tmp_path: Path, corpus: JsonObject, which: str) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    a = approval(path, corpus)
    wire = paid.RecordingProbeTransport((paid.WireReply(200, raw(), 120_001),))
    paid.execute(str(a.bound["run_id"]), path, a, transport=wire)
    b = approval(
        path,
        corpus,
        run_id=str(a.bound["run_id"]) if which == "run" else None,
        approval_id=a.approval_id if which == "approval" else None,
    )
    fresh = transport(corpus)
    with pytest.raises(sqlite3.IntegrityError):
        paid.execute(str(b.bound["run_id"]), path, b, transport=fresh)
    assert fresh.bodies == []


@pytest.mark.parametrize("stage", ["RESERVED", "ATTEMPTED", "RESPONSE", "SETTLED"])
def test_crash_is_durable_never_reopened(
    tmp_path: Path, corpus: JsonObject, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    a = approval(path, corpus)
    original = ProbeJournal.append

    def crash(self: ProbeJournal, kind: str, data: JsonObject) -> None:
        original(self, kind, data)
        if kind == stage:
            raise RuntimeError("simulated process termination")

    monkeypatch.setattr(ProbeJournal, "append", crash)
    wire = transport(corpus)
    with pytest.raises(RuntimeError, match="termination"):
        paid.execute(str(a.bound["run_id"]), path, a, transport=wire)
    snapshot = read_journal(path, str(a.bound["run_id"]))
    assert object_(snapshot["snapshot"])["status"] == "OPEN"
    assert len(wire.bodies) == (0 if stage in {"RESERVED", "ATTEMPTED"} else 1)
    report = paid.make_report(corpus, snapshot)
    assert object_(report["accounting"])["total_cost_nano_usd"] is None
    monkeypatch.setattr(ProbeJournal, "append", original)
    b = approval(path, corpus)
    fresh = transport(corpus)
    with pytest.raises(PermissionError, match="open or uncertain"):
        paid.execute(str(b.bound["run_id"]), path, b, transport=fresh)
    assert fresh.bodies == []


@pytest.mark.parametrize(
    "reply,classification,known",
    [
        (paid.WireReply(200, raw(), 120_001), "TIMEOUT", True),
        (paid.WireReply(503, raw(), 1), "HTTP_ERROR", False),
        (paid.WireReply(200, raw(model="wrong-model"), 1), "IDENTITY_MISMATCH", False),
        (paid.WireReply(200, raw(service_tier="flex"), 1), "IDENTITY_MISMATCH", False),
        (paid.WireReply(200, raw(usage=None), 1), "INVALID_RESPONSE", False),
        (paid.WireReply(None, None, 120_000, "TRANSPORT_UNCERTAIN"), "TRANSPORT_UNCERTAIN", False),
        (paid.WireReply(200, "[" * 2000, 1), "INVALID_RESPONSE", False),
        (paid.WireReply(200, raw(status="failed"), 1), "RESPONSE_FAILED", True),
    ],
)
def test_stop_and_usage_uncertainty(
    tmp_path: Path, corpus: JsonObject, reply: paid.WireReply, classification: str, known: bool
) -> None:
    wire = paid.RecordingProbeTransport((reply, *transport(corpus).replies))
    report = run(tmp_path / "probe-runs.sqlite3", corpus, wire)
    account = object_(report["accounting"])
    assert account["stop_reason"] == classification
    assert account["attempted_sends"] == 1
    assert account["status"] == ("FINISHED" if known else "UNCERTAIN")
    assert (account["total_cost_nano_usd"] is not None) == known
    assert len(wire.bodies) == 1
    outcome = rows(object_(report["evaluation"])["rows"])[0]
    assert outcome["engine"] is None
    if known:
        assert account["total_cost_nano_usd"] == 1_065_000  # reasoning is already in output.


def test_same_response_id_is_not_an_independent_repeat(tmp_path: Path, corpus: JsonObject) -> None:
    wire = paid.RecordingProbeTransport((paid.WireReply(200, raw(), 1),) * 72)
    report = run(tmp_path / "probe-runs.sqlite3", corpus, wire)
    assert object_(report["accounting"])["stop_reason"] == "DUPLICATE_RESPONSE_ID"
    assert object_(report["accounting"])["status"] == "UNCERTAIN"
    assert len(wire.bodies) == 2


def test_budget_cutoff_keeps_all_72_denominators(tmp_path: Path, corpus: JsonObject) -> None:
    replies = []
    for index in range(72):
        doc = object_(strict_json(raw(id=f"fixture-{index}")))
        usage = object_(doc["usage"])
        usage.update(
            output_tokens=8192, total_tokens=9192, input_tokens_details={"cached_tokens": 0}
        )
        replies.append(paid.WireReply(200, canonical_json(doc), 1))
    wire = paid.RecordingProbeTransport(tuple(replies))
    report = run(tmp_path / "probe-runs.sqlite3", corpus, wire)
    assert len(wire.bodies) == 18
    account = object_(report["accounting"])
    assert account["status"] == "FINISHED" and account["stop_reason"] == "COST_LIMIT"
    assert account["total_cost_nano_usd"] == 18 * 37_614_000
    results = rows(object_(report["evaluation"])["rows"])
    assert len(results) == 72
    assert sum(row["classification"] == "NOT_ATTEMPTED" for row in results) == 54


@pytest.mark.parametrize(
    "state,reason",
    [
        (Ledger(attempts=72), "REQUEST_LIMIT"),
        (Ledger(input_tokens=100_001), "INPUT_TOKEN_LIMIT"),
        (Ledger(output_tokens=589_824), "OUTPUT_TOKEN_LIMIT"),
        (Ledger(known_cost_nano_usd=663_136_001), "COST_LIMIT"),
        (Ledger(reserved_cost_nano_usd=1), "UNRESOLVED_RESERVATION"),
    ],
)
def test_reservation_limits(state: Ledger, reason: str) -> None:
    assert state.blocked() == reason
    with pytest.raises(ValueError, match=reason):
        state.reserve()


def reseal(journal: JsonObject) -> None:
    previous = "0" * 64
    entries = rows(journal["entries"])
    for index, row in enumerate(entries, 1):
        row["ordinal"], row["previous"] = index, previous
        previous = state_digest(row)
    object_(journal["snapshot"]).update(entry_count=len(entries), tail_digest=previous)


@pytest.mark.parametrize(
    "change",
    ["remove_response", "remove_settlement", "reorder", "usage", "body", "tail", "terminal"],
)
def test_resealed_journal_counterexamples_are_rejected(
    full: JsonObject, corpus: JsonObject, change: str
) -> None:
    journal = clone_json_object(object_(full["journal"]))
    entries = rows(journal["entries"])
    if change == "remove_response":
        del entries[3]
    elif change == "remove_settlement":
        del entries[4]
    elif change == "reorder":
        entries[2], entries[3] = entries[3], entries[2]
    elif change == "usage":
        object_(object_(entries[4]["data"])["ledger"])["known_cost_nano_usd"] = 0
    elif change == "body":
        object_(object_(entries[2]["data"])["body"])["reasoning"] = {"effort": "low"}
    elif change == "tail":
        del entries[-1]
    else:
        object_(entries[-1]["data"])["reason"] = "OPERATOR_DONE"
    journal["entries"] = cast(list[JsonValue], entries)
    reseal(journal)
    with pytest.raises(ValueError):
        paid.make_report(corpus, journal)


def test_report_summary_tamper_is_not_audit_evidence(full: JsonObject) -> None:
    changed = clone_json_object(full)
    object_(changed["accounting"])["total_cost_nano_usd"] = 0
    with pytest.raises(ValueError, match="journal-derived"):
        paid.audit_report(changed)


def test_non_probe_database_is_never_reinitialized(tmp_path: Path, corpus: JsonObject) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE life_evidence (value TEXT)")
        db.execute("INSERT INTO life_evidence VALUES ('keep')")
    before = path.read_bytes()
    with pytest.raises(sqlite3.OperationalError):
        run(path, corpus, transport(corpus))
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="dedicated"):
        binding(str(uuid4()), tmp_path / "runs.sqlite3", corpus)


class FakeResponse:
    def __init__(self, raw_bytes: bytes, status: int = 200) -> None:
        self.raw_bytes, self.status, self.done = raw_bytes, status, False

    def read1(self, size: int) -> bytes:
        self.done = True
        return self.raw_bytes

    def isclosed(self) -> bool:
        return self.done


class FakeConnection:
    def __init__(self, response: FakeResponse | HTTPResponse) -> None:
        self.response = response
        self.sent: list[tuple[str, str, bytes, dict[str, str]]] = []
        self.closed = False

    def request(self, method: str, url: str, body: bytes, headers: dict[str, str]) -> None:
        self.sent.append((method, url, body, headers))

    def getresponse(self) -> FakeResponse | HTTPResponse:
        return self.response

    def close(self) -> None:
        self.closed = True
        if isinstance(self.response, HTTPResponse):
            self.response.close()


class MemorySocket:
    def __init__(self, message: bytes) -> None:
        self.message = message

    def makefile(self, mode: str) -> BytesIO:
        assert mode == "rb"
        return BytesIO(self.message)


def framed_connection(kind: str, content: bytes) -> FakeConnection:
    if kind in {"length", "short_length"}:
        length = len(content) + (17 if kind == "short_length" else 0)
        framed = f"Content-Length: {length}\r\n\r\n".encode() + content
    elif kind == "close":
        framed = b"Connection: close\r\n\r\n" + content
    else:
        length = len(content) + (17 if kind == "short_chunk" else 0)
        terminal = {
            "chunked": b"0\r\n\r\n",
            "missing_terminal": b"",
            "short_chunk": b"",
            "invalid_chunk_size": b"invalid\r\n",
        }[kind]
        framed = (
            b"Transfer-Encoding: chunked\r\n\r\n"
            + f"{length:x}\r\n".encode()
            + content
            + (b"" if kind == "short_chunk" else b"\r\n")
            + terminal
        )
    response = HTTPResponse(cast(Any, MemorySocket(b"HTTP/1.1 200 OK\r\n" + framed)))
    response.begin()
    return FakeConnection(response)


@pytest.mark.parametrize(
    "kind",
    [
        "length",
        "chunked",
        "close",
        "short_length",
        "missing_terminal",
        "short_chunk",
        "invalid_chunk_size",
    ],
)
def test_http_framing_requires_complete_body(kind: str) -> None:
    conn = framed_connection(kind, raw().encode())
    wire = paid.HTTPProbeTransport(
        credential=lambda: "SYNTHETIC-TEST-KEY", connection=lambda h, t: conn, clock=lambda: 0
    )
    wire.prepare()
    reply = wire.send({"model": "fixture-body"})
    assert len(conn.sent) == 1 and conn.closed
    if kind in {"length", "chunked", "close"}:
        assert reply.raw_response == raw()
        assert reply.error is None
    else:
        assert reply.raw_response is None
        assert reply.error == "TRANSPORT_UNCERTAIN"


def test_http_framing_complete_late_usage_is_recovered(tmp_path: Path, corpus: JsonObject) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    a = approval(path, corpus, synthetic=False)
    conn = framed_connection("length", raw().encode())
    ticks = iter([0.0, 120.001])
    wire = paid.HTTPProbeTransport(
        credential=lambda: "SYNTHETIC-TEST-KEY",
        connection=lambda h, t: conn,
        clock=lambda: next(ticks),
    )
    report = execute_http_fixture(str(a.bound["run_id"]), path, a, transport=wire)
    account = object_(report["accounting"])
    assert account["stop_reason"] == "TIMEOUT"
    assert account["status"] == "FINISHED"
    assert account["total_cost_nano_usd"] == 1_065_000
    assert object_(account["ledger"])["reserved_cost_nano_usd"] == 0
    assert all(row["engine"] is None for row in rows(object_(report["evaluation"])["rows"]))
    assert len(conn.sent) == 1 and conn.closed


@pytest.mark.parametrize("kind", ["short_length", "missing_terminal", "short_chunk"])
def test_http_framing_incomplete_stops_execute(
    tmp_path: Path, corpus: JsonObject, kind: str
) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    a = approval(path, corpus, synthetic=False)
    conn = framed_connection(kind, raw().encode())
    wire = paid.HTTPProbeTransport(
        credential=lambda: "SYNTHETIC-TEST-KEY", connection=lambda h, t: conn, clock=lambda: 0
    )
    report = execute_http_fixture(str(a.bound["run_id"]), path, a, transport=wire)
    account = object_(report["accounting"])
    assert account["stop_reason"] == "TRANSPORT_UNCERTAIN"
    assert account["status"] == "UNCERTAIN"
    assert account["total_cost_nano_usd"] is None
    assert account["attempted_sends"] == 1
    assert object_(account["ledger"])["reserved_cost_nano_usd"] == 336_864_000
    results = rows(object_(report["evaluation"])["rows"])
    assert results[0]["classification"] == "TRANSPORT_UNCERTAIN"
    assert len(results) == 72
    assert all(row["classification"] == "NOT_ATTEMPTED" for row in results[1:])
    assert all(row["engine"] is None for row in results)
    assert len(conn.sent) == 1 and conn.closed
    assert audit_fixture_report(report)["data_only_equal"] is True
    if kind == "short_length":
        saved = tmp_path / "report.json"
        saved.write_text(canonical_json(report), encoding="utf-8")
        # A new interpreter must reproduce the stopped state without keys or networking.
        script = """
import socket, sys
from journeymap.experiments import paid_probe as paid
from pathlib import Path
from journeymap.examples.probe_continuation import main
def forbidden(*args, **kwargs):
    raise AssertionError('external access forbidden during data-only audit')
socket.socket = socket.getaddrinfo = forbidden
paid.real_credential = paid.real_connection = forbidden
sys.argv = ['probe_continuation', 'audit', sys.argv[1], '--frozen-source',
            str(Path(paid.__file__).resolve().parents[2])]
main()
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(saved)],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        audited = object_(strict_json(result.stdout))
        assert audited["data_only_equal"] is True
        assert audited["provider_recalled"] is False
        assert audited["status"] == "UNCERTAIN"


def test_fake_https_is_admitted_before_keys_and_wire(tmp_path: Path, corpus: JsonObject) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    a = approval(path, corpus, synthetic=False)
    conn = FakeConnection(FakeResponse(raw().encode()))
    ticks = iter([0.0, 120.001])

    def key() -> str:
        records = rows(read_fixture_journal(path, str(a.bound["run_id"]))["entries"])
        assert records[-1]["stage"] == "RESERVED"
        return "SYNTHETIC-TEST-KEY"

    def connection(host: str, timeout: float) -> FakeConnection:
        records = rows(read_fixture_journal(path, str(a.bound["run_id"]))["entries"])
        assert records[-1]["stage"] == "ATTEMPTED"
        assert (host, timeout) == ("api.openai.com", 120)
        return conn

    wire = paid.HTTPProbeTransport(credential=key, connection=connection, clock=lambda: next(ticks))
    report = execute_http_fixture(str(a.bound["run_id"]), path, a, transport=wire)
    assert object_(report["accounting"])["stop_reason"] == "TIMEOUT"
    assert conn.sent[0][:2] == ("POST", "/v1/responses")
    assert len(conn.sent) == 1 and conn.closed
    assert "SYNTHETIC-TEST-KEY" not in canonical_json(report)
    assert wire._key is None


@pytest.mark.parametrize(
    "kind", ["literal_secret", "escaped_secret", "invalid_json", "oversize", "redirect"]
)
def test_http_withholding_and_no_redirect(kind: str) -> None:
    key = "SYNTHETIC-TEST-KEY"
    content, status = raw().encode(), 200
    if kind == "literal_secret":
        content = raw(extra=key).encode()
    elif kind == "escaped_secret":
        content = raw(extra=key).replace(key, "".join(f"\\u{ord(c):04x}" for c in key)).encode()
    elif kind == "invalid_json":
        content = b'{"broken":'
    elif kind == "oversize":
        content = b"x" * 2_000_001
    else:
        status = 302
    conn = FakeConnection(FakeResponse(content, status))
    wire = paid.HTTPProbeTransport(
        credential=lambda: key, connection=lambda h, t: conn, clock=lambda: 0
    )
    wire.prepare()
    result = wire.send({"model": "fixture-body"})
    assert len(conn.sent) == 1 and conn.closed
    if kind != "redirect":
        assert result.raw_response is None
        assert result.error == (
            "SECRET_ECHO"
            if "secret" in kind
            else "RESPONSE_TOO_LARGE"
            if kind == "oversize"
            else "INVALID_RESPONSE"
        )
    else:
        assert result.http_status == 302


def test_credential_failure_known_not_sent(tmp_path: Path, corpus: JsonObject) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    a = approval(path, corpus, synthetic=False)
    report = execute_http_fixture(str(a.bound["run_id"]), path, a)
    account = object_(report["accounting"])
    assert account["status"] == "FINISHED"
    assert account["total_cost_nano_usd"] == 0
    assert account["attempted_sends"] == 0
    assert account["stop_reason"] == "CREDENTIAL_UNAVAILABLE"


def test_journal_detaches_mutable_callers(tmp_path: Path, corpus: JsonObject) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    a = approval(path, corpus)
    journal = ProbeJournal(path, a.bound, a, corpus, synthetic=True)
    a.bound["plan"] = "mutated"
    assert object_(journal.entries[0]["data"])["binding"] != a.bound
    journal.close()


def test_reducer_cannot_finish_empty_trace(corpus: JsonObject) -> None:
    b = binding(str(uuid4()), Path("probe-runs.sqlite3"), corpus)
    state = reduce_trace(b, corpus, [])
    assert state["status"] == "OPEN"
    assert state["total_cost_nano_usd"] is None
