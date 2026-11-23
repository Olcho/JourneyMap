"""Offline diagnostics: real exception types, fake wire, strict replay, no secrets."""

import http.client
import socket
import sqlite3
import ssl
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from probe_policy_fixtures import execute_http_fixture, read_fixture_journal, reduce_fixture_journal
from test_probe_execution import (
    FakeConnection,
    FakeResponse,
    approval,
    framed_connection,
    raw,
    reseal,
)

from journeymap.adapters import life_http
from journeymap.adapters.probe_diagnostics import (
    TransportDiagnostic,
    exception_category,
)
from journeymap.adapters.probe_execution import object_, rows
from journeymap.adapters.probe_journal import ProbeJournal
from journeymap.adapters.probe_lineage import ContinuationJournal
from journeymap.core.canonical import JsonObject, canonical_json, clone_json_object
from journeymap.experiments import life_reasoning_probe as probe
from journeymap.experiments import paid_probe as paid

PRIVATE = "PRIVATE-DIAGNOSTIC-SENTINEL-DO-NOT-STORE"


@pytest.fixture(autouse=True)
def block_external_access(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError("offline test: external access forbidden")

    for name in ("socket", "getaddrinfo", "create_connection"):
        monkeypatch.setattr(socket, name, forbidden)
    monkeypatch.setattr(http.client, "HTTPSConnection", forbidden)
    monkeypatch.setattr(life_http, "real_credential", forbidden)
    monkeypatch.setattr(life_http, "real_connection", forbidden)
    monkeypatch.setattr(paid, "real_credential", forbidden)
    monkeypatch.setattr(paid, "real_connection", forbidden)


@pytest.fixture(scope="module")
def corpus() -> JsonObject:
    return probe.prepare()


@pytest.mark.parametrize(
    "exc,category",
    [
        (TimeoutError(PRIVATE), "TIMEOUT"),
        (socket.gaierror(11001, PRIVATE), "DNS"),
        (ssl.SSLError(PRIVATE), "TLS"),
        (ssl.SSLCertVerificationError(PRIVATE), "TLS"),
        (http.client.RemoteDisconnected(PRIVATE), "REMOTE_DISCONNECTED"),
        (ConnectionResetError(PRIVATE), "CONNECTION_RESET"),
        (ConnectionRefusedError(PRIVATE), "CONNECTION_REFUSED"),
        (http.client.IncompleteRead(PRIVATE.encode(), 100), "INCOMPLETE_READ"),
        (OSError(987654, PRIVATE), "OS_ERROR"),
        (UnicodeError(PRIVATE), "UNICODE_ERROR"),
        (ValueError(PRIVATE), "VALUE_ERROR"),
        (RecursionError(PRIVATE), "RECURSION_ERROR"),
        (type(PRIVATE, (Exception,), {})(PRIVATE), "OTHER"),
    ],
)
def test_exception_allowlist_ignores_text_and_dynamic_names(exc: Exception, category: str) -> None:
    assert exception_category(exc) == category
    conn = FailingConnection("REQUEST", exc)
    wire = paid.HTTPProbeTransport(
        credential=lambda: "OFFLINE-KEY", connection=lambda h, t: conn, clock=lambda: 0
    )
    wire.prepare()
    reply = wire.send({"model": "offline"})
    assert reply.raw_response is None and reply.error == "TRANSPORT_UNCERTAIN"
    assert reply.diagnostic == TransportDiagnostic("REQUEST", category, "EXCEPTION")
    saved = canonical_json(
        reply.diagnostic.linked("run", {"request_id": "r", "body_digest": "b"}, "RESPONSE", 4)
    )
    assert PRIVATE not in saved
    assert "987654" not in saved and "partial" not in saved and "errno" not in saved


class FailingResponse(FakeResponse):
    def __init__(self, stage: str, exc: Exception) -> None:
        super().__init__(raw().encode())
        self.stage, self.exc = stage, exc

    def read1(self, size: int) -> bytes:
        if self.stage == "BODY_READ":
            raise self.exc
        return super().read1(size)

    @property
    def length(self) -> None:
        if self.stage == "FRAMING_CHECK":
            raise self.exc


class FailingConnection(FakeConnection):
    def __init__(self, stage: str, exc: Exception) -> None:
        super().__init__(FailingResponse(stage, exc))
        self.stage, self.exc = stage, exc

    def request(self, method: str, url: str, body: bytes, headers: dict[str, str]) -> None:
        super().request(method, url, body, headers)
        if self.stage == "REQUEST":
            raise self.exc

    def getresponse(self) -> FakeResponse | http.client.HTTPResponse:
        if self.stage == "RESPONSE_HEADERS":
            raise self.exc
        return super().getresponse()


@pytest.mark.parametrize(
    "stage",
    [
        "CONNECTION_OBJECT",
        "REQUEST",
        "RESPONSE_HEADERS",
        "BODY_READ",
        "FRAMING_CHECK",
        "DECODE_JSON",
        "SECRET_SCAN",
        "RUNNER",
    ],
)
def test_each_stage_is_bound_and_stops_without_engine_or_next_send(
    tmp_path: Path,
    corpus: JsonObject,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stage: str,
) -> None:
    exc = type(PRIVATE, (Exception,), {})(PRIVATE, PRIVATE.encode())
    conn = FailingConnection(stage, exc)
    connections = []

    def connect(host: str, timeout: float) -> FakeConnection:
        connections.append((host, timeout))
        if stage == "CONNECTION_OBJECT":
            raise exc
        return conn

    def fail(*args: object, **kwargs: object) -> Any:
        raise exc

    if stage == "DECODE_JSON":
        monkeypatch.setattr(paid, "strict_json", fail)
    elif stage == "SECRET_SCAN":
        monkeypatch.setattr(paid, "json", SimpleNamespace(dumps=fail))
    elif stage == "RUNNER":
        monkeypatch.setattr(paid.HTTPProbeTransport, "send", fail)
    path = tmp_path / "probe-runs.sqlite3"
    a = approval(path, corpus, synthetic=False)
    wire = paid.HTTPProbeTransport(
        credential=lambda: "OFFLINE-KEY", connection=connect, clock=lambda: 0
    )
    report = execute_http_fixture(str(a.bound["run_id"]), path, a, transport=wire)
    account = object_(report["accounting"])
    assert account["status"] == "UNCERTAIN" and account["attempted_sends"] == 1
    assert account["stop_reason"] == "TRANSPORT_UNCERTAIN"
    assert account["total_cost_nano_usd"] is None
    assert object_(account["ledger"])["reserved_cost_nano_usd"] == 336_864_000
    assert all(row["engine"] is None for row in rows(object_(report["evaluation"])["rows"]))
    assert len(connections) == (0 if stage == "RUNNER" else 1)
    assert len(conn.sent) == (0 if stage in {"RUNNER", "CONNECTION_OBJECT"} else 1)
    diagnostic = rows(report["transport_diagnostics"])[0]
    assert diagnostic["stage"] == stage and diagnostic["category"] == "OTHER"
    assert diagnostic["run_id"] == a.bound["run_id"]
    assert diagnostic["request_id"] == rows(corpus["requests"])[0]["request_id"]
    assert diagnostic["body_digest"] == rows(corpus["requests"])[0]["body_digest"]
    assert diagnostic["journal_stage"] == "RESPONSE" and diagnostic["journal_ordinal"] == 4
    condition = str(rows(corpus["requests"])[0]["condition"])
    metrics = object_(object_(object_(report["evaluation"])["conditions"])[condition])
    assert metrics["latency_known_count"] == (0 if stage == "RUNNER" else 1)
    assert PRIVATE not in canonical_json(report) and PRIVATE.encode() not in path.read_bytes()
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out + captured.err
    assert wire._key is None


def fake_http_report(path: Path, corpus: JsonObject, fail_at: int | None) -> JsonObject:
    a = approval(path, corpus, synthetic=False)
    connections: list[FakeConnection] = []

    def connect(host: str, timeout: float) -> FakeConnection:
        index = len(connections)
        conn = (
            FailingConnection("BODY_READ", ConnectionResetError(PRIVATE))
            if index == fail_at
            else framed_connection("length", raw(id=f"fixture-{index}").encode())
        )
        connections.append(conn)
        return conn

    report = execute_http_fixture(
        str(a.bound["run_id"]),
        path,
        a,
        transport=paid.HTTPProbeTransport(
            credential=lambda: "OFFLINE-KEY", connection=connect, clock=lambda: 0
        ),
    )
    assert len(connections) == (72 if fail_at is None else fail_at + 1)
    assert all(len(c.sent) == 1 for c in connections)
    return report


@pytest.fixture(scope="module")
def stopped(tmp_path_factory: pytest.TempPathFactory, corpus: JsonObject) -> JsonObject:
    return fake_http_report(
        tmp_path_factory.mktemp("diagnostic-stopped") / "probe-runs.sqlite3", corpus, 1
    )


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "version",
        "extra",
        "stage",
        "category",
        "complete",
        "none_exception",
        "early_stage_with_status",
        "other_request",
        "body",
        "run",
        "ordinal",
        "event",
        "wrong_error",
        "false_late",
        "recording_in_http",
        "bool_ordinal",
    ],
)
def test_resealed_diagnostic_tamper_is_rejected(
    stopped: JsonObject, corpus: JsonObject, change: str
) -> None:
    journal = clone_json_object(object_(stopped["journal"]))
    entries = rows(journal["entries"])
    data = object_(entries[7]["data"])
    d = object_(data["diagnostic"])
    if change == "missing":
        del data["diagnostic"]
    elif change == "version":
        d["version"] = "old"
    elif change == "extra":
        d["exception_text"] = "unsupported"
    elif change == "stage":
        d["stage"] = "DNS_CONNECTED"
    elif change == "category":
        d["category"] = "unexpected"
    elif change == "complete":
        d["framing_complete"] = True
    elif change == "none_exception":
        d["category"] = "NONE"
    elif change == "early_stage_with_status":
        d["stage"] = "RESPONSE_HEADERS"
    elif change == "other_request":
        data["diagnostic"] = object_(entries[3]["data"])["diagnostic"]
    elif change == "body":
        d["body_digest"] = "wrong"
    elif change == "run":
        d["run_id"] = "wrong"
    elif change == "ordinal":
        d["journal_ordinal"] = 4
    elif change == "event":
        d["journal_stage"] = "NOT_SENT"
    elif change == "wrong_error":
        d.update(stage="BODY_READ", category="NONE", disposition="DEADLINE_PARTIAL")
    elif change == "false_late":
        object_(object_(entries[3]["data"])["diagnostic"])["disposition"] = "LATE_COMPLETE"
    elif change == "recording_in_http":
        d.update(stage="RECORDING", category="NONE", disposition="RECORDED")
    else:
        d["journal_ordinal"] = True
    reseal(journal)
    with pytest.raises(ValueError, match=r"diagnostic|response evidence"):
        reduce_fixture_journal(object_(journal["binding"]), corpus, rows(journal["entries"]))


@pytest.mark.parametrize("after_commit", [False, True])
def test_diagnostic_storage_failure_stops_and_exposes_only_fixed_error(
    tmp_path: Path,
    corpus: JsonObject,
    monkeypatch: pytest.MonkeyPatch,
    after_commit: bool,
) -> None:
    original = ContinuationJournal._append

    def fail(self: ContinuationJournal, stage: str, data: JsonObject) -> None:
        if stage == "RESPONSE":
            if after_commit:
                original(self, stage, data)
            raise OSError(PRIVATE)
        original(self, stage, data)

    monkeypatch.setattr(ContinuationJournal, "_append", fail)
    path = tmp_path / "probe-runs.sqlite3"
    a = approval(path, corpus, synthetic=False)
    conn = framed_connection("length", raw().encode())
    wire = paid.HTTPProbeTransport(
        credential=lambda: "OFFLINE-KEY", connection=lambda h, t: conn, clock=lambda: 0
    )
    with pytest.raises(RuntimeError, match="journal write failed") as caught:
        execute_http_fixture(str(a.bound["run_id"]), path, a, transport=wire)
    assert PRIVATE not in "".join(traceback.format_exception(caught.value))
    assert len(conn.sent) == 1
    journal = read_fixture_journal(path, str(a.bound["run_id"]))
    reduced = reduce_fixture_journal(object_(journal["binding"]), corpus, rows(journal["entries"]))
    assert reduced["status"] == "OPEN" and reduced["total_cost_nano_usd"] is None
    assert object_(reduced["ledger"])["reserved_cost_nano_usd"] == 336_864_000
    assert rows(journal["entries"])[-1]["stage"] == ("RESPONSE" if after_commit else "ATTEMPTED")


def test_missing_http_diagnostic_is_not_fabricated(
    tmp_path: Path, corpus: JsonObject, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(self: paid.HTTPProbeTransport, body: JsonObject) -> paid.WireReply:
        return paid.WireReply(200, raw(), 1)

    monkeypatch.setattr(paid.HTTPProbeTransport, "send", missing)
    path = tmp_path / "probe-runs.sqlite3"
    a = approval(path, corpus, synthetic=False)
    with pytest.raises(ValueError, match="missing transport diagnostic"):
        execute_http_fixture(
            str(a.bound["run_id"]),
            path,
            a,
            transport=paid.HTTPProbeTransport(credential=lambda: "OFFLINE-KEY"),
        )
    assert (
        object_(read_fixture_journal(path, str(a.bound["run_id"]))["snapshot"])["status"] == "OPEN"
    )


def test_v1_binding_and_registry_are_not_upgraded(tmp_path: Path, corpus: JsonObject) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    a = approval(path, corpus)
    old_bound = clone_json_object(a.bound)
    old_bound["protocol"] = "life-reasoning-probe-execution-1"
    old_bound["source_digest"] = "a362689bd6589ba7512610eabdb0b97a60aab3b8e704306d0dcfabe54b5edf56"
    with pytest.raises(PermissionError):
        a.validate(old_bound, synthetic=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE probe_meta(version TEXT NOT NULL)")
        db.execute("INSERT INTO probe_meta VALUES ('life-reasoning-probe-execution-1')")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="not a probe registry"):
        ProbeJournal(path, a.bound, a, corpus, synthetic=True)
    assert path.read_bytes() == before


def test_diagnostic_constructor_rejects_unbounded_values() -> None:
    with pytest.raises(ValueError, match="invalid transport diagnostic"):
        TransportDiagnostic(PRIVATE, "OTHER", "EXCEPTION")
    with pytest.raises(ValueError):
        TransportDiagnostic("BODY_READ", "OTHER", "EXCEPTION", cast(bool, 1))


@pytest.mark.parametrize("kind", ["late", "partial", "exception"])
def test_exception_timeout_partial_deadline_and_late_complete_are_distinct(kind: str) -> None:
    class OpenResponse(FakeResponse):
        def isclosed(self) -> bool:
            return False

    complete = framed_connection("length", raw().encode())
    partial = FakeConnection(OpenResponse(raw().encode()))
    failure = FailingConnection("BODY_READ", TimeoutError(PRIVATE))
    conn, ticks, disposition, category, error, retained = {
        "late": (complete, [0.0, 120.001], "LATE_COMPLETE", "NONE", None, True),
        "partial": (partial, [0.0, 120.001, 120.001], "DEADLINE_PARTIAL", "NONE", "TIMEOUT", False),
        "exception": (failure, [0.0, 0.001], "EXCEPTION", "TIMEOUT", "TRANSPORT_UNCERTAIN", False),
    }[kind]
    clock = iter(ticks)
    wire = paid.HTTPProbeTransport(
        credential=lambda: "OFFLINE-KEY", connection=lambda h, t: conn, clock=lambda: next(clock)
    )
    wire.prepare()
    reply = wire.send({"model": "offline"})
    assert reply.diagnostic is not None
    assert reply.diagnostic.disposition == disposition and reply.diagnostic.category == category
    assert reply.error == error and (reply.raw_response is not None) == retained


def test_credential_diagnostic_is_linked_without_claiming_an_http_attempt(
    tmp_path: Path,
    corpus: JsonObject,
) -> None:
    path = tmp_path / "probe-runs.sqlite3"
    a = approval(path, corpus, synthetic=False)

    def credential() -> str:
        raise ValueError(PRIVATE)

    report = execute_http_fixture(
        str(a.bound["run_id"]), path, a, transport=paid.HTTPProbeTransport(credential=credential)
    )
    account = object_(report["accounting"])
    assert account["status"] == "FINISHED" and account["attempted_sends"] == 0
    assert account["total_cost_nano_usd"] == 0
    d = rows(report["transport_diagnostics"])[0]
    assert d["stage"] == "CREDENTIAL" and d["category"] == "VALUE_ERROR"
    assert d["journal_stage"] == "NOT_SENT" and d["journal_ordinal"] == 3
    assert PRIVATE not in canonical_json(report)


@pytest.mark.parametrize("size", [1_999_999, 2_000_000, 2_000_001])
def test_http_body_byte_limit_with_stdlib_framing(size: int) -> None:
    conn = framed_connection("length", b"{}" + b" " * (size - 2))
    wire = paid.HTTPProbeTransport(
        credential=lambda: "OFFLINE-KEY", connection=lambda h, t: conn, clock=lambda: 0
    )
    wire.prepare()
    reply = wire.send({"model": "offline"})
    assert reply.diagnostic is not None
    if size <= 2_000_000:
        assert reply.error is None and reply.raw_response is not None
        assert reply.diagnostic.disposition == "COMPLETE"
    else:
        assert reply.error == "RESPONSE_TOO_LARGE" and reply.raw_response is None
        assert reply.diagnostic.disposition == "SIZE_LIMIT"


@pytest.mark.parametrize("kind,category", [("utf8", "UNICODE_ERROR"), ("deep", "RECURSION_ERROR")])
def test_json_boundary_errors_withhold_raw_after_complete_framing(kind: str, category: str) -> None:
    # Valid nested JSON, deeper than this interpreter's configured limit.
    # An unterminated '[' prefix can instead fail with JSONDecodeError/ValueError.
    depth = sys.getrecursionlimit() + 100
    content = b"\xff" if kind == "utf8" else b"[" * depth + b"]" * depth
    conn = framed_connection("length", content)
    wire = paid.HTTPProbeTransport(
        credential=lambda: "OFFLINE-KEY", connection=lambda h, t: conn, clock=lambda: 0
    )
    wire.prepare()
    reply = wire.send({"model": "offline"})
    assert reply.error == "INVALID_RESPONSE" and reply.raw_response is None
    assert reply.diagnostic == TransportDiagnostic("DECODE_JSON", category, "EXCEPTION", True)
