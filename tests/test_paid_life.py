"""All paid code tests use synthetic approval/key and a fake HTTPS connection."""

import json
import os
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from journeymap.adapters.life_admission import (
    ExecutionApproval,
    JournalFailure,
    RunJournal,
    binding,
)
from journeymap.adapters.life_http import (
    LifeHTTPProvider,
    PaidFailure,
    admit_provider,
    paid_request,
)
from journeymap.adapters.life_llm import LifeLLMConfig, scripted_responses
from journeymap.adapters.life_responses import LifeExecutionPlan, synthetic_response
from journeymap.adapters.provider import provider_identity
from journeymap.experiments.checkpoint import save_checkpoint
from journeymap.experiments.life import LifeSession
from journeymap.experiments.mixed_life import MixedLifeSession
from journeymap.experiments.paid_life import PaidLifeSession
from journeymap.experiments.paid_replay import replay_paid_report
from journeymap.scenarios.life import LifeConfig

SECRET = "sk-synthetic-NOT-A-REAL-KEY-54321"


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    original = os.environ.get

    def deny(*args: object, **kwargs: object) -> Any:
        raise AssertionError("real key/DNS/socket forbidden")

    def guarded(key: str, default: Any = None) -> Any:
        return deny() if key == "OPENAI_API_KEY" else original(key, default)

    monkeypatch.setattr(os.environ, "get", guarded)
    monkeypatch.setattr("socket.socket", deny)
    monkeypatch.setattr("socket.getaddrinfo", deny)


class Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


class FakeResponse:
    def __init__(self, raw: str, status: int = 200, gate: threading.Event | None = None) -> None:
        self.data = raw.encode()
        self.status = status
        self.gate = gate
        self.entered = threading.Event()
        self.headers = {"Authorization": SECRET, "X-Echo": SECRET}

    def read1(self, size: int) -> bytes:
        self.entered.set()
        if self.gate is not None:
            assert self.gate.wait(2), "release fake response gate"
        part, self.data = self.data[:size], self.data[size:]
        return part

    def isclosed(self) -> bool:
        return not self.data


class FakeConnection:
    def __init__(self, owner: "FakeHTTPS", response: FakeResponse | Exception) -> None:
        self.owner, self.response = owner, response
        self.closed = False

    def request(self, method: str, url: str, body: bytes, headers: dict[str, str]) -> None:
        self.owner.sent.append((method, url, body, headers))
        if self.owner.partial_error:
            raise OSError(SECRET + " partial send private detail")

    def getresponse(self) -> FakeResponse:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def close(self) -> None:
        self.closed = True


class FakeHTTPS:
    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = responses
        self.sent: list[tuple[str, str, bytes, dict[str, str]]] = []
        self.created: list[tuple[str, float]] = []
        self.connections: list[FakeConnection] = []
        self.partial_error = False

    def __call__(self, host: str, timeout: float) -> FakeConnection:
        self.created.append((host, timeout))
        connection = FakeConnection(self, self.responses[len(self.connections)])
        self.connections.append(connection)
        return connection


def approval(run: str, registry: Path) -> ExecutionApproval:
    # Synthetic test grant; never installed in the real canonical registry.
    return ExecutionApproval(
        str(uuid4()),
        "human-user",
        "approve-one-paid-life-run",
        ("read-openai-api-key", "paid-responses"),
        binding(run, registry),
    )


def compose(tmp_path: Path, fake: FakeHTTPS, clock: Clock | None = None) -> LifeHTTPProvider:
    registry = tmp_path / "runs.sqlite3"
    run = str(uuid4())
    return admit_provider(
        run,
        approval(run, registry),
        registry,
        credential=lambda: SECRET,
        connection=fake,
        clock=clock or Clock(),
    )


def current_request() -> Any:
    session = LifeSession(LifeConfig(horizon=24))
    try:
        return paid_request(session.application.game_for("ada").observe(), LifeExecutionPlan())
    finally:
        session.close()


def safe_records(p: LifeHTTPProvider) -> str:
    text = json.dumps(p.journal.records())
    assert SECRET not in text and "private detail" not in text
    return text


def test_no_approval_or_mismatch_cannot_touch_credentials_or_connection(tmp_path: Path) -> None:
    run, registry = str(uuid4()), tmp_path / "runs.sqlite3"
    fake = FakeHTTPS([])
    for grant in (
        None,
        replace(approval(run, registry), decision="NOT_APPROVED"),
        replace(approval(run, registry), bound=binding(str(uuid4()), registry)),
    ):
        with pytest.raises(PermissionError):
            admit_provider(
                run, grant, registry, credential=lambda: pytest.fail("credential"), connection=fake
            )
    assert not fake.created and not registry.exists()


def test_run_and_approval_are_durable_single_use_in_fresh_connection(tmp_path: Path) -> None:
    run, registry = str(uuid4()), tmp_path / "runs.sqlite3"
    grant = approval(run, registry)
    journal = RunJournal(registry, binding(run, registry), grant)
    journal.finish(True)
    journal.close()
    with pytest.raises(PermissionError):
        RunJournal(registry, binding(run, registry), grant)
    new_run = str(uuid4())
    with pytest.raises(PermissionError):
        RunJournal(
            registry, binding(new_run, registry), replace(grant, bound=binding(new_run, registry))
        )
    with sqlite3.connect(registry) as db:
        assert db.execute("SELECT status FROM runs WHERE run_id=?", (run,)).fetchone() == (
            "UNCERTAIN",
        )


@pytest.mark.parametrize("stage", ["PREPARED_NOT_SENT", "ATTEMPTED"])
def test_durable_write_failure_before_send_blocks_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    fake = FakeHTTPS(
        [FakeResponse(synthetic_response('{"action_type":"WAIT","payload":{"duration":1}}'))]
    )
    p = compose(tmp_path, fake)
    original = p.journal.append

    def fail(name: str, data: Any) -> None:
        if name == stage:
            raise JournalFailure("synthetic")
        original(name, data)

    monkeypatch.setattr(p.journal, "append", fail)
    with pytest.raises(PaidFailure, match="JOURNAL_UNAVAILABLE"):
        p.generate(current_request())
    assert not fake.created and not fake.sent
    assert p.budget.stop == "JOURNAL_UNAVAILABLE"
    safe_records(p)
    p.journal.close()


def test_attempt_is_durable_before_connection_and_response_write_failure_is_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeHTTPS([FakeResponse(synthetic_response("{}"))])
    p = compose(tmp_path, fake)
    original = p.journal.append

    def fail(stage: str, data: Any) -> None:
        if stage == "RESPONSE_RECEIVED":
            raise JournalFailure("synthetic")
        original(stage, data)

    monkeypatch.setattr(p.journal, "append", fail)

    def connect(host: str, timeout: float) -> FakeConnection:
        with sqlite3.connect(p.journal.path) as db:
            records = db.execute("SELECT payload FROM entries").fetchall()
            assert any(json.loads(r[0])["stage"] == "ATTEMPTED" for r in records)
        return fake(host, timeout)

    p._connection = connect
    with pytest.raises(PaidFailure, match="JOURNAL_UNAVAILABLE"):
        p.generate(current_request())
    with pytest.raises(PaidFailure):
        p.generate(current_request())
    assert len(fake.sent) == 1 and p.budget.reserved_cost == 318_432_000
    assert p.cost_state == "UNCERTAIN" and fake.connections[0].closed
    safe_records(p)
    p.journal.close()


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("attempts", 32, "REQUEST_LIMIT"),
        ("input_tokens", 100001, "INPUT_TOKEN_LIMIT"),
        ("output_tokens", 126977, "OUTPUT_TOKEN_LIMIT"),
        ("cost_nano_usd", 681568001, "COST_LIMIT"),
    ],
)
def test_paid_budget_blocks_before_credentials(
    tmp_path: Path, field: str, value: int, reason: str
) -> None:
    p = compose(tmp_path, FakeHTTPS([]))
    p._credential = lambda: pytest.fail("credential before cap")
    setattr(p.budget, field, value)
    with pytest.raises(PaidFailure, match=reason):
        p.generate(current_request())
    p.journal.close()


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"model": "wrong-model"}, "IDENTITY_MISMATCH"),
        ({"service_tier": "priority"}, "SERVICE_TIER_MISMATCH"),
        ({"usage": None}, "USAGE_UNAVAILABLE"),
    ],
)
def test_unknown_pricing_keeps_reported_usage_separate_and_reservation(
    tmp_path: Path, change: dict[str, Any], reason: str
) -> None:
    doc = json.loads(synthetic_response("{}")) | change
    p = compose(tmp_path, FakeHTTPS([FakeResponse(json.dumps(doc))]))
    with pytest.raises(PaidFailure, match=reason):
        p.generate(current_request())
    assert p.budget.cost_nano_usd == 0 and p.budget.reserved_cost == 318432000
    assert p.cost_state == "UNCERTAIN"
    assert bool(p.reported_usage) == (change.get("usage", True) is not None)
    assert p.accounting()["unknown_model_or_tier_cost_bounded"] is False
    safe_records(p)
    p.journal.close()


@pytest.mark.parametrize(
    "where", ["text", "nested", "escaped", "malformed", "exception", "partial"]
)
def test_secret_echo_is_not_in_raw_journal_export_or_engine(tmp_path: Path, where: str) -> None:
    doc = json.loads(synthetic_response('{"action_type":"WAIT","payload":{"duration":1}}'))
    if where == "text":
        doc["output"][0]["content"][0]["text"] = SECRET
    if where == "nested":
        doc["untrusted_metadata"] = {"a": [{"nested": SECRET}]}
    raw = json.dumps(doc)
    if where == "escaped":
        raw = raw.replace("synthetic-life-response", "".join(f"\\u{ord(c):04x}" for c in SECRET))
    if where == "malformed":
        raw = '{"broken":' + json.dumps(SECRET)
    outcome: FakeResponse | Exception = (
        OSError(SECRET + " private detail") if where == "exception" else FakeResponse(raw)
    )
    fake = FakeHTTPS([outcome])
    fake.partial_error = where == "partial"
    p = compose(tmp_path, fake)
    s = PaidLifeSession(p, clock=Clock())
    try:
        with pytest.raises(ValueError, match="PAUSED_ERROR"):
            s.run_until(1)
        assert s.kernel.simulation_time == 0 and not s.application.attempts
        serialized = json.dumps(s.controller_report())
        assert SECRET not in serialized and "private detail" not in serialized
        assert s.decider.state.calls[0].raw is None
        assert p.budget.reserved_cost == 318432000
        safe_records(p)
    finally:
        s.close()


@pytest.mark.parametrize("complete", [219.999, 220.0, 220.001])
def test_owner_completion_boundary_recovers_late_usage_without_action(
    tmp_path: Path, complete: float
) -> None:
    gate = threading.Event()
    clock = Clock()
    response = FakeResponse(
        synthetic_response('{"action_type":"WAIT","payload":{"duration":1}}'), gate=gate
    )
    p = compose(tmp_path, FakeHTTPS([response]), clock)
    s = PaidLifeSession(p, clock=clock)
    try:
        s.progress(1, 0)
        assert response.entered.wait(2)
        clock.value = complete
        gate.set()
        assert s.decider._signal.wait(2)
        clock.value = 230
        s.decider.poll()
        s.progress(1, 0)
        assert p.budget.cost_nano_usd == 1065000 and p.budget.reserved_cost == 0
        assert s.kernel.simulation_time == (1 if complete <= 220 else 0)
        if complete > 220:
            assert s.decider.state.calls[0].discarded_late
            assert not s.application.attempts
        assert s.replay_recorded()["provider_recalled"] is False
        safe_records(p)
    finally:
        gate.set()
        s.close()


def test_timeout_first_then_late_recovery_is_journaled(tmp_path: Path) -> None:
    gate = threading.Event()
    clock = Clock()
    response = FakeResponse(
        synthetic_response('{"action_type":"WAIT","payload":{"duration":1}}'), gate=gate
    )
    p = compose(tmp_path, FakeHTTPS([response]), clock)
    s = PaidLifeSession(p, clock=clock)
    try:
        s.progress(1, 0)
        assert response.entered.wait(2)
        clock.value = 220.001
        s.decider.poll()
        assert s.decider.state.status == "PAUSED_ERROR" and p.budget.reserved_cost == 318432000
        assert not s.drain(0)
        clock.value = 221
        gate.set()
        assert s.decider._signal.wait(2)
        s.decider.poll()
        assert p.budget.reserved_cost == 0 and p.reported_usage and s.kernel.simulation_time == 0
        stages = [r["stage"] for r in p.journal.records()]
        assert "OWNER_TIMEOUT" in stages and "USAGE_PRICED" in stages
        assert s.decider.state.calls[0].discarded_late
        assert s.replay_recorded()["provider_recalled"] is False
    finally:
        gate.set()
        s.close()


def test_normal_paid_composition_preserves_fixture_and_no_recall_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = MixedLifeSession(LifeConfig(horizon=24))
    try:
        baseline.run_until(24)
        expected = baseline.report()
    finally:
        baseline.close()
    from journeymap.adapters.life import LifeController

    decide = LifeController.decide

    def no_ada(self: LifeController, observation: Any) -> Any:
        if observation.actor_id == "ada":
            pytest.fail("Ada RULE fallback")
        return decide(self, observation)

    monkeypatch.setattr(LifeController, "decide", no_ada)
    fake = FakeHTTPS([FakeResponse(synthetic_response(r.text)) for r in scripted_responses()])
    p = compose(tmp_path, fake)
    s = PaidLifeSession(p)
    try:
        s.run_until(24)
        assert (
            s.report() == expected and len(s.kernel.events) == 171 and len(s.kernel.in_flight) == 3
        )
        assert len(fake.sent) == 12 and p.budget.attempts == 12
        assert provider_identity(p)["kind"] == "live"
        assert all(x == ("api.openai.com", 120) for x in fake.created)
        assert all(m == "POST" and u == "/v1/responses" for m, u, _, _ in fake.sent)
        assert all(
            json.loads(b)["text"]["format"]["name"] == "journeymap_life_candidate_v1"
            for _, _, b, _ in fake.sent
        )
        monkeypatch.setattr(LifeHTTPProvider, "generate", lambda *args: pytest.fail("no recall"))
        assert s.replay_recorded()["paid_journal_checked"] is True
        exported = s.export_report()
        assert replay_paid_report(exported)["engine_equal"] is True
        assert SECRET not in json.dumps(s.controller_report())
        with pytest.raises(ValueError, match="custom provider"):
            save_checkpoint(s, tmp_path / "forbidden.json")
    finally:
        s.close()


def test_default_cli_is_proposal_only(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    from journeymap.examples.life_paid import main

    monkeypatch.setattr("sys.argv", ["life_paid"])
    main()
    assert json.loads(capsys.readouterr().out)["authorized"] is False
    monkeypatch.setattr("sys.argv", ["life_paid", "--execute"])
    with pytest.raises(SystemExit):
        main()


@pytest.mark.parametrize("status", [301, 429, 500])
def test_http_error_never_follows_or_exports_response_body(tmp_path: Path, status: int) -> None:
    fake = FakeHTTPS([FakeResponse(SECRET, status=status)])
    p = compose(tmp_path, fake)
    try:
        with pytest.raises(PaidFailure, match="HTTP_ERROR"):
            p.generate(current_request())
        assert len(fake.sent) == 1 and p.budget.reserved_cost == 318432000
        assert p.cost_state == "UNCERTAIN"
        safe_records(p)
    finally:
        p.journal.close()


@pytest.mark.parametrize(
    "status,refusal,reason", [("completed", True, "REFUSAL"), ("incomplete", False, "INCOMPLETE")]
)
def test_paid_refusal_incomplete_preserve_usage_and_stop(
    tmp_path: Path, status: str, refusal: bool, reason: str
) -> None:
    p = compose(
        tmp_path, FakeHTTPS([FakeResponse(synthetic_response("", status=status, refusal=refusal))])
    )
    s = PaidLifeSession(p)
    try:
        with pytest.raises(ValueError, match="PAUSED_ERROR"):
            s.run_until(1)
        assert s.decider.state.calls[0].error == reason
        assert p.cost_state == "KNOWN_RATE_CARD" and p.budget.cost_nano_usd == 1065000
        assert p.budget.reserved_cost == 0 and s.kernel.simulation_time == 0
        assert s.replay_recorded()["provider_recalled"] is False
    finally:
        s.close()


@pytest.mark.parametrize(
    "mutation,reason",
    [
        ({"input_tokens": True}, "INVALID_USAGE"),
        ({"total_tokens": 0}, "INVALID_USAGE"),
        ({"input_tokens_details": {"cached_tokens": 1001}}, "INVALID_USAGE"),
        ({"output_tokens_details": {"reasoning_tokens": 101}}, "INVALID_USAGE"),
        ({"input_tokens": 400001, "total_tokens": 400101}, "USAGE_EXCEEDS_RESERVATION"),
    ],
)
def test_paid_invalid_or_excess_usage_is_uncertain(
    tmp_path: Path, mutation: dict[str, Any], reason: str
) -> None:
    doc = json.loads(synthetic_response("{}"))
    doc["usage"].update(mutation)
    p = compose(tmp_path, FakeHTTPS([FakeResponse(json.dumps(doc))]))
    try:
        with pytest.raises(PaidFailure, match=reason):
            p.generate(current_request())
        assert p.cost_state == "UNCERTAIN" and p.budget.reserved_cost == 318432000
        assert p.budget.cost_nano_usd == 0
        with pytest.raises(PaidFailure):
            p.generate(current_request())
    finally:
        p.journal.close()


@pytest.mark.parametrize("field", ["plan_digest", "source_digest", "protocol_digest", "registry"])
def test_approval_binding_fields_are_all_enforced(tmp_path: Path, field: str) -> None:
    run = str(uuid4())
    registry = tmp_path / "runs.sqlite3"
    grant = approval(run, registry)
    grant = replace(grant, bound=grant.bound | {field: "mismatch"})
    with pytest.raises(PermissionError):
        admit_provider(
            run,
            grant,
            registry,
            credential=lambda: pytest.fail("credential"),
            connection=FakeHTTPS([]),
        )
    assert not registry.exists()


def test_no_credential_is_known_not_sent_and_private_exception_is_hidden(tmp_path: Path) -> None:
    p = compose(tmp_path, FakeHTTPS([]))

    def missing() -> str:
        raise RuntimeError(SECRET + " private detail")

    p._credential = missing
    try:
        with pytest.raises(PaidFailure, match="CREDENTIAL_UNAVAILABLE"):
            p.generate(current_request())
        assert p.cost_state == "NOT_SENT" and p.budget.reserved_cost == 0
        assert any(r["stage"] == "NOT_SENT" for r in p.journal.records())
        safe_records(p)
    finally:
        p.journal.close()


def test_journal_integrity_and_response_metadata_tamper_are_rejected(tmp_path: Path) -> None:
    from journeymap.adapters.llm import Record

    p = compose(
        tmp_path,
        FakeHTTPS(
            [FakeResponse(synthetic_response('{"action_type":"WAIT","payload":{"duration":1}}'))]
        ),
    )
    s = PaidLifeSession(p)
    try:
        s.run_until(1)
        call = s.decider.state.calls[0]
        assert call.raw is not None
        data = call.raw.data
        cast(dict[str, Any], data["metadata"])["usage"]["input_tokens"] = 999
        s.decider.state.calls[0] = replace(call, raw=Record.capture(data))
        with pytest.raises(ValueError, match="paid journal raw mismatch"):
            s.replay_recorded()
        s.decider.state.calls[0] = call
        p.journal._db.execute("UPDATE entries SET digest='bad' WHERE ordinal=1")
        p.journal._db.commit()
        with pytest.raises(JournalFailure, match="JOURNAL_INTEGRITY"):
            p.journal.records()
    finally:
        s.close()


def test_worker_after_close_keeps_usage_without_reopening_world(tmp_path: Path) -> None:
    gate = threading.Event()
    clock = Clock()
    response = FakeResponse(
        synthetic_response('{"action_type":"WAIT","payload":{"duration":1}}'), gate=gate
    )
    p = compose(tmp_path, FakeHTTPS([response]), clock)
    s = PaidLifeSession(p, clock=clock)
    s.progress(1, 0)
    assert response.entered.wait(2)
    s.close()
    clock.value = 221
    gate.set()
    assert s.decider._signal.wait(2)
    assert s.kernel.simulation_time == 0 and not s.application.attempts
    assert p.budget.reserved_cost == 0 and p.reported_usage
    rows = p.journal.records()
    assert any(r["stage"] == "RUN_UNCERTAIN" for r in rows)
    assert any(
        r["stage"] == "RESPONSE_DELIVERY" and cast(dict[str, Any], r["data"])["owner_closed"]
        for r in rows
    )
    p.journal.close()


def test_ordinary_mixed_session_cannot_bypass_approved_composition(tmp_path: Path) -> None:
    fake = FakeHTTPS([])
    p = compose(tmp_path, fake)
    try:
        with pytest.raises(PermissionError, match="PaidLifeSession"):
            MixedLifeSession(
                LifeConfig(seed=123, horizon=24),
                LifeLLMConfig(max_output_tokens=4096, timeout_seconds=120),
                provider=p,
            )
        assert not fake.created
    finally:
        p.journal.close()


@pytest.mark.parametrize("raw", ["{", '["x",]', "[" * 1500 + "0" + "]" * 1500, '{"id":1,"id":2}'])
def test_invalid_http_envelopes_keep_reservation_and_withhold_raw(tmp_path: Path, raw: str) -> None:
    p = compose(tmp_path, FakeHTTPS([FakeResponse(raw)]))
    try:
        with pytest.raises(PaidFailure, match="INVALID_RESPONSE"):
            p.generate(current_request())
        assert p.budget.reserved_cost == 318432000
        assert any(r["stage"] == "RESPONSE_ENVELOPE_RECEIVED" for r in p.journal.records())
        assert not any(r["stage"] == "RESPONSE_RECEIVED" for r in p.journal.records())
    finally:
        p.journal.close()


def test_finished_run_cannot_send_even_with_same_provider_object(tmp_path: Path) -> None:
    fake = FakeHTTPS([])
    p = compose(tmp_path, fake)
    p.journal.finish(False)
    try:
        with pytest.raises(PaidFailure, match="ADMISSION_MISMATCH"):
            p.generate(current_request())
        assert not fake.created
    finally:
        p.journal.close()


def test_timeout_during_credential_loading_never_starts_a_late_send(tmp_path: Path) -> None:
    clock = Clock()
    fake = FakeHTTPS([])
    p = compose(tmp_path, fake, clock)
    gate = threading.Event()
    entered = threading.Event()

    def credential() -> str:
        entered.set()
        assert gate.wait(2)
        return SECRET

    p._credential = credential
    s = PaidLifeSession(p, clock=clock)
    try:
        s.progress(1, 0)
        assert entered.wait(2)
        clock.value = 220.001
        s.decider.poll()
        gate.set()
        assert s.decider._signal.wait(2)
        s.decider.poll()
        assert not fake.created and not fake.sent and p.cost_state == "NOT_SENT"
        assert p.budget.reserved_cost == 0 and s.kernel.simulation_time == 0
    finally:
        gate.set()
        s.close()
