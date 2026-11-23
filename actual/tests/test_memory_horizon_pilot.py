"""Live-pilot preflight mechanics only. No model behavior is measured here."""

import ast
import json
import os
import socket
from collections.abc import Callable
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from journeymap.adapters.event_memory import EventMemoryPolicy, RecencyEventMemory
from journeymap.adapters.llm import LLMController, Record
from journeymap.adapters.memory import NoMemory
from journeymap.adapters.memory_horizon_live import (
    CONFIGURATION_VERSION,
    WIRE_SCHEMA_VERSION,
    MockHorizonProvider,
    MockOutcome,
    PilotController,
    RecordingMockTransport,
    request_body,
    validate_candidate,
    wire_schema,
)
from journeymap.adapters.memory_horizon_prompt import PROFILE, PROFILE_V1
from journeymap.adapters.prompt_profile import PromptProfile
from journeymap.adapters.provider import ProviderRequest
from journeymap.application.observations import ObservationPipeline
from journeymap.application.research import ResearchView
from journeymap.application.session import SimulationApplication
from journeymap.bootstrap import create_memory_horizon_application
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.core.kernel import SimulationKernel
from journeymap.examples.memory_horizon import HorizonFakeProvider
from journeymap.experiments.memory_horizon import PROTOCOL_VERSION_V1
from journeymap.experiments.memory_horizon import run_trial as offline_trial
from journeymap.experiments.memory_horizon_audit import assess_trial, obj, rows
from journeymap.experiments.memory_horizon_pilot import run_mock_trial, run_preflight
from journeymap.experiments.memory_horizon_pilot_audit import (
    audit_cohort,
    audit_export,
    audit_record,
    inspect_cohort,
    inspect_trial,
)
from journeymap.experiments.memory_horizon_study import (
    CONDITIONS,
    PROTOCOL_VERSION,
    add_replacement,
    allocations,
    build_cohort,
    derived_metrics,
    official_m8_reference,
    validate_cohort,
)
from journeymap.experiments.pilot_journal import (
    AttemptJournal,
    digest,
    inspect_journal,
    read_journal,
    write_snapshot,
)

type JournalWriter = Callable[[str, JsonObject], None]


@pytest.fixture(autouse=True)
def offline_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network forbidden in preflight")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


def plan(*, max_decisions: int = 24) -> JsonObject:
    # Explicit fixture seeds, not silently chosen/approved live study values.
    return build_cohort(
        cohort_id="pilot-fixture",
        engine_seed=42,
        allocation_randomization_seed=917,
        configuration=replace(official_m8_reference(), max_decisions=max_decisions),
    )


def moves(targets: tuple[str, ...] = ("inn", "bakery", "well")) -> tuple[MockOutcome, ...]:
    return tuple(
        MockOutcome(json.dumps({"action_type": "MOVE", "payload": {"route_id": route}}))
        for target in targets
        for route in (f"village-square-to-{target}", f"{target}-to-village-square")
    )


def first_id(cohort: JsonObject) -> str:
    return cast(str, rows(cohort["allocations"])[0]["allocation_id"])


@pytest.fixture(scope="module")
def cohort_run(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[JsonObject, dict[str, JsonObject], Path]:
    cohort = plan()
    root = tmp_path_factory.mktemp("pilot") / "batch"
    scripted = {cast(str, a["allocation_id"]): moves() for a in rows(cohort["allocations"])}
    captured: list[Record] = []
    send = RecordingMockTransport.send

    def recording(self: RecordingMockTransport, body: JsonObject) -> MockOutcome:
        captured.append(Record.capture(body))
        return send(self, body)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(RecordingMockTransport, "send", recording)
        finished = run_preflight(cohort, root, outcomes=scripted)
    records = {
        cast(str, a["allocation_id"]): obj(
            json.loads((root / cast(str, a["allocation_id"]) / "trial.json").read_text())
        )
        for a in rows(finished["allocations"])
    }
    assert [r.data for r in captured] == [
        obj(d["call"])["transport_body"]
        for data in records.values()
        for d in rows(data["decisions"])
    ]
    return finished, records, root


def test_balanced_blocks_and_seed_reproducibility() -> None:
    a = allocations("cohort", 22)
    assert a == allocations("cohort", 22)
    assert a != allocations("cohort", 23)
    assert len(a) == 12 and len({row["intended_trial_id"] for row in a}) == 12
    for block in (1, 2, 3):
        assert sorted(cast(str, row["condition"]) for row in a if row["block"] == block) == sorted(
            CONDITIONS
        )
    cohort = plan()
    assert cohort["engine_seed"] == 42 and cohort["allocation_randomization_seed"] == 917
    assert cohort["configuration_approval"] == "REQUIRES HUMAN DECISION"
    assert cohort["live_transport_enabled"] is False
    assert "not a behavioral-effect" in cast(str, cohort["purpose"])
    assert official_m8_reference().to_json() == {
        "provider_name": "openai",
        "model": "gpt-5.6-sol",
        "reference_adapter_version": "responses-http-2",
        "reasoning_effort": "medium",
        "max_output_tokens": 4096,
        "transport_timeout_seconds": 20,
        "max_provider_calls": 24,
        "max_decisions": 24,
        "max_consecutive_failures": 3,
        "wall_timeout_seconds": 240,
    }


@pytest.mark.parametrize("seed", [None, True, 1.5, "42"])
def test_no_implicit_seed(seed: object) -> None:
    with pytest.raises(ValueError):
        allocations("cohort", cast(int, seed))
    with pytest.raises(ValueError):
        build_cohort(
            cohort_id="cohort",
            engine_seed=cast(int, seed),
            allocation_randomization_seed=1,
            configuration=official_m8_reference(),
        )


@pytest.mark.parametrize("changed", ["order", "block", "condition", "source", "live", "endpoint"])
def test_plan_corruption_rejected(changed: str) -> None:
    cohort = plan()
    if changed in ("order", "block", "condition"):
        rows(cohort["allocations"])[0][changed] = "forged"
    elif changed == "source":
        obj(cohort["source"])["working_source_sha256"] = "0" * 64
    elif changed == "live":
        cohort["live_transport_enabled"] = True
    else:
        cohort["endpoint"] = "https://invalid.example"
    with pytest.raises(ValueError):
        validate_cohort(cohort)


def test_provider_reuse_contamination_is_explicitly_rejected() -> None:
    events: list[tuple[str, JsonObject]] = []
    transport = RecordingMockTransport(moves())
    provider = MockHorizonProvider(transport, lambda kind, data: events.append((kind, data)))
    config = official_m8_reference()
    first = PilotController(provider, NoMemory(), config.model, config.parameters)
    assert first.archive.traces == ()
    provider.generate(
        ProviderRequest(
            "fixed", PROFILE.version, config.model, CONFIGURATION_VERSION, config.parameters
        )
    )
    assert len(transport.calls) == 1
    with pytest.raises(ValueError, match="provider reuse"):
        PilotController(provider, RecencyEventMemory(3), config.model, config.parameters)
    # A new wrapper around the old stateful transport also cannot hide reuse.
    second = MockHorizonProvider(transport, lambda kind, data: None)
    with pytest.raises(ValueError, match="transport reuse"):
        PilotController(second, NoMemory(), config.model, config.parameters)


def test_fresh_lifetimes_for_each_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import journeymap.experiments.memory_horizon_pilot as runner

    kept: dict[str, list[object]] = {
        key: [] for key in ("provider", "controller", "monitor", "kernel", "application")
    }
    original_provider = MockHorizonProvider.__init__
    original_controller = PilotController.__init__
    original_application = create_memory_horizon_application

    def provider_init(
        self: MockHorizonProvider, transport: RecordingMockTransport, record: object
    ) -> None:
        original_provider(self, transport, cast("JournalWriter", record))
        kept["provider"].append(self)

    def controller_init(
        self: PilotController,
        provider: MockHorizonProvider,
        memory: object,
        model: str,
        parameters: JsonObject,
    ) -> None:
        original_controller(self, provider, cast("EventMemoryPolicy", memory), model, parameters)
        kept["controller"].append(self)

    def application(
        kernel: "SimulationKernel", *, pipeline: "ObservationPipeline | None" = None
    ) -> tuple["SimulationApplication", "ResearchView"]:
        result = original_application(kernel, pipeline=pipeline)
        kept["kernel"].append(kernel)
        kept["monitor"].append(pipeline)
        kept["application"].append(result[0])
        return result

    monkeypatch.setattr(MockHorizonProvider, "__init__", provider_init)
    monkeypatch.setattr(PilotController, "__init__", controller_init)
    monkeypatch.setattr(runner, "create_memory_horizon_application", application)
    cohort = plan()
    # Short fixture-only bounds keep this identity test independent of behavior.
    cohort = plan(max_decisions=1)
    run_preflight(cohort, tmp_path / "batch")
    for values in kept.values():
        assert len(values) == len({id(v) for v in values}) == 12
    archives = [cast(PilotController, c).archive for c in kept["controller"]]
    assert len({id(a) for a in archives}) == 12


@pytest.mark.parametrize(
    "candidate",
    [
        {"action_type": "REST", "payload": {"duration": 1}},
        {"action_type": "WAIT", "payload": {"duration": 2}},
        {"action_type": "WAIT", "payload": {"duration": True}},
        {"action_type": "WAIT", "payload": {"duration": 1.0}},
        {"action_type": "MOVE", "payload": {"duration": 1}},
        {"action_type": "WAIT", "payload": {"route_id": "x"}},
        {"action_type": "MOVE", "payload": {"route_id": ""}},
        {"action_type": "MOVE", "payload": {"route_id": "x"}, "run_id": "forged"},
    ],
)
def test_wire_contract_rejects_other_actions_durations_and_pairings(candidate: JsonObject) -> None:
    with pytest.raises(ValueError):
        validate_candidate(candidate)


def test_wire_schema_is_independent_narrow_and_valid_candidates() -> None:
    from journeymap.adapters.decision_schema import decision_schema

    old = canonical_json(decision_schema())
    schema = wire_schema()
    props = obj(schema["properties"])
    assert obj(props["action_type"])["enum"] == ["MOVE", "WAIT"]
    branches = rows(obj(props["payload"])["anyOf"])
    assert obj(obj(branches[1]["properties"])["duration"]) == {"type": "integer", "enum": [1]}
    assert "anyOf" not in schema and "oneOf" not in schema
    for candidate in (
        {"action_type": "MOVE", "payload": {"route_id": "x"}},
        {"action_type": "WAIT", "payload": {"duration": 1}},
    ):
        validate_candidate(candidate)
    props["action_type"] = {}
    assert wire_schema()["properties"] != props
    assert canonical_json(decision_schema()) == old


def test_full_body_golden_equivalence_and_no_allocation_leak(
    cohort_run: tuple[JsonObject, dict[str, JsonObject], Path],
) -> None:
    cohort, records, _ = cohort_run
    bodies: dict[int, set[str]] = {}
    first_hashes: set[str] = set()
    for allocation in rows(cohort["allocations"]):
        data = records[cast(str, allocation["allocation_id"])]
        k = (
            0
            if allocation["condition"] == "no-event-memory"
            else int(cast(str, allocation["condition"])[-1])
        )
        assert len(rows(data["decisions"])) == 18
        for index, d in enumerate(rows(data["decisions"])):
            call = obj(d["call"])
            body = Record.capture(obj(call["transport_body"])).data
            assert body == call["body"]
            assert set(body) == {
                "model",
                "input",
                "store",
                "text",
                "reasoning",
                "max_output_tokens",
            }
            assert body["store"] is False
            assert body["reasoning"] == {"effort": "medium"}
            assert body["max_output_tokens"] == 4096
            assert call["configuration_version"] == CONFIGURATION_VERSION
            assert call["wire_schema_version"] == WIRE_SCHEMA_VERSION
            assert call["body_canonical"] == canonical_json(body)
            assert call["body_sha256"] == sha256(canonical_json(body).encode()).hexdigest()
            prompt = cast(str, body["input"])
            assert call["prompt_sha256"] == sha256(prompt.encode()).hexdigest()
            text, raw = prompt.split("\nINPUT_JSON\n")
            assert text == PROFILE.instructions
            inputs = obj(json.loads(raw))
            assert set(inputs) == {"observation", "event_memory"}
            assert set(obj(inputs["observation"])) == {"content"}
            forbidden_keys = {
                "trial_id",
                "allocation_id",
                "block",
                "order",
                "condition",
                "k",
                "policy",
                "memory_policy",
                "memory_policy_version",
                "run_id",
                "actor_id",
                "observation_id",
                "action_request_id",
                "event_trace_id",
                "simulation_time",
                "opportunity_sequence",
                "opportunity_attempt",
                "decision_sequence",
                "observation_sequence",
                "content_digest",
                "started_at",
                "resolved_at",
                "retrieved_count",
                "task_completed",
                "primary_success",
                "remaining_ticks",
            }

            def check_keys(value: JsonValue, forbidden: set[str] = forbidden_keys) -> None:
                if isinstance(value, dict):
                    assert not (set(value) & forbidden)
                    for nested in value.values():
                        check_keys(nested)
                elif isinstance(value, list):
                    for nested in value:
                        check_keys(nested)

            check_keys(inputs)
            for event in rows(inputs["event_memory"]):
                assert set(event) == {"observation", "action", "receipt"}
                assert set(obj(event["observation"])) == {"content"}
                assert set(obj(event["action"])) == {"action_type", "payload"}
                assert set(obj(event["receipt"])) == {"status", "reason_code"}
            assert len(rows(inputs["event_memory"])) == min(index, k)
            assert canonical_json(inputs) == raw
            for forbidden in (
                "pilot-fixture",
                "allocation_id",
                "block",
                "randomization",
                "condition",
                "trial_id",
                "previous_response_id",
                "conversation",
            ):
                assert forbidden not in canonical_json(body)
            if index == 0:
                first_hashes.add(call["body_sha256"])
            inputs["event_memory"] = []
            body["input"] = text + "\nINPUT_JSON\n" + canonical_json(inputs)
            bodies.setdefault(index, set()).add(canonical_json(body))
    assert len(first_hashes) == 1
    # Canonical UTF-8, fixed prompt v2/schema/model/parameters and initial semantic
    # state. No allocation/source/runtime data enters the body. Structural
    # allowlists and all-opportunity comparisons above/below are independent gates.
    assert first_hashes == {"417326ce6d92dd10a7fecdbf02af8f36d337103f0f40bd5bfa33a4777bf3345b"}
    assert len(bodies) == 18 and all(len(values) == 1 for values in bodies.values())


@pytest.mark.parametrize(
    "targets,primary,pre,post,strict,at",
    [
        (("inn", "bakery", "well"), True, 0, 0, True, 6),
        (("inn", "inn", "bakery", "well"), False, 1, 0, False, 8),
        (("inn", "bakery", "well", "inn"), True, 0, 1, False, 6),
        (("inn", "inn", "bakery", "well", "well"), False, 1, 1, False, 8),
        (("inn", "bakery"), False, 0, 0, False, None),
        ((), False, 0, 0, False, None),
    ],
)
def test_primary_and_post_completion_oracles(
    tmp_path: Path,
    targets: tuple[str, ...],
    primary: bool,
    pre: int,
    post: int,
    strict: bool,
    at: int | None,
) -> None:
    cohort = plan()
    data = run_mock_trial(
        cohort, first_id(cohort), tmp_path / "trial", outcomes=moves(targets)
    ).data
    metrics = obj(data["metrics"])
    assert len(metrics) == 8
    assert data["derived_metrics"] == {
        "primary_success": primary,
        "pre_completion_repeat_count": pre,
    }
    assert metrics["post_completion_repeat_count"] == post
    assert metrics["strict_no_repeat_success"] is strict
    assert metrics["decisions_to_completion"] == at
    assert obj(data["manifest"])["simulation_end"] == 24
    assert audit_export(tmp_path / "trial")["behavioral_denominator"] is True


@pytest.mark.parametrize(
    "outcome,failure",
    [
        (MockOutcome(refusal=True), "REFUSAL"),
        (MockOutcome(status="incomplete"), "INCOMPLETE_OUTPUT"),
        (MockOutcome(text="invalid"), "PARSER_FAILURE"),
        (MockOutcome(text='{"action_type":"WAIT","payload":{"duration":2}}'), "SCHEMA_INVALID"),
    ],
)
def test_behavioral_failures_remain_in_denominator(
    tmp_path: Path, outcome: MockOutcome, failure: str
) -> None:
    cohort = plan()
    data = run_mock_trial(
        cohort, first_id(cohort), tmp_path / "trial", outcomes=(outcome,) * 3
    ).data
    assert [d["failure"] for d in rows(data["decisions"])] == [failure] * 3
    assert len({canonical_json(d["provider_request"]) for d in rows(data["decisions"])}) == 1
    assert data["event_traces"] == data["action_traces"] == []
    assert obj(data["metrics"])["decision_attempt_count"] == 3
    assert obj(data["metrics"])["engine_submission_count"] == 0
    assessment = audit_export(tmp_path / "trial")
    assert assessment["integrity_status"] == "VERIFIED"
    assert assessment["behavioral_denominator"] is True
    assert assessment["behavioral_failure_count"] == 3


def test_rejected_move_is_behavioral_and_occupies_memory(tmp_path: Path) -> None:
    cohort = plan()
    allocation = next(a for a in rows(cohort["allocations"]) if a["condition"] == "recency-k3")
    data = run_mock_trial(
        cohort,
        cast(str, allocation["allocation_id"]),
        tmp_path / "trial",
        outcomes=(MockOutcome(text='{"action_type":"MOVE","payload":{"route_id":"absent"}}'),) * 3,
    ).data
    assert len(rows(data["event_traces"])) == 3
    assert [d["retrieved_count"] for d in rows(data["decisions"])] == [0, 1, 2]
    assert obj(data["metrics"])["unique_destination_coverage"] == 0
    assert audit_export(tmp_path / "trial")["behavioral_denominator"] is True


def test_infrastructure_replacement_retains_original_and_linkage(tmp_path: Path) -> None:
    cohort = plan()
    cohort = plan(max_decisions=1)
    root = tmp_path / "original"
    original = run_preflight(
        cohort, root, outcomes={first_id(cohort): (MockOutcome(transport_failure=True),)}
    )
    failed = rows(original["allocations"])[0]
    assert failed["integrity_status"] == "VERIFIED"
    assert failed["infrastructure_failure"] is True and failed["behavioral_denominator"] is False
    replacement = add_replacement(original, cast(str, failed["allocation_id"]))
    validate_cohort(replacement)
    assert rows(replacement["allocations"])[:12] == rows(original["allocations"])
    added = rows(replacement["allocations"])[-1]
    assert added["replacement_of"] == failed["allocation_id"]
    assert added["condition"] == failed["condition"] and added["block"] == failed["block"]
    with pytest.raises(ValueError):
        add_replacement(replacement, cast(str, failed["allocation_id"]))
    with pytest.raises(ValueError):
        add_replacement(original, cast(str, rows(original["allocations"])[1]["allocation_id"]))
    done = run_preflight(replacement, tmp_path / "replacement")
    evidence = {
        cast(str, a["allocation_id"]): obj(
            json.loads((root / cast(str, a["allocation_id"]) / "trial.json").read_text())
        )
        for a in rows(original["allocations"])
    }
    evidence[cast(str, added["allocation_id"])] = obj(
        json.loads(
            (
                tmp_path / "replacement" / cast(str, added["allocation_id"]) / "trial.json"
            ).read_text()
        )
    )
    assert (
        audit_cohort(done, evidence, batches=(root, tmp_path / "replacement"))["all_attempt_count"]
        == 13
    )
    del evidence[cast(str, failed["allocation_id"])]
    assert (
        audit_cohort(done, evidence, batches=(root, tmp_path / "replacement"))["integrity_status"]
        == "EXCLUDED"
    )


def test_manual_replacement_of_integrity_excluded_parent_rejected() -> None:
    cohort = plan()
    allocation_rows = cast(list[JsonObject], cohort["allocations"])
    parent = allocation_rows[0]
    replacement: JsonObject = {
        **parent,
        "allocation_id": f"{cohort['cohort_id']}-a013",
        "intended_trial_id": f"{cohort['cohort_id']}-t013",
        "order": 13,
        "replacement_of": parent["allocation_id"],
        "replacement_reason": "INFRASTRUCTURE_FAILURE",
    }
    parent.update(
        execution_status="FINISHED",
        infrastructure_failure=True,
        integrity_status="EXCLUDED",
        behavioral_denominator=False,
        primary_success=False,
        trial_record_sha256="0" * 64,
    )
    allocation_rows.append(replacement)
    with pytest.raises(ValueError, match="invalid replacement linkage"):
        validate_cohort(cohort)
    parent["integrity_status"] = "VERIFIED"
    validate_cohort(cohort)


def test_infrastructure_after_behavior_does_not_remove_responded_trial(tmp_path: Path) -> None:
    cohort = plan()
    data = run_mock_trial(
        cohort,
        first_id(cohort),
        tmp_path / "trial",
        outcomes=(MockOutcome(text="invalid"), MockOutcome(transport_failure=True)),
    ).data
    assert obj(data["metrics"])["decision_attempt_count"] == 2
    assert audit_export(tmp_path / "trial")["behavioral_denominator"] is True


@pytest.mark.parametrize(
    "tamper",
    [
        "primary",
        "pre-repeat",
        "old-metric",
        "body",
        "schema",
        "memory",
        "failure",
        "receipt",
        "replay",
        "condition",
        "coherent-body",
        "transport-body",
        "body-hash",
    ],
)
def test_resealed_trial_tampering_excluded(
    cohort_run: tuple[JsonObject, dict[str, JsonObject], Path], tamper: str
) -> None:
    _, records, _ = cohort_run
    data = Record.capture(next(iter(records.values()))).data
    d = rows(data["decisions"])[4]
    if tamper == "primary":
        obj(data["derived_metrics"])["primary_success"] = False
    elif tamper == "pre-repeat":
        obj(data["derived_metrics"])["pre_completion_repeat_count"] = 1
    elif tamper == "old-metric":
        obj(data["metrics"])["decision_attempt_count"] = 6
    elif tamper in ("body", "coherent-body"):
        call = obj(d["call"])
        body = obj(call["body"])
        body["conversation"] = "leaked"
        if tamper == "coherent-body":
            call["body_canonical"] = canonical_json(body)
            call["body_sha256"] = sha256(canonical_json(body).encode()).hexdigest()
    elif tamper == "schema":
        d["wire_schema_version"] = "decision-candidate-2"
    elif tamper == "transport-body":
        obj(obj(d["call"])["transport_body"])["metadata"] = {"allocation_id": "leaked"}
    elif tamper == "body-hash":
        obj(d["call"])["body_sha256"] = "0" * 64
    elif tamper == "memory":
        obj(d["model_visible_input"])["condition"] = "k3"
    elif tamper == "failure":
        d["failure"] = "REFUSAL"
    elif tamper == "receipt":
        obj(d["receipt"])["status"] = "FAILED"
    elif tamper == "condition":
        obj(obj(data["manifest"])["allocation"])["condition"] = "unknown"
    else:
        obj(data["replay_input"])["advance_to"] = 23
    data["record_sha256"] = digest({k: v for k, v in data.items() if k != "record_sha256"})
    assert audit_record(data)["integrity_status"] == "EXCLUDED"


def test_cohort_audit_provider_free_and_denominator_tampering(
    cohort_run: tuple[JsonObject, dict[str, JsonObject], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    cohort, records, root = cohort_run

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("audit must not call or construct provider/controller")

    monkeypatch.setattr(MockHorizonProvider, "__init__", forbidden)
    monkeypatch.setattr(MockHorizonProvider, "generate", forbidden)
    monkeypatch.setattr(PilotController, "__init__", forbidden)
    audit = audit_cohort(cohort, records, batches=(root,))
    assert audit["integrity_status"] == "VERIFIED" and audit["behavioral_denominator_count"] == 12
    assert all(
        audit_export(root / identity)["integrity_status"] == "VERIFIED" for identity in records
    )
    damaged = Record.capture(cohort).data
    rows(damaged["allocations"])[0]["behavioral_denominator"] = False
    assert audit_cohort(damaged, records, batches=(root,))["integrity_status"] == "EXCLUDED"


def test_journal_flushes_attempts_before_failure_and_detects_torn_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flushed: list[int] = []
    original = os.fsync

    def fsync(fd: int) -> None:
        original(fd)
        flushed.append(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    path = tmp_path / "attempts.jsonl"
    journal = AttemptJournal(path)
    provider = MockHorizonProvider(
        RecordingMockTransport((MockOutcome(transport_failure=True),)), journal.append
    )
    provider.claim()
    request = ProviderRequest(
        "fixed", PROFILE.version, "test", CONFIGURATION_VERSION, official_m8_reference().parameters
    )
    from journeymap.adapters.provider import ProviderFailure

    with pytest.raises(ProviderFailure):
        provider.generate(request)
    assert [r["kind"] for r in read_journal(path)] == ["provider_intent", "provider_result"]
    assert len(flushed) == 2
    journal.close()
    assert audit_export(tmp_path)["integrity_status"] == "EXCLUDED"
    path.write_bytes(path.read_bytes()[:-1])  # New test fixture only, never historical raw.
    with pytest.raises(ValueError, match="torn"):
        read_journal(path)


def test_journal_and_snapshot_never_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "new.jsonl"
    journal = AttemptJournal(path)
    journal.append("test", {"x": 1})
    journal.close()
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        AttemptJournal(path)
    with pytest.raises(FileExistsError):
        write_snapshot(path, {"different": True})
    assert path.read_bytes() == before
    path.write_bytes(before.replace(b'"x":1', b'"x":2'))
    with pytest.raises(ValueError):
        read_journal(path)


def test_missing_or_mismatched_finished_journal_excluded(tmp_path: Path) -> None:
    cohort = plan()
    root = tmp_path / "trial"
    run_mock_trial(cohort, first_id(cohort), root)
    path = root / "attempts.jsonl"
    content = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join(content[:-1]))
    assert audit_export(root)["integrity_status"] == "EXCLUDED"


@pytest.mark.parametrize(
    "profile,protocol",
    [(PROFILE_V1, PROTOCOL_VERSION_V1), (PROFILE, "alderwick-memory-horizon-offline-2")],
)
def test_historical_offline_versions_keep_own_meaning(
    profile: "PromptProfile", protocol: str
) -> None:
    controller = LLMController(
        HorizonFakeProvider(), event_memory=NoMemory(), prompt_profile=profile
    )
    data = offline_trial(controller, trial_id="unchanged-offline", protocol_version=protocol).data
    assert obj(data["manifest"])["protocol_version"] == protocol
    assert "derived_metrics" not in data
    assert len(obj(data["metrics"])) == 8
    assert assess_trial(data)["status"] == "INCLUDED"
    with pytest.raises(ValueError):
        offline_trial(
            LLMController(HorizonFakeProvider(), event_memory=NoMemory(), prompt_profile=PROFILE),
            trial_id="wrong",
            protocol_version=PROTOCOL_VERSION,
        )


def test_preflight_has_no_network_import_or_live_toggle() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "journeymap"
    files = [
        root / "adapters" / "memory_horizon_live.py",
        *(root / "experiments").glob("memory_horizon_pilot*.py"),
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        imports = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        imports += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not any(
            name.startswith(("socket", "http", "urllib", "requests")) or "openai_provider" in name
            for name in imports
        )
        assert "OPENAI_API_KEY" not in text and '"--live"' not in text
    request = ProviderRequest(
        "fixed", PROFILE.version, "test", CONFIGURATION_VERSION, official_m8_reference().parameters
    )
    assert "previous_response_id" not in request_body(request)


def test_derived_metrics_reject_invalid_counts() -> None:
    with pytest.raises(ValueError):
        derived_metrics(
            {
                "repeat_destination_count": 0,
                "post_completion_repeat_count": 1,
                "task_completed": True,
            }
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("behavioral_denominator", True),
        ("behavioral_denominator", False),
        ("infrastructure_failure", True),
        ("primary_success", False),
        ("trial_record_sha256", "0" * 64),
        ("integrity_status", "VERIFIED"),
    ],
)
def test_planned_result_injection_is_not_evidence(field: str, value: object) -> None:
    cohort = plan()
    rows(cohort["allocations"])[0][field] = cast("JsonValue", value)
    with pytest.raises(ValueError, match="planned allocation"):
        validate_cohort(cohort, execution=False)
    assert audit_cohort(cohort, {})["integrity_status"] == "EXCLUDED"


@pytest.mark.parametrize("tamper", ["rollback", "delete", "admission", "primary", "all-rollback"])
def test_cohort_inventory_adversaries(
    cohort_run: tuple[JsonObject, dict[str, JsonObject], Path],
    tamper: str,
) -> None:
    original, original_records, root = cohort_run
    cohort = Record.capture(original).data
    records = dict(original_records)
    row = rows(cohort["allocations"])[0]
    if tamper in ("rollback", "all-rollback"):
        victims = rows(cohort["allocations"]) if tamper == "all-rollback" else [row]
        for victim in victims:
            victim.update(
                {
                    "execution_status": "PLANNED",
                    "integrity_status": "NOT_ASSESSED",
                    "behavioral_denominator": None,
                    "infrastructure_failure": None,
                    "primary_success": None,
                    "trial_record_sha256": None,
                }
            )
            records.pop(cast(str, victim["allocation_id"]))
    elif tamper == "delete":
        records.pop(cast(str, row["allocation_id"]))
    elif tamper == "admission":
        row["behavioral_denominator"] = False
    else:
        row["primary_success"] = False
    assert audit_cohort(cohort, records, batches=(root,))["integrity_status"] == "EXCLUDED"


@pytest.mark.parametrize(
    "boundary",
    [
        "trial_started",
        "provider_intent",
        "provider_result",
        "engine_intent",
        "engine_receipt",
        "trial_finished",
        "partial_snapshot",
    ],
)
def test_crash_boundaries_are_read_only_indeterminate_prefixes(
    cohort_run: tuple[JsonObject, dict[str, JsonObject], Path],
    tmp_path: Path,
    boundary: str,
) -> None:
    _, records, root = cohort_run
    identity = next(iter(records))
    lines = (root / identity / "attempts.jsonl").read_bytes().splitlines(keepends=True)
    kind = "trial_finished" if boundary == "partial_snapshot" else boundary
    cut = next(i for i, line in enumerate(lines) if json.loads(line)["kind"] == kind) + 1
    journal = tmp_path / "attempts.jsonl"
    journal.write_bytes(b"".join(lines[:cut]))
    if boundary == "partial_snapshot":
        (tmp_path / "trial.json").write_bytes(b'{"manifest":')
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    partial = inspect_trial(tmp_path)
    assert partial["last_journal_kind"] == kind
    assert partial["trial_started"] is True and partial["interrupted"] is True
    assert partial["infrastructure_failure"] is None  # Missing response is not a model failure.
    assert partial["snapshot_status"] == (
        "INVALID" if boundary == "partial_snapshot" else "MISSING"
    )
    assert partial["trial_finished"] is (kind == "trial_finished")
    assert audit_export(tmp_path)["integrity_status"] == "EXCLUDED"
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize(
    "damage,expected",
    [
        ("torn", "TORN_TAIL"),
        ("middle-json", "CORRUPT_RECORD"),
        ("middle-hash", "CHAIN_BREAK"),
        ("last-hash", "CHAIN_BREAK"),
    ],
)
def test_journal_verified_prefix_distinguishes_corruption(
    tmp_path: Path,
    damage: str,
    expected: str,
) -> None:
    path = tmp_path / "journal.jsonl"
    journal = AttemptJournal(path)
    for n in range(3):
        journal.append("test", {"n": n})
    journal.close()
    lines = path.read_bytes().splitlines(keepends=True)
    if damage == "torn":
        lines[-1] = lines[-1][:20]
    elif damage == "middle-json":
        lines[1] = b"{broken}\n"
    else:
        at = -1 if damage == "last-hash" else 1
        row = json.loads(lines[at])
        row["previous_sha256"] = "f" * 64
        lines[at] = (json.dumps(row) + "\n").encode()
    path.write_bytes(b"".join(lines))
    before = path.read_bytes()
    inspected = inspect_journal(path)
    assert inspected.status == expected
    assert len(inspected.rows) == (2 if damage in ("torn", "last-hash") else 1)
    with pytest.raises(ValueError):
        read_journal(path)
    assert path.read_bytes() == before


def test_cohort_allocation_start_crash_is_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import journeymap.experiments.memory_horizon_pilot as runner

    def interrupted(*args: object, **kwargs: object) -> Record:
        raise OSError("injected interruption")

    monkeypatch.setattr(runner, "run_mock_trial", interrupted)
    cohort = plan()
    with pytest.raises(OSError):
        runner.run_preflight(cohort, tmp_path / "batch")
    report = inspect_cohort(tmp_path / "batch")
    assert report["prefix_valid"] is True
    states = rows(report["allocations"])
    assert states[0]["allocation_started"] is True
    assert states[0]["allocation_finished"] is False
    assert all(a["allocation_started"] is False for a in states[1:])
    assert obj(states[0]["trial"])["trial_started"] is False
    assert audit_cohort(cohort, {}, batches=(tmp_path / "batch",))["integrity_status"] == "EXCLUDED"


@pytest.mark.parametrize("phase", ["observation", "engine"])
def test_system_error_provenance_survives_integrity_exclusion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    def broken(*args: object, **kwargs: object) -> object:
        raise RuntimeError("system fault, not model behavior")

    owner = SimulationApplication if phase == "observation" else SimulationKernel
    monkeypatch.setattr(owner, "_observe" if phase == "observation" else "submit_action", broken)
    cohort = plan()
    data = run_mock_trial(cohort, first_id(cohort), tmp_path / "trial").data
    failure = "OBSERVATION_UNAVAILABLE" if phase == "observation" else "ENGINE_ERROR"
    assert rows(data["decisions"])[0]["failure"] == failure
    assessed = audit_export(tmp_path / "trial")
    assert assessed["integrity_status"] == "EXCLUDED"  # Fault itself cannot be replay-proven.
    assert assessed["infrastructure_failure"] is True
    assert assessed["behavioral_denominator"] is False
    # Damage the final snapshot: independently retained error provenance must survive.
    (tmp_path / "trial" / "trial.json").write_text("{torn", encoding="utf-8")
    assert audit_export(tmp_path / "trial")["infrastructure_failure"] is True


def test_engine_failed_receipt_stays_in_behavioral_denominator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from journeymap.core.handlers import ActionValidationError
    from journeymap.modules.movement.handlers import MoveHandler

    def failed(*args: object, **kwargs: object) -> None:
        raise ActionValidationError("ROUTE_CHANGED")

    # This empty-schedule scenario cannot naturally change routes mid-move.
    # Inject only a completion validation rejection, using the real kernel FAILED path.
    monkeypatch.setattr(MoveHandler, "validate_completion", failed)
    cohort = plan(max_decisions=1)
    data = run_mock_trial(cohort, first_id(cohort), tmp_path / "trial", outcomes=moves()).data
    receipt = obj(rows(data["decisions"])[0]["receipt"])
    assert receipt["status"] == "FAILED"
    assert len(rows(data["event_traces"])) == 1
    assessed = audit_export(tmp_path / "trial")
    assert assessed["integrity_status"] == "VERIFIED"
    assert assessed["behavioral_denominator"] is True
    assert assessed["infrastructure_failure"] is False


def test_persistence_error_aborts_before_transport_or_next_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_fsync = os.fsync
    count = 0

    def broken(fd: int) -> None:
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("injected disk error")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", broken)
    cohort = plan()
    with pytest.raises(OSError, match="persistence"):
        run_mock_trial(cohort, first_id(cohort), tmp_path / "trial")
    assert count == 2 and not (tmp_path / "trial" / "trial.json").exists()
    partial = inspect_trial(tmp_path / "trial")
    assert partial["last_journal_kind"] == "provider_intent"
    assert partial["interrupted"] is True and partial["behavioral_denominator"] is False


def test_execution_freeze_is_separate_from_later_audit(
    cohort_run: tuple[JsonObject, dict[str, JsonObject], Path],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import journeymap.experiments.memory_horizon_study as study

    cohort, records, root = cohort_run
    validate_cohort(cohort)
    monkeypatch.setattr(study, "code_identity", lambda: {"different": True})
    monkeypatch.setattr(study, "runtime_identity", lambda: {"different": True})
    with pytest.raises(ValueError, match="source/runtime"):
        validate_cohort(cohort)
    with pytest.raises(ValueError, match="source/runtime"):
        run_mock_trial(cohort, first_id(cohort), tmp_path / "forbidden")
    assert not (tmp_path / "forbidden").exists()
    validate_cohort(cohort, execution=False)
    assert audit_cohort(cohort, records, batches=(root,))["integrity_status"] == "VERIFIED"


def test_resealed_source_tamper_disagrees_with_retained_journal(
    cohort_run: tuple[JsonObject, dict[str, JsonObject], Path],
    tmp_path: Path,
) -> None:
    _, records, root = cohort_run
    identity = next(iter(records))
    data = Record.capture(records[identity]).data
    cohort = obj(obj(data["manifest"])["cohort"])
    obj(cohort["source"])["working_source_sha256"] = "0" * 64
    assert audit_record(data)["integrity_status"] == "EXCLUDED"
    cohort["frozen_plan_sha256"] = digest(
        {k: v for k, v in cohort.items() if k not in ("allocations", "frozen_plan_sha256")}
    )
    data["record_sha256"] = digest({k: v for k, v in data.items() if k != "record_sha256"})
    (tmp_path / "trial.json").write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "attempts.jsonl").write_bytes((root / identity / "attempts.jsonl").read_bytes())
    assert audit_export(tmp_path)["integrity_status"] == "EXCLUDED"


def test_snapshot_presence_is_independent_of_journal_presence(
    cohort_run: tuple[JsonObject, dict[str, JsonObject], Path],
    tmp_path: Path,
) -> None:
    _, records, root = cohort_run
    identity = next(iter(records))
    complete = inspect_trial(root / identity)
    assert complete["trial_finished"] is True and complete["snapshot_linked"] is True
    assert complete["interrupted"] is False
    (tmp_path / "trial.json").write_bytes((root / identity / "trial.json").read_bytes())
    partial = inspect_trial(tmp_path)
    assert partial["snapshot_status"] == "PRESENT"
    assert partial["journal_status"] == "MISSING"
    assert partial["interrupted"] is True
    assert audit_export(tmp_path)["integrity_status"] == "EXCLUDED"
