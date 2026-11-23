"""Transport-boundary, durable failure and independent audit checks; no real credentials."""

import http.client
import os
import shutil
import socket
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from journeymap.adapters import pickup_cue_live as wire
from journeymap.adapters.llm import Record, strict_json
from journeymap.adapters.pickup_cue_prompt import PROFILE
from journeymap.adapters.provider import ProviderFailure, ProviderRequest, RawModelResponse
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.experiments import pickup_cue_live as runner
from journeymap.experiments import pickup_cue_live_plan as plans
from journeymap.experiments.memory_horizon_audit import obj, rows
from journeymap.experiments.pickup_cue import CONDITIONS, seal
from journeymap.experiments.pickup_cue_live_audit import audit_directory, read_json
from journeymap.experiments.pickup_cue_session import PickupSession
from journeymap.experiments.pilot_journal import digest, inspect_journal


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("offline test forbids network, credentials and Providers")


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    original = os.environ.get

    def safe_get(key: str, default: str | None = None) -> str | None:
        if key == "OPENAI_API_KEY":
            raise AssertionError("real credential lookup forbidden")
        return original(key, default)

    monkeypatch.setattr(os.environ, "get", safe_get)


@pytest.fixture(scope="module")
def matrix(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[bytes], list[JsonObject]]:
    directory = tmp_path_factory.mktemp("pickup-live") / "matrix"
    payloads: list[bytes] = []
    original = wire.RecordingTransport.send
    transports: list[wire.RecordingTransport] = []

    def capture(self: wire.RecordingTransport, payload: bytes) -> RawModelResponse:
        transports.append(self)
        payloads.append(payload)
        return original(self, payload)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(wire.RecordingTransport, "send", capture)
        patch.setattr(wire.HTTPTransport, "__init__", forbidden)
        patch.setattr(socket, "create_connection", forbidden)
        plan = plans.make_plan(config=plans.mock_config())
        report = runner.execute(plan, directory)
    assert report["status"] == "INCLUDED"
    assert report["calls_consumed"] == report["behavioral_denominator"] == 40
    assert report["preparation_submissions"] == 200
    assert report["evaluation_submissions"] == 40
    assert report["task_success_count"] == 20
    assert len({id(t) for t in transports}) == 40
    assert all(len(t.payloads) == 1 for t in transports)
    return directory, payloads, [read_json(directory / f"a{i:03}.json") for i in range(1, 41)]


def test_transport_bytes_and_independent_pair_expectations(
    matrix: tuple[Path, list[bytes], list[JsonObject]],
) -> None:
    _, actual_payloads, records = matrix
    pairs: dict[tuple[int, str], dict[str, JsonObject]] = {}
    for sent, record in zip(actual_payloads, records, strict=True):
        result = obj(record["result"])
        outgoing = obj(result["wire"])
        assert sent == cast(str, outgoing["canonical_utf8"]).encode("utf-8")
        assert sha256(sent).hexdigest() == outgoing["sha256"]
        assert len(sent) == outgoing["bytes"]
        body = obj(strict_json(sent.decode()))
        assert body == outgoing["body"]
        assert set(body) == {"input", "model", "store", "text", "reasoning", "max_output_tokens"}
        assert body["store"] is False
        schema = obj(obj(obj(body["text"])["format"])["schema"])
        assert schema["type"] == "object" and "anyOf" not in schema
        assert schema["additionalProperties"] is False
        prompt = cast(str, body["input"])
        assert prompt.startswith(PROFILE.instructions)
        inputs = obj(strict_json(prompt.split("\nINPUT_JSON\n", 1)[1]))
        text = canonical_json(inputs)
        for key in (
            "target",
            "condition",
            "simulation_time",
            "checkpoint",
            "case_id",
            "seed",
            "trace_id",
            "event_trace_id",
            "observation_id",
            "run_id",
            "k",
            "d",
        ):
            assert f'"{key}":' not in text
        assert result["knowledge"] == result["initial_knowledge"] == []
        assert obj(result["checkpoint"])["tick"] == 7
        assert len(rows(result["prefix"])) == 5
        allocation = obj(record["allocation"])
        pair = pairs.setdefault(
            (cast(int, allocation["d"]), cast(str, allocation["condition"])), {}
        )
        pair[cast(str, allocation["target"])] = body
    cue_pairs = no_cue_pairs = 0
    for (d, condition), pair in pairs.items():
        expected_cue = condition == "full-prefix" or (
            condition.startswith("recency") and int(condition[-1]) >= d
        )
        if not expected_cue:
            assert pair["inn"] == pair["bakery"]
            no_cue_pairs += 1
            continue
        cue_pairs += 1
        left, right = Record.capture(pair["inn"]).data, pair["bakery"]
        prompt = cast(str, left["input"])
        needle = '"pickup_location":"inn"'
        assert prompt.count(needle) == 1
        left["input"] = prompt.replace(needle, '"pickup_location":"bakery"')
        assert left == right
    assert (cue_pairs, no_cue_pairs) == (10, 10)
    assert len(pairs) == 4 * len(CONDITIONS)


def test_audit_has_no_provider_transport_or_key_dependency(
    matrix: tuple[Path, list[bytes], list[JsonObject]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for cls in (wire.PickupProvider, wire.HTTPTransport, wire.RecordingTransport):
        monkeypatch.setattr(cls, "__init__", forbidden)
    monkeypatch.setattr(plans, "source_identity", forbidden)
    monkeypatch.setattr(plans, "runtime_identity", forbidden)
    report = audit_directory(matrix[0])
    assert report["status"] == "INCLUDED"
    assert report["mode"] == "recording-mock"
    assert all(
        r["engine_replay"] and r["input_reconstruction"] for r in rows(report["allocations"])
    )


@pytest.mark.parametrize(
    "issue",
    [
        "flag",
        "draft",
        "digest",
        "tamper",
        "source",
        "runtime",
        "existing",
        "budget",
        "duplicate",
        "order",
        "branch-missing",
        "mock-live",
        "metadata",
    ],
)
def test_preflight_failure_before_transport_or_key(
    issue: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = plans.mock_config()
    config.update(
        {
            "model": "reviewed-test-only",
            "returned_models": ["reviewed-test-only"],
            "model_support_review": "https://developers.openai.com/api/docs/models/",
            "budget_review": "test-only authorization fixture",
        }
    )
    plan = plans.make_plan(config=config, mode="live")
    output = tmp_path / "output"
    enable = issue != "flag"
    if issue == "draft":
        plan = plans.make_plan(mode="live")
    elif issue == "source":
        obj(plan["source"])["working_source_sha256"] = "0" * 64
    elif issue == "runtime":
        obj(plan["runtime"])["version"] = [3, 12, 1]
    elif issue == "existing":
        output.mkdir()
        (output / "keep").write_text("original")
    elif issue == "budget":
        plan["call_cap"] = 39
    elif issue == "duplicate":
        rows(plan["allocations"])[1].update(rows(plan["allocations"])[0])
    elif issue == "order":
        cast(list[JsonValue], plan["allocations"]).reverse()
    elif issue == "branch-missing":
        del obj(plan["source"])["branch"]
    elif issue == "mock-live":
        plan["config"] = plans.mock_config()
    elif issue == "metadata":
        obj(plan["config"])["metadata"] = {"target": "inn"}
    plans.seal_plan(plan)
    approved = cast(str, plan["plan_sha256"])
    if issue == "digest":
        approved = "0" * 64
    if issue == "tamper":
        plan["engine_seed"] = 12
    monkeypatch.setattr(runner, "HTTPTransport", forbidden)
    monkeypatch.setattr(runner, "RecordingTransport", forbidden)
    with pytest.raises(ValueError):
        runner.execute(plan, output, live=True, enable_live=enable, approved_plan_sha256=approved)
    assert output.exists() == (issue == "existing")
    if issue == "existing":
        assert (output / "keep").read_text() == "original"


@pytest.mark.parametrize(
    ("kind", "text", "category", "submitted", "failure"),
    [
        ("text", '{"action_type":"WAIT","payload":{"duration":1}}', "BEHAVIORAL", True, None),
        (
            "text",
            '{"action_type":"MOVE","payload":{"route_id":"bad-route"}}',
            "BEHAVIORAL",
            True,
            None,
        ),
        ("refusal", "declined", "BEHAVIORAL", False, "REFUSAL"),
        ("incomplete", "", "BEHAVIORAL", False, "INCOMPLETE"),
        ("text", "not JSON", "BEHAVIORAL", False, "INVALID_OUTPUT"),
        (
            "text",
            '{"action_type":"WAIT","payload":{"duration":true}}',
            "BEHAVIORAL",
            False,
            "INVALID_OUTPUT",
        ),
        (
            "text",
            '{"action_type":"MOVE","payload":{"duration":1}}',
            "BEHAVIORAL",
            False,
            "INVALID_OUTPUT",
        ),
        ("text", '{"action_type":"BUY","payload":{}}', "BEHAVIORAL", False, "INVALID_OUTPUT"),
        ("timeout", "", "INFRASTRUCTURE", False, "TRANSPORT_ERROR"),
        ("transport-error", "", "INFRASTRUCTURE", False, "TRANSPORT_ERROR"),
        ("model-mismatch", "", "INTEGRITY_EXCLUSION", False, "RESPONSE_IDENTITY_MISMATCH"),
    ],
)
def test_response_and_failure_accounting(
    kind: str,
    text: str,
    category: str,
    submitted: bool,
    failure: str | None,
    tmp_path: Path,
) -> None:
    plan = plans.make_plan(config=plans.mock_config(), allocation_count=2, call_cap=2)
    directory = tmp_path / "run"
    report = runner.execute(plan, directory, outcome=wire.MockOutcome(kind, text))
    expected_calls = 2 if category == "BEHAVIORAL" else 1
    assert report["calls_consumed"] == expected_calls
    assert len(rows(report["allocations"])) == 2
    result = obj(read_json(directory / "a001.json")["result"])
    assert result["category"] == category
    assert obj(result["decision"])["engine_submitted"] is submitted
    assert obj(result["decision"])["failure"] == failure
    assert obj(result["evaluation"])["behavioral_denominator"] == (category == "BEHAVIORAL")
    assert obj(result["evaluation"])["task_success"] == (
        False if category == "BEHAVIORAL" else None
    )
    if category != "BEHAVIORAL":
        assert rows(report["allocations"])[1]["category"] == "NOT_ATTEMPTED"


@pytest.mark.parametrize("boundary", ["intent", "response", "export", "result", "end"])
def test_recording_fsync_failure_stops_further_calls(
    boundary: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = plans.make_plan(config=plans.mock_config(), allocation_count=2, call_cap=2)
    fsync_count = calls = 0
    fsync = os.fsync
    send = wire.RecordingTransport.send
    failure_index = {"intent": 4, "response": 5, "export": 6, "result": 7, "end": 13}[boundary]

    def fail_fsync(fd: int) -> None:
        nonlocal fsync_count
        fsync_count += 1
        if fsync_count == failure_index:
            raise OSError("sensitive fixture exception")
        fsync(fd)

    def count_send(self: wire.RecordingTransport, payload: bytes) -> RawModelResponse:
        nonlocal calls
        calls += 1
        return send(self, payload)

    monkeypatch.setattr(os, "fsync", fail_fsync)
    monkeypatch.setattr(wire.RecordingTransport, "send", count_send)
    directory = tmp_path / boundary
    execution = runner.execute(plan, directory)
    assert execution["status"] == "INCOMPLETE"
    assert execution["execution_failure"] == "RECORDING_ERROR"
    assert calls == (0 if boundary == "intent" else 2 if boundary == "end" else 1)
    # fsync failure is not proof that readable bytes reached stable storage.
    data = (directory / "attempts.jsonl").read_text()
    assert "sensitive fixture exception" not in data
    if boundary in ("intent", "response", "export"):
        report = audit_directory(directory)
        assert report["status"] == "INCOMPLETE"
        assert report["calls_consumed"] == 1
        assert report["unknown"] == 1


def test_crash_intent_consumes_budget_and_never_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = plans.make_plan(config=plans.mock_config(), allocation_count=2, call_cap=2)
    directory = tmp_path / "crash"

    def crash(self: wire.RecordingTransport, payload: bytes) -> RawModelResponse:
        raise KeyboardInterrupt

    monkeypatch.setattr(wire.RecordingTransport, "send", crash)
    with pytest.raises(KeyboardInterrupt):
        runner.execute(plan, directory)
    report = audit_directory(directory)
    assert report["status"] == "INCOMPLETE" and report["calls_consumed"] == 1
    assert report["unknown"] == 1 and report["behavioral_denominator"] == 0
    assert rows(report["allocations"])[0]["category"] == "UNKNOWN_RESPONSE"
    with pytest.raises(ValueError):
        runner.execute(plan, directory)
    with (directory / "attempts.jsonl").open("ab") as file:
        file.write(b'{"partial":')
    report = audit_directory(directory)
    assert report["journal_status"] == "TORN_TAIL" and report["calls_consumed"] == 1


def reseal_journal(path: Path, stream: list[JsonObject]) -> None:
    previous = "0" * 64
    lines: list[str] = []
    for sequence, row in enumerate(stream, 1):
        row.update({"previous_sha256": previous, "sequence": sequence})
        row.pop("sha256", None)
        previous = digest(row)
        lines.append(Record.capture({**row, "sha256": previous}).serialized)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    "field", ["body", "response", "success", "plan", "allocation", "mode", "drop"]
)
def test_resealed_contradictions_rejected(field: str, tmp_path: Path) -> None:
    plan = plans.make_plan(config=plans.mock_config(), allocation_count=1, call_cap=1)
    directory = tmp_path / field
    assert runner.execute(plan, directory)["status"] == "INCLUDED"
    path = directory / "a001.json"
    record = read_json(path)
    result = obj(record["result"])
    journal_path = directory / "attempts.jsonl"
    stream = inspect_journal(journal_path).rows
    if field == "body":
        outgoing = obj(result["wire"])
        obj(outgoing["body"])["metadata"] = {"target": "inn"}
        outgoing["canonical_utf8"] = canonical_json(outgoing["body"])
        payload = cast(str, outgoing["canonical_utf8"]).encode()
        outgoing.update({"bytes": len(payload), "sha256": sha256(payload).hexdigest()})
        obj(obj(stream[2]["data"])["prepared"])["wire"] = outgoing
    elif field == "response":
        obj(obj(result["decision"])["raw_response"])["text"] = "different output"
        obj(stream[3]["data"])["raw_response"] = obj(result["decision"])["raw_response"]
    elif field == "success":
        obj(result["evaluation"])["task_success"] = True
    elif field == "plan":
        plan["engine_seed"] = 900
        plans.seal_plan(plan)
        (directory / "plan.json").write_text(Record.capture(plan).serialized)
        obj(stream[0]["data"])["plan_sha256"] = plan["plan_sha256"]
        record["plan_sha256"] = plan["plan_sha256"]
    elif field == "allocation":
        obj(record["allocation"])["target"] = "bakery"
    elif field == "mode":
        record["mode"] = "live"
        obj(stream[0]["data"])["mode"] = "live"
    elif field == "drop":
        path.unlink()
    seal(record)
    if field != "drop":
        path.write_text(Record.capture(record).serialized)
    obj(stream[4]["data"])["record_sha256"] = record["record_sha256"]
    reseal_journal(journal_path, stream)
    report = audit_directory(directory)
    assert report["status"] == "EXCLUDED"
    assert report["behavioral_denominator"] == 0


@pytest.mark.parametrize("branch", ["codex/named", "", "   "])
def test_provenance_named_and_detached(branch: str, monkeypatch: pytest.MonkeyPatch) -> None:
    original = plans.source_identity()

    def git(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, stdout=branch)

    monkeypatch.setattr(
        plans, "code_identity", lambda: {k: v for k, v in original.items() if k != "branch"}
    )
    monkeypatch.setattr(subprocess, "run", git)
    source = plans.source_identity()
    assert source["branch"] == (branch.strip() or None)
    plan = plans.make_plan(config=plans.mock_config(), allocation_count=1, call_cap=1)
    plans.validate_plan(plan, execution=True)


FAKE_KEY = "FAKE-KEY-OFFLINE-ONLY-DO-NOT-USE"


@pytest.mark.parametrize(
    "outcome", ["success", "timeout", "exception", "http", "large", "echo", "close"]
)
def test_http_boundary_with_fake_key_only(
    outcome: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config = plans.mock_config()
    config["max_response_bytes"] = 1024
    seen: list[bytes] = []
    response_document: JsonObject = {
        "id": "resp-fixture",
        "model": "pickup-recording-mock",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": (
                            FAKE_KEY
                            if outcome == "echo"
                            else '{"action_type":"WAIT","payload":{"duration":1}}'
                        ),
                    }
                ],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    class Response:
        status = 429 if outcome == "http" else 200

        def read(self, limit: int) -> bytes:
            assert limit == 1025
            return (
                b"x" * limit if outcome == "large" else canonical_json(response_document).encode()
            )

    class Connection:
        def __init__(self, host: str, *, timeout: float) -> None:
            assert host == "api.openai.com" and timeout == 20

        def request(self, method: str, path: str, *, body: bytes, headers: dict[str, str]) -> None:
            assert method == "POST" and path == "/v1/responses"
            assert headers == {
                "Authorization": f"Bearer {FAKE_KEY}",
                "Content-Type": "application/json",
            }
            seen.append(body)
            if outcome == "timeout":
                raise TimeoutError(FAKE_KEY)
            if outcome == "exception":
                raise RuntimeError(FAKE_KEY)

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            if outcome == "close":
                raise RuntimeError(FAKE_KEY)

    monkeypatch.setattr(
        os.environ,
        "get",
        lambda key, default=None: FAKE_KEY if key == "OPENAI_API_KEY" else default,
    )
    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)
    transport = wire.HTTPTransport(config)
    if outcome in ("success", "close"):
        raw = transport.send(b'{"fixture":true}')
        assert raw.metadata["provider"] == "openai"
        assert raw.metadata["response_id"] == "resp-fixture"
        assert FAKE_KEY not in Record.capture(raw.metadata).serialized + raw.text
    else:
        with pytest.raises(ProviderFailure) as error:
            transport.send(b'{"fixture":true}')
        assert FAKE_KEY not in str(error.value)
        assert (
            error.value.kind
            == {
                "timeout": "TIMEOUT",
                "exception": "TRANSPORT_ERROR",
                "http": "HTTP_ERROR",
                "large": "RESPONSE_TOO_LARGE",
                "echo": "SECRET_ECHO",
            }[outcome]
        )
    assert seen == [b'{"fixture":true}']
    with pytest.raises(ValueError):
        transport.send(b"repeat")


def test_wire_rejects_extra_parameters_and_changed_prompt(tmp_path: Path) -> None:
    config = plans.mock_config()
    plan = plans.make_plan(config=config, allocation_count=1, call_cap=1)
    session = PickupSession(rows(plan["allocations"])[0], 42, config)
    try:
        session.prepare()
        request = session.request
        for parameters, prompt in (
            ({**request.parameters, "metadata": {}}, request.prompt),
            (request.parameters, request.prompt.replace("Choose one", "Choose two")),
        ):
            changed = ProviderRequest(
                prompt,
                request.prompt_version,
                request.model,
                request.configuration_version,
                parameters,
            )
            with pytest.raises(ValueError):
                wire.request_body(changed, config)
    finally:
        session.close()


def test_cli_default_is_help_and_draft_is_unresolved(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from journeymap.examples.pickup_cue_live import main

    monkeypatch.setattr("sys.argv", ["pickup_cue_live"])
    monkeypatch.setattr(runner, "HTTPTransport", forbidden)
    assert main() == 0
    assert "--help" in capsys.readouterr().out
    plan = plans.make_plan()
    assert plan["state"] == "DRAFT"
    assert plan["mode"] == "recording-mock"
    assert obj(plan["config"])["model"] is None
    assert obj(plan["config"])["max_output_tokens"] is None
    assert len(rows(plan["allocations"])) == 40


@pytest.mark.parametrize("outcome", ["success", "timeout", "transport-error", "intent-crash"])
def test_live_branch_offline_http_double_and_sanitized_failure(
    outcome: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = plans.mock_config()
    config.update(
        {
            "model": "reviewed-test-only",
            "returned_models": ["reviewed-test-only"],
            "model_support_review": "https://developers.openai.com/api/docs/models/",
            "budget_review": "test fixture only, no network",
        }
    )
    plan = plans.make_plan(config=config, mode="live", allocation_count=2, call_cap=2)
    sent: list[bytes] = []

    class Response:
        status = 200

        def read(self, limit: int) -> bytes:
            return canonical_json(
                {
                    "id": "resp-offline-fixture",
                    "model": "reviewed-test-only",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": '{"action_type":"WAIT","payload":{"duration":1}}',
                                }
                            ],
                        }
                    ],
                }
            ).encode()

    class Connection:
        def __init__(self, host: str, *, timeout: float) -> None:
            assert host == "api.openai.com"

        def request(self, method: str, path: str, *, body: bytes, headers: dict[str, str]) -> None:
            sent.append(body)
            assert headers["Authorization"] == f"Bearer {FAKE_KEY}"
            if outcome == "timeout":
                raise TimeoutError(FAKE_KEY)
            if outcome == "transport-error":
                raise RuntimeError(FAKE_KEY)
            if outcome == "intent-crash":
                raise KeyboardInterrupt

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            pass

    original = os.environ.get
    monkeypatch.setattr(
        os.environ,
        "get",
        lambda key, default=None: FAKE_KEY if key == "OPENAI_API_KEY" else original(key, default),
    )
    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)
    directory = tmp_path / "simulated-live-transport-failure"
    if outcome == "intent-crash":
        with pytest.raises(KeyboardInterrupt):
            runner.execute(
                plan,
                directory,
                live=True,
                enable_live=True,
                approved_plan_sha256=cast(str, plan["plan_sha256"]),
            )
        report = audit_directory(directory, expected_plan_sha256=cast(str, plan["plan_sha256"]))
        assert report["mode"] == "live" and report["status"] == "INCOMPLETE"
        assert report["calls_consumed"] == report["unknown"] == 1
        assert report["behavioral_denominator"] == report["integrity_exclusions"] == 0
        assert all(FAKE_KEY not in p.read_text() for p in directory.iterdir())
        return
    report = runner.execute(
        plan,
        directory,
        live=True,
        enable_live=True,
        approved_plan_sha256=cast(str, plan["plan_sha256"]),
    )
    count = 2 if outcome == "success" else 1
    assert len(sent) == report["calls_consumed"] == count
    assert report["infrastructure"] == (0 if outcome == "success" else 1)
    assert report["behavioral_denominator"] == (2 if outcome == "success" else 0)
    assert report["status"] == "INCLUDED" and report["mode"] == "live"
    result = obj(read_json(directory / "a001.json")["result"])
    assert sent[0] == cast(str, obj(result["wire"])["canonical_utf8"]).encode()
    if outcome == "timeout":
        assert obj(obj(result["decision"])["provider_failure"])["kind"] == "TIMEOUT"
    assert all(FAKE_KEY not in p.read_text() for p in directory.iterdir())
    assert FAKE_KEY not in canonical_json(report)


def test_preparation_failure_has_no_model_attempt_and_keeps_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = plans.make_plan(config=plans.mock_config(), allocation_count=2, call_cap=2)

    def fail(self: PickupSession) -> JsonObject:
        raise RuntimeError("sensitive exception")

    monkeypatch.setattr(PickupSession, "prepare", fail)
    monkeypatch.setattr(runner, "RecordingTransport", forbidden)
    directory = tmp_path / "preparation-failure"
    report = runner.execute(plan, directory)
    assert report["calls_consumed"] == 0
    assert report["infrastructure"] == 1
    assert rows(report["allocations"])[1]["category"] == "NOT_ATTEMPTED"
    row = inspect_journal(directory / "attempts.jsonl").rows[2]
    assert row["kind"] == "PREPARATION_FAILED"
    assert isinstance(obj(row["data"])["partial"], dict)
    assert "sensitive exception" not in (directory / "attempts.jsonl").read_text()


def reviewed_config() -> JsonObject:
    config = plans.mock_config()
    config.update(
        {
            "model": "reviewed-test-only",
            "returned_models": ["reviewed-test-only"],
            "model_support_review": "https://developers.openai.com/api/docs/models/",
            "budget_review": "offline test fixture, not human execution approval",
        }
    )
    return config


@pytest.mark.parametrize("config_kind", ["mock-only", "reviewed-model"])
@pytest.mark.parametrize("failure", ["timeout", "transport-error", "preparation", "intent"])
def test_no_response_mode_reclassification_rejected_with_original_plan_digest(
    config_kind: str,
    failure: str,
    tmp_path: Path,
) -> None:
    config = plans.mock_config() if config_kind == "mock-only" else reviewed_config()
    plan = plans.make_plan(config=config, allocation_count=1, call_cap=1)
    original, altered = tmp_path / "original", tmp_path / "altered"
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runner, "HTTPTransport", forbidden)
        if failure == "preparation":
            patch.setattr(PickupSession, "prepare", forbidden)
        if failure == "intent":

            def crash(self: wire.RecordingTransport, payload: bytes) -> RawModelResponse:
                raise KeyboardInterrupt

            patch.setattr(wire.RecordingTransport, "send", crash)
            with pytest.raises(KeyboardInterrupt):
                runner.execute(plan, original)
        else:
            runner.execute(
                plan,
                original,
                outcome=wire.MockOutcome(
                    failure if failure in ("timeout", "transport-error") else "notice"
                ),
            )
    approved = cast(str, plan["plan_sha256"])
    before = audit_directory(original, expected_plan_sha256=approved)
    assert before["status"] == ("INCOMPLETE" if failure == "intent" else "INCLUDED")
    assert before["mode"] == "recording-mock"
    assert before["calls_consumed"] == (0 if failure == "preparation" else 1)
    assert before["behavioral_denominator"] == 0
    hashes = {p.name: sha256(p.read_bytes()).hexdigest() for p in original.iterdir()}
    shutil.copytree(original, altered)
    stream = inspect_journal(altered / "attempts.jsonl").rows
    obj(stream[0]["data"])["mode"] = "live"
    export = altered / "a001.json"
    if export.exists():
        record = read_json(export)
        assert obj(record["result"])["category"] == "INFRASTRUCTURE"
        assert obj(obj(record["result"])["decision"])["raw_response"] is None
        record["mode"] = "live"
        seal(record)
        export.write_text(Record.capture(record).serialized, encoding="utf-8")
        obj(next(r for r in stream if r["kind"] == "RESULT")["data"])["record_sha256"] = record[
            "record_sha256"
        ]
    reseal_journal(altered / "attempts.jsonl", stream)
    assert read_json(altered / "plan.json")["plan_sha256"] == approved
    after = audit_directory(altered, expected_plan_sha256=approved)
    assert after["status"] == "EXCLUDED" and after["behavioral_denominator"] == 0
    assert {p.name: sha256(p.read_bytes()).hexdigest() for p in original.iterdir()} == hashes


@pytest.mark.parametrize("planned", ["recording-mock", "live"])
def test_runner_mode_mismatch_before_file_key_transport(
    planned: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = plans.make_plan(config=reviewed_config(), mode=planned, allocation_count=1, call_cap=1)
    live = planned != "live"
    monkeypatch.setattr(runner, "HTTPTransport", forbidden)
    monkeypatch.setattr(runner, "RecordingTransport", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    with pytest.raises(ValueError, match="runner/planned mode mismatch"):
        runner.execute(
            plan,
            tmp_path / "output",
            live=live,
            enable_live=live,
            approved_plan_sha256=cast(str, plan["plan_sha256"]) if live else None,
        )
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize(
    "contradiction", ["mock-model", "mock-returned", "unreviewed", "mock-budget"]
)
def test_audit_pure_live_config_checks_without_execution_checks(
    contradiction: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = plans.make_plan(config=reviewed_config(), allocation_count=1, call_cap=1)
    directory = tmp_path / "config"
    runner.execute(plan, directory, outcome=wire.MockOutcome("timeout"))
    plan["mode"] = "live"
    config = obj(plan["config"])
    if contradiction == "mock-model":
        config["model"] = "pickup-recording-mock"
    elif contradiction == "mock-returned":
        config["returned_models"] = ["pickup-recording-mock"]
    elif contradiction == "unreviewed":
        config["model_support_review"] = "OFFLINE_MOCK_ONLY"
    else:
        config["budget_review"] = "NO_NETWORK_NO_COST"
    plans.seal_plan(plan)
    (directory / "plan.json").write_text(Record.capture(plan).serialized, encoding="utf-8")
    stream = inspect_journal(directory / "attempts.jsonl").rows
    obj(stream[0]["data"]).update({"mode": "live", "plan_sha256": plan["plan_sha256"]})
    reseal_journal(directory / "attempts.jsonl", stream)
    monkeypatch.setattr(plans, "source_identity", forbidden)
    monkeypatch.setattr(plans, "runtime_identity", forbidden)
    assert audit_directory(directory)["status"] == "EXCLUDED"


def test_planned_mode_changes_digest_but_not_model_input() -> None:
    config = reviewed_config()
    mock = plans.make_plan(config=config, allocation_count=1, call_cap=1)
    live = plans.make_plan(config=config, mode="live", allocation_count=1, call_cap=1)
    assert mock["plan_sha256"] != live["plan_sha256"]
    mock_session = PickupSession(rows(mock["allocations"])[0], 42, config)
    live_session = PickupSession(rows(live["allocations"])[0], 42, config)
    try:
        assert mock_session.prepare()["wire"] == live_session.prepare()["wire"]
        assert '"mode":' not in mock_session.request.prompt
    finally:
        mock_session.close()
        live_session.close()


@pytest.mark.parametrize("mode", [None, "live"])
def test_cli_plan_explicit_mode_with_reviewed_config(
    mode: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from journeymap.examples.pickup_cue_live import main

    configuration, output = tmp_path / "configuration.json", tmp_path / "plan.json"
    configuration.write_text(Record.capture(reviewed_config()).serialized, encoding="utf-8")
    argv = ["pickup_cue_live", "plan", "--config", str(configuration), "--output", str(output)]
    if mode:
        argv.extend(["--mode", mode])
    monkeypatch.setattr("sys.argv", argv)
    assert main() == 0
    assert read_json(output)["mode"] == (mode or "recording-mock")
    capsys.readouterr()


def test_legacy_plan_read_only_never_promotes_unbound_mode(tmp_path: Path) -> None:
    plan = plans.make_plan(config=plans.mock_config(), allocation_count=1, call_cap=1)
    directory = tmp_path / "legacy"
    runner.execute(plan, directory, outcome=wire.MockOutcome("timeout"))
    plan.pop("mode")
    for key, value in (
        ("plan_version", plans.LEGACY_PLAN_VERSION),
        ("protocol_version", "pickup-cue-live-execution-1"),
        ("export_version", "pickup-cue-live-export-1"),
        ("audit_version", "pickup-cue-live-audit-1"),
    ):
        plan[key] = value
    plans.seal_plan(plan)
    (directory / "plan.json").write_text(Record.capture(plan).serialized, encoding="utf-8")
    hashes = {p.name: sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
    report = audit_directory(directory, expected_plan_sha256=cast(str, plan["plan_sha256"]))
    assert report["status"] == "EXCLUDED" and report["reason"] == "LEGACY_PLAN_MODE_UNBOUND"
    assert report["legacy_read_only"] is True and report["execution_mode_verified"] is False
    assert report["mode"] is None and report["behavioral_denominator"] == 0
    assert {p.name: sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()} == hashes
