"""Sequential contract, memory visibility, durable boundaries and adversarial audits."""

import json
import os
import shutil
import socket
from pathlib import Path
from typing import cast

import pytest

from journeymap.adapters import pickup_sequential as wire
from journeymap.adapters.llm import Record
from journeymap.adapters.pickup_cue_live import HTTPTransport
from journeymap.adapters.provider import RawModelResponse
from journeymap.core.canonical import JsonObject, canonical_json
from journeymap.experiments import pickup_sequential as runner
from journeymap.experiments.memory_horizon_audit import obj, rows
from journeymap.experiments.pickup_cue_live_audit import read_json
from journeymap.experiments.pickup_sequential_audit import audit_directory
from journeymap.experiments.pickup_sequential_plan import allocations, make_plan, validate_plan
from journeymap.experiments.pickup_sequential_session import SequentialSession
from journeymap.experiments.pilot_journal import AttemptJournal, digest, inspect_journal


def forbidden(*args: object, **kwargs: object) -> None:
    raise AssertionError("no providers, network or credential reads")


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(HTTPTransport, "__init__", forbidden)
    original = os.environ.get

    def no_key(key: str, default: str | None = None) -> str | None:
        if key == "OPENAI_API_KEY":
            raise AssertionError("no key lookup")
        return original(key, default)

    monkeypatch.setattr(os.environ, "get", no_key)


def action(route: str) -> str:
    return canonical_json({"action_type": "MOVE", "payload": {"route_id": route}})


WAIT = '{"action_type":"WAIT","payload":{"duration":1}}'
WELL = action("village-square-to-well")
RETURN = action("well-to-village-square")
BAKERY = action("village-square-to-bakery")
INN = action("village-square-to-inn")


def run(tmp_path: Path, outcomes: tuple[str, ...]) -> tuple[Path, JsonObject, JsonObject]:
    plan = make_plan(allocation_count=1)
    directory = tmp_path / "run"
    report = runner.execute(plan, directory, scripts=(wire.MockScript(outcomes),))
    return directory, report, obj(read_json(directory / "a001.json")["result"])


@pytest.fixture(scope="module")
def matrix(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, JsonObject]:
    directory = tmp_path_factory.mktemp("sequential") / "matrix"
    payloads: list[bytes] = []
    transports: list[wire.RecordingTransport] = []
    original_send = wire.RecordingTransport.send

    def capture(self: wire.RecordingTransport, payload: bytes) -> RawModelResponse:
        payloads.append(payload)
        transports.append(self)
        return original_send(self, payload)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(wire.RecordingTransport, "send", capture)
        patch.setattr(HTTPTransport, "__init__", forbidden)
        report = runner.execute(make_plan(), directory)
    assert report["status"] == "INCLUDED"
    assert report["calls_consumed"] == 104
    assert report["immediate_success_count"] == 20
    assert report["eventual_success_count"] == 32
    assert report["recovered_success_count"] == 12
    outgoing = [
        obj(obj(r["data"])["step"])["wire"]
        for r in inspect_journal(directory / "attempts.jsonl").rows
        if r["kind"] == "INTENT"
    ]
    assert payloads == [cast(str, obj(w)["canonical_utf8"]).encode() for w in outgoing]
    assert len(transports) == len({id(t) for t in transports}) == 104
    return directory, report


def test_matrix_and_first_input_equivalence(matrix: tuple[Path, JsonObject]) -> None:
    from journeymap.experiments.pickup_cue_session import PickupSession

    counts = dict.fromkeys(
        ("no-event-memory", "recency-k1", "recency-k2", "recency-k3", "full-history"), 0
    )
    directory, report = matrix
    assert report["planned_denominator"] == report["attempted_denominator"] == 40
    for allocation in rows(read_json(directory / "plan.json")["allocations"]):
        result = obj(read_json(directory / f"{allocation['allocation_id']}.json")["result"])
        steps = rows(result["steps"])
        first = steps[0]
        condition = cast(str, allocation["condition"])
        counts[condition] += int(obj(first["cue"])["cue_memory_present"] is True)
        previous = {
            **allocation,
            "condition": "full-prefix" if condition == "full-history" else condition,
        }
        session = PickupSession(previous, 42, obj(read_json(directory / "plan.json")["config"]))
        try:
            old = session.prepare()
            assert (
                obj(first["input"])["model_visible_input"]
                == obj(obj(old["decision"])["input"])["model_visible_input"]
            )
            assert obj(first["wire"])["canonical_utf8"] != obj(old["wire"])["canonical_utf8"]
        finally:
            session.close()
        assert result["knowledge"] == []
        assert len(steps) <= 6
        for step in steps:
            text = canonical_json(obj(step["input"])["model_visible_input"])
            for field in (
                "target",
                "condition",
                "step_number",
                "simulation_time",
                "run_id",
                "checkpoint",
                "seed",
                "d",
            ):
                assert f'"{field}":' not in text
        assert obj(first["cue"])["cue_current_present"] is False
        visible = canonical_json(obj(first["input"])["model_visible_input"])
        for field in (
            "target",
            "condition",
            "step_number",
            "simulation_time",
            "run_id",
            "checkpoint",
            "seed",
            "d",
        ):
            assert f'"{field}":' not in visible
    assert list(counts.values()) == [0, 2, 4, 6, 8]


def test_immediate_stops_without_extra_calls(tmp_path: Path) -> None:
    _, report, result = run(tmp_path, (BAKERY, "TIMEOUT"))
    m = obj(result["metrics"])
    assert report["calls_consumed"] == 1
    assert m["immediate_success"] is m["eventual_success"] is True
    assert m["recovered_success"] is False
    assert (m["steps_to_success"], m["ticks_to_success"], m["extra_actions"], m["extra_ticks"]) == (
        1,
        2,
        0,
        0,
    )


@pytest.mark.parametrize(
    "outcomes,extra",
    [
        ((WELL, RETURN, BAKERY), (2, 4)),
        ((INN, action("inn-to-village-square"), BAKERY), (2, 4)),
        ((WAIT, BAKERY), (1, 1)),
    ],
)
def test_recovery_preserves_first_score(
    tmp_path: Path, outcomes: tuple[str, ...], extra: tuple[int, int]
) -> None:
    _, _, result = run(tmp_path, outcomes)
    m = obj(result["metrics"])
    assert m["immediate_success"] is False and m["eventual_success"] is True
    assert m["recovered_success"] is True
    assert (m["extra_actions"], m["extra_ticks"]) == extra
    if outcomes[0] == WELL:
        assert (m["recheck_count"], m["first_recheck_step"]) == (1, 2)


def test_wait_exhaustion_and_null_success_cost(tmp_path: Path) -> None:
    _, report, result = run(tmp_path, (WAIT,) * 6)
    m = obj(result["metrics"])
    assert report["calls_consumed"] == 6 and report["decision_limit"] == 1
    assert m["immediate_success"] is m["eventual_success"] is False
    assert (
        m["steps_to_success"]
        is m["ticks_to_success"]
        is m["extra_actions"]
        is m["extra_ticks"]
        is None
    )
    assert (m["submitted_actions"], m["wait_count"], m["elapsed_ticks"]) == (6, 6, 6)


def test_normal_engine_rejected_progresses_atomically(tmp_path: Path) -> None:
    _, _, result = run(tmp_path, (action("well-to-village-square"), BAKERY))
    steps = rows(result["steps"])
    assert steps[0]["engine_status"] == "REJECTED"
    assert steps[1]["engine_status"] == "SUCCEEDED"
    assert obj(result["metrics"])["elapsed_ticks"] == 2
    assert obj(result["metrics"])["extra_actions"] == 1


@pytest.mark.parametrize(
    "outcome",
    ["REFUSAL", "INCOMPLETE", "not json", '{"action_type":"WAIT","payload":{"duration":2}}'],
)
def test_response_failure_terminates_case(tmp_path: Path, outcome: str) -> None:
    _, report, result = run(tmp_path, (outcome, BAKERY))
    assert report["calls_consumed"] == 1 and report["response_failure"] == 1
    assert obj(result["metrics"])["eventual_success"] is False
    assert rows(result["steps"])[0]["engine_status"] == "NOT_SUBMITTED"


@pytest.mark.parametrize("condition", ["no-event-memory", "recency-k1", "full-history"])
def test_actual_recheck_current_and_memory(condition: str) -> None:
    plan = make_plan(allocation_count=1)
    allocation = rows(plan["allocations"])[0]
    allocation["condition"] = condition
    session = SequentialSession(allocation, 42, obj(plan["config"]))
    try:
        session.prepare()
        cues = []
        for text in (WELL, RETURN, WAIT):
            step = session.prepare_step()
            cues.append(obj(step["cue"]))
            response = wire.SequentialProvider(
                wire.RecordingTransport(text), obj(plan["config"])
            ).generate(session.request)
            session.finish_step(response, None, mock=True)
        assert cues[1]["cue_current_present"] is True
        assert cues[2]["cue_current_present"] is False
        assert cues[2]["cue_memory_present"] is (condition != "no-event-memory")
        assert len(session.base.archive.traces) == 8
        assert obj(session.snapshot()["metrics"])["recheck_count"] == 1
    finally:
        session.close()


def test_repeated_history_and_conflicting_cue() -> None:
    plan = make_plan(allocation_count=1)
    session = SequentialSession(rows(plan["allocations"])[0], 42, obj(plan["config"]))
    try:
        session.prepare()
        for text in (WELL, WAIT, WAIT):
            session.prepare_step()
            response = wire.SequentialProvider(
                wire.RecordingTransport(text), obj(plan["config"])
            ).generate(session.request)
            session.finish_step(response, None, mock=True)
        step = session.prepare_step()
        assert obj(step["cue"])["cue_memory_count"] == 3
        inputs = Record.capture(obj(obj(step["input"])["model_visible_input"])).data
        observation = obj(rows(inputs["event_memory"])[-1]["observation"])
        for section in rows(obj(observation["content"])["sections"]):
            if section["contributor_id"] == "pickup_notice":
                obj(section["content"])["pickup_notice"] = {"pickup_location": "inn"}
        with pytest.raises(ValueError, match="conflicting"):
            wire.cue_flags(inputs)
    finally:
        session.close()


@pytest.mark.parametrize("outcome", ["TIMEOUT", "TRANSPORT_ERROR", "MODEL_MISMATCH"])
def test_midrun_error_stops_all_and_preserves_first(tmp_path: Path, outcome: str) -> None:
    plan = make_plan(allocation_count=2)
    report = runner.execute(
        plan, tmp_path / "matrix", scripts=(wire.MockScript((WELL, outcome)), wire.MockScript())
    )
    assert report["calls_consumed"] == 2 and report["not_attempted"] == 1
    first = obj(rows(report["allocations"])[0]["metrics"])
    assert first["immediate_success"] is False
    assert first["eventual_success"] is None
    assert (
        report["immediate_result_denominator"] == 1 and report["eventual_result_denominator"] == 0
    )


def test_total_call_cap_is_not_a_tick_bound(tmp_path: Path) -> None:
    report = runner.execute(
        make_plan(allocation_count=2, call_cap=1),
        tmp_path / "run",
        scripts=(wire.MockScript((WELL,)), wire.MockScript()),
    )
    assert report["calls_consumed"] == 1 and report["not_attempted"] == 1
    assert obj(rows(report["allocations"])[0]["metrics"])["eventual_success"] is None


def test_recording_failure_prefix_preserves_immediate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    append = AttemptJournal.append

    def fail(self: AttemptJournal, kind: str, data: JsonObject) -> None:
        if kind == "RESPONSE" and data.get("step_number") == 2:
            raise OSError("fixture failure")
        append(self, kind, data)

    monkeypatch.setattr(AttemptJournal, "append", fail)
    report = runner.execute(
        make_plan(allocation_count=2),
        tmp_path / "run",
        scripts=(wire.MockScript((WELL, RETURN)), wire.MockScript()),
    )
    assert report["status"] == "INCOMPLETE"
    assert report["calls_consumed"] == 2 and report["not_attempted"] == 1
    first = obj(rows(report["allocations"])[0]["metrics"])
    assert first["immediate_success"] is False and first["eventual_success"] is None
    assert first["decision_attempts"] == 2


@pytest.mark.parametrize("kind", ["step-order", "body", "cue", "summary", "cost", "mode"])
def test_resealed_tampering_rejected(tmp_path: Path, kind: str) -> None:
    source, _, _ = run(tmp_path, (WELL, RETURN, BAKERY))
    target = tmp_path / "altered"
    shutil.copytree(source, target)
    stream = inspect_journal(target / "attempts.jsonl").rows
    if kind in ("summary", "cost"):
        path = target / "a001.json"
        record = read_json(path)
        obj(obj(record["result"])["metrics"])[
            "immediate_success" if kind == "summary" else "extra_ticks"
        ] = True if kind == "summary" else 0
        record["record_sha256"] = digest({k: v for k, v in record.items() if k != "record_sha256"})
        path.write_text(Record.capture(record).serialized, encoding="utf-8")
        for row in stream:
            if row["kind"] == "CASE_END":
                obj(row["data"])["record_sha256"] = record["record_sha256"]
    elif kind == "mode":
        obj(stream[0]["data"])["mode"] = "live"
    else:
        intent = obj(next(r for r in stream if r["kind"] == "INTENT")["data"])
        if kind == "step-order":
            intent["step_number"] = 2
        elif kind == "body":
            obj(obj(intent["step"])["wire"])["body"] = {"model": "altered"}
        else:
            obj(obj(intent["step"])["cue"])["cue_current_present"] = True
    (target / "attempts.jsonl").unlink()
    journal = AttemptJournal(target / "attempts.jsonl")
    try:
        for row in stream:
            journal.append(cast(str, row["kind"]), obj(row["data"]))
    finally:
        journal.close()
    report = audit_directory(
        target, expected_plan_sha256=cast(str, read_json(source / "plan.json")["plan_sha256"])
    )
    assert report["status"] == "EXCLUDED"
    if kind in ("summary", "cost"):
        metrics = obj(rows(report["allocations"])[0]["metrics"])
        assert metrics["immediate_success"] is False
        for key in (
            "eventual_success",
            "recovered_success",
            "steps_to_success",
            "ticks_to_success",
            "extra_actions",
            "extra_ticks",
        ):
            assert metrics[key] is None


def test_broken_chain_preserves_verified_first(tmp_path: Path) -> None:
    directory, _, _ = run(tmp_path, (WELL, RETURN, BAKERY))
    payloads = (directory / "attempts.jsonl").read_bytes().splitlines(keepends=True)
    stream = inspect_journal(directory / "attempts.jsonl").rows
    index = next(
        i
        for i, row in enumerate(stream)
        if row["kind"] == "RESPONSE" and obj(row["data"]).get("step_number") == 2
    )
    broken = obj(json.loads(payloads[index]))
    broken["sha256"] = "0" * 64
    payloads[index] = (Record.capture(broken).serialized + "\n").encode()
    (directory / "attempts.jsonl").write_bytes(b"".join(payloads))
    report = audit_directory(directory)
    assert report["status"] == "EXCLUDED"
    assert report["journal_status"] == "CHAIN_BREAK"
    metrics = obj(rows(report["allocations"])[0]["metrics"])
    assert metrics["immediate_success"] is False and metrics["eventual_success"] is None
    assert metrics["decision_attempts"] == 2


def test_audit_prefix_without_providers(
    matrix: tuple[Path, JsonObject], monkeypatch: pytest.MonkeyPatch
) -> None:
    for cls in (wire.SequentialProvider, wire.RecordingTransport):
        monkeypatch.setattr(cls, "__init__", forbidden)
    import journeymap.experiments.pickup_sequential_plan as plans

    monkeypatch.setattr(plans, "source_identity", forbidden)
    monkeypatch.setattr(plans, "runtime_identity", forbidden)
    assert audit_directory(matrix[0]) == matrix[1]


def test_draft_mode_and_caps_fail_before_output(tmp_path: Path) -> None:
    config: JsonObject = {
        "provider": "openai",
        "model": "gpt-6.1-sol",
        "returned_models": ["gpt-6.1-sol"],
        "reasoning_effort": "low",
        "max_output_tokens": 2048,
        "timeout_seconds": 120,
        "max_response_bytes": 2000000,
        "model_support_review": "https://developers.openai.com/api/docs/models/gpt-6.1-sol",
        "budget_review": "PENDING_NEW_USER_APPROVAL",
    }
    draft = make_plan(config=config, mode="live")
    assert draft["state"] == "DRAFT"
    with pytest.raises(ValueError):
        runner.execute(
            draft,
            tmp_path / "draft",
            live=True,
            enable_live=True,
            approved_plan_sha256=cast(str, draft["plan_sha256"]),
        )
    assert not (tmp_path / "draft").exists()
    with pytest.raises(ValueError):
        runner.execute(make_plan(), tmp_path / "mismatch", live=True)
    with pytest.raises(ValueError):
        make_plan(call_cap=241)
    assert allocations(6, 1)[0] == {
        "allocation_id": "a001",
        "target": "bakery",
        "d": 4,
        "condition": "full-history",
    }
    validate_plan(draft)


@pytest.mark.parametrize("boundary", ["INTENT", "RESPONSE", "STEP_RESULT"])
def test_unfinished_prefix_preserves_verified_first(tmp_path: Path, boundary: str) -> None:
    directory, _, _ = run(tmp_path, (WELL, RETURN, BAKERY))
    payloads = (directory / "attempts.jsonl").read_bytes().splitlines(keepends=True)
    stream = inspect_journal(directory / "attempts.jsonl").rows
    stop = next(
        i
        for i, r in enumerate(stream)
        if r["kind"] == boundary and obj(r["data"]).get("step_number") == 2
    )
    (directory / "attempts.jsonl").write_bytes(b"".join(payloads[: stop + 1]))
    report = audit_directory(directory)
    assert report["status"] == "INCOMPLETE" and report["calls_consumed"] == 2
    m = obj(rows(report["allocations"])[0]["metrics"])
    assert m["immediate_success"] is False and m["eventual_success"] is None
    assert m["decision_attempts"] == 2 and m["first_recheck_step"] == 2


@pytest.mark.parametrize("failure_at", ["INTENT", "STEP_RESULT", "CASE_END"])
def test_durable_recording_stop_no_recall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_at: str
) -> None:
    append = AttemptJournal.append

    def fail(self: AttemptJournal, kind: str, data: JsonObject) -> None:
        if kind == failure_at:
            raise OSError("fixture recording stop")
        append(self, kind, data)

    monkeypatch.setattr(AttemptJournal, "append", fail)
    report = runner.execute(make_plan(allocation_count=2), tmp_path / "run")
    assert report["status"] == "INCOMPLETE" and report["not_attempted"] == 1
    assert report["calls_consumed"] == (0 if failure_at == "INTENT" else 1)


def test_request_parameter_drift_and_plan_memory_isolation() -> None:
    from journeymap.adapters.provider import ProviderRequest

    plan = make_plan(allocation_count=1)
    session = SequentialSession(rows(plan["allocations"])[0], 42, obj(plan["config"]))
    try:
        session.prepare()
        session.prepare_step()
        request = session.request
        changed = ProviderRequest(
            request.prompt,
            request.prompt_version,
            request.model,
            request.configuration_version,
            {"max_output_tokens": 256.0, "reasoning": {"effort": "low"}},
        )
        with pytest.raises(ValueError, match="request contract"):
            wire.wire_record(changed, obj(plan["config"]))
    finally:
        session.close()
    obj(plan["memory_semantics"])["full-history"] = "altered"
    with pytest.raises(ValueError):
        validate_plan(plan)
    assert (
        obj(make_plan()["memory_semantics"])["full-history"] == "pickup-sequential-full-history-1"
    )


def test_preparation_failure_has_no_evaluation_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from journeymap.experiments.pickup_cue_session import PickupSession

    monkeypatch.setattr(PickupSession, "prepare", forbidden)
    report = runner.execute(make_plan(allocation_count=2), tmp_path / "run")
    assert report["status"] == "INCLUDED"
    assert report["calls_consumed"] == 0 and report["not_attempted"] == 1
    assert rows(report["allocations"])[0]["category"] == "INFRASTRUCTURE_PREPARATION"


def test_sequential_live_wire_with_fake_http_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import http.client
    import json

    payloads: list[bytes] = []
    key_reads: list[str] = []

    def init(self: HTTPTransport, config: JsonObject) -> None:
        self.timeout, self.limit, self.used = 120, 2000000, False

    original_get = os.environ.get

    def fake_key(key: str, default: str | None = None) -> str | None:
        if key == "OPENAI_API_KEY":
            key_reads.append(key)
            return "unit-test-only-secret"
        return original_get(key, default)

    class Response:
        status = 200

        def read(self, limit: int) -> bytes:
            return json.dumps(
                {
                    "id": "fake-http-sequential",
                    "model": "gpt-6.1-sol",
                    "status": "completed",
                    "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": BAKERY}]}
                    ],
                }
            ).encode()

    class Connection:
        def __init__(self, host: str, *, timeout: float) -> None:
            assert host == "api.openai.com" and timeout == 120

        def request(self, method: str, path: str, *, body: bytes, headers: dict[str, str]) -> None:
            assert method == "POST" and path == "/v1/responses"
            payloads.append(body)

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            pass

    monkeypatch.setattr(HTTPTransport, "__init__", init)
    monkeypatch.setattr(os.environ, "get", fake_key)
    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)
    config: JsonObject = {
        "provider": "openai",
        "model": "gpt-6.1-sol",
        "returned_models": ["gpt-6.1-sol"],
        "reasoning_effort": "low",
        "max_output_tokens": 2048,
        "timeout_seconds": 120,
        "max_response_bytes": 2000000,
        "model_support_review": "https://developers.openai.com/api/docs/models/gpt-6.1-sol",
        "budget_review": "UNIT_TEST_FAKE_HTTP_ONLY_NO_REAL_API",
    }
    plan = make_plan(mode="live", config=config, reviewed=True, allocation_count=1)
    directory = tmp_path / "fake-live"
    report = runner.execute(
        plan,
        directory,
        live=True,
        enable_live=True,
        approved_plan_sha256=cast(str, plan["plan_sha256"]),
    )
    assert report["status"] == "INCLUDED" and report["immediate_success_count"] == 1
    result = obj(read_json(directory / "a001.json")["result"])
    assert payloads == [cast(str, obj(rows(result["steps"])[0]["wire"])["canonical_utf8"]).encode()]
    assert len(key_reads) == 1
    monkeypatch.setattr(os.environ, "get", forbidden)
    monkeypatch.setattr(HTTPTransport, "__init__", forbidden)
    assert audit_directory(directory) == report
