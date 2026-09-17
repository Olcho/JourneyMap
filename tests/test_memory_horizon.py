"""Offline preparation evidence; fixtures never measure real model behaviour."""

import json
import socket
from dataclasses import replace
from hashlib import sha256
from itertools import permutations
from pathlib import Path
from typing import cast

import pytest

from journeymap.adapters.event_memory import (
    EVENT_MEMORY_PROMPT_VERSION,
    EVENT_MEMORY_PROTOCOL_VERSION,
    RECENCY_WINDOW_PROTOCOL_VERSION,
    RecencyEventMemory,
)
from journeymap.adapters.llm import (
    PROMPT,
    PROMPT_VERSION,
    LLMController,
    Record,
    event_memory_prompt,
)
from journeymap.adapters.memory import NoMemory
from journeymap.adapters.memory_horizon_prompt import PROFILE, PROFILE_V1, semantic_input
from journeymap.adapters.provider import (
    Provider,
    ProviderIdentity,
    ProviderRequest,
    RawModelResponse,
)
from journeymap.bootstrap import (
    create_alderwick_application,
    create_alderwick_kernel,
    create_memory_horizon_application,
    create_memory_horizon_kernel,
)
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.examples.memory_horizon import HorizonFakeProvider, main
from journeymap.experiments.alderwick import (
    PROTOCOL_VERSION as M8_PROTOCOL,
)
from journeymap.experiments.alderwick import (
    BudgetMonitor,
    TrialPolicy,
    export_trial,
)
from journeymap.experiments.alderwick import (
    audit_export as historical_audit,
)
from journeymap.experiments.alderwick import (
    run_trial as historical_trial,
)
from journeymap.experiments.event_memory import assess_event_memory
from journeymap.experiments.inclusion import record_digest
from journeymap.experiments.memory_horizon import PROTOCOL_VERSION, PROTOCOL_VERSION_V1, run_trial
from journeymap.experiments.memory_horizon_audit import (
    AUDIT_VERSION,
    AUDIT_VERSION_V1,
    assess_trial,
    audit_export,
    obj,
    replay_export,
    rows,
)
from journeymap.scenarios.alderwick.fixture import initial_world
from journeymap.scenarios.alderwick.memory_horizon import (
    SCENARIO_VERSION,
    TARGETS,
    memory_horizon_schedule,
    memory_horizon_world,
)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline preparation must not access network")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)


def controller(k: int = 3, provider: Provider | None = None) -> LLMController:
    return LLMController(
        provider if provider is not None else HorizonFakeProvider(),
        model="memory-horizon-fixture-2",
        event_memory=RecencyEventMemory(k) if k else NoMemory(),
        prompt_profile=PROFILE,
    )


@pytest.fixture(scope="module")
def four_trials() -> dict[int, Record]:
    return {k: run_trial(controller(k), trial_id="same-input") for k in (0, 1, 2, 3)}


def test_scenario_is_separate_and_preserves_existing_topology() -> None:
    original, world = initial_world(), memory_horizon_world()
    assert obj(world["movement"])["routes"] == obj(original["movement"])["routes"]
    assert obj(world["movement"])["locations"] == obj(original["movement"])["locations"]
    assert {
        r["traversal_cost"] for r in rows(list(obj(obj(world["movement"])["routes"]).values()))
    } == {2}
    assert (
        obj(obj(obj(world["movement"])["positions"])["stranger"])["location_id"] == "village-square"
    )
    assert (
        obj(obj(obj(original["movement"])["positions"])["stranger"])["location_id"] == "west-gate"
    )
    assert memory_horizon_schedule().events == ()
    assert memory_horizon_schedule().scenario_version == SCENARIO_VERSION
    kernel = create_memory_horizon_kernel(run_id="scenario")
    kernel.boot()
    try:
        assert kernel.manifest.scenario_version == SCENARIO_VERSION
        assert set(kernel.module_ids) == {"movement", "knowledge"}
        _, research = create_memory_horizon_application(kernel)
        assert research.knowledge_history("stranger") == ()
    finally:
        kernel.close()


def test_four_conditions_share_inputs(four_trials: dict[int, Record]) -> None:
    baseline = four_trials[0].data
    for trial in four_trials.values():
        data, manifest = trial.data, obj(trial.data["manifest"])
        for field in ("initial_state", "initial_knowledge", "observations", "action_requests"):
            assert data[field] == baseline[field]
        for field in (
            "engine_manifest",
            "schedule_digest",
            "scenario_composition",
            "prompt_version",
            "protocol_version",
            "model",
            "parameters",
            "termination_policy",
        ):
            assert manifest[field] == obj(baseline["manifest"])[field]
        assert data["system_event_outcomes"] == []
        assert {e["event_type"] for e in rows(data["events"])} == {"ActorMoved"}
        assert {a["actor_id"] for a in rows(data["activations"])} == {"stranger"}
        assert all(
            cast(str, obj(d["provider_request"])["prompt"]).split("\nINPUT_JSON\n")[0]
            == PROFILE.instructions
            for d in rows(data["decisions"])
        )


@pytest.mark.parametrize("k,expected", [(0, []), (1, [4]), (2, [3, 4]), (3, [2, 3, 4])])
def test_actual_memory_horizon(k: int, expected: list[int], four_trials: dict[int, Record]) -> None:
    data = four_trials[k].data
    decisions, traces = rows(data["decisions"]), rows(data["event_traces"])
    assert decisions[0]["retrieved_count"] == 0
    decision = decisions[4]
    assert decision["simulation_time"] == 8
    assert decision["retrieved_event_trace_ids"] == [
        traces[i - 1]["event_trace_id"] for i in expected
    ]
    selected = [traces[i - 1] for i in expected]
    assert decision["serialized_event_memory"] == canonical_json(cast(list[JsonValue], selected))
    assert decision["event_memory_bytes"] == len(decision["serialized_event_memory"].encode())
    for d in decisions:
        assert d["closed_event_trace_id"] not in cast(
            list[JsonValue], d["retrieved_event_trace_ids"]
        )
        prompt = cast(str, obj(d["provider_request"])["prompt"])
        inputs = json.loads(prompt.split("\nINPUT_JSON\n")[1])
        assert set(inputs) == {"observation", "event_memory"}
        assert inputs == semantic_input(
            {
                "observation": d["observation"],
                "event_memory": json.loads(cast(str, d["serialized_event_memory"])),
            }
        )
        assert "last_action" not in prompt and "last_receipt" not in prompt
    assert (
        "inn-to-village-square" not in decision["serialized_event_memory"]
        if k < 3
        else (
            obj(obj(selected[0]["action_request"])["payload"])["route_id"]
            == "inn-to-village-square"
        )
    )


def test_square_content_has_no_visit_information(four_trials: dict[int, Record]) -> None:
    data = four_trials[3].data
    observations = rows(data["observations"])
    contents = [
        o["content"]
        for o in observations
        if next(
            obj(obj(s["content"])["position"])["location_id"]
            for s in rows(obj(o["content"])["sections"])
            if s["contributor_id"] == "position"
        )
        == "village-square"
    ]
    assert all(content == contents[0] for content in contents)
    forbidden = (
        "visited_inn",
        "visited_bakery",
        "visited_well",
        "visited_places",
        "completed_targets",
        "next_target",
        "last_action",
        "last_receipt",
    )
    for o in observations:
        assert all(word not in canonical_json(o["content"]) for word in forbidden)
        sections = rows(obj(o["content"])["sections"])
        assert {s["contributor_id"] for s in sections} == {"self", "position", "local", "records"}
        assert (
            next(obj(s["content"])["records"] for s in sections if s["module_id"] == "knowledge")
            == []
        )
    assert data["initial_knowledge"] == data["knowledge"] == []


@pytest.mark.parametrize("order", list(permutations(TARGETS)))
def test_six_permutations_complete_then_wait(order: tuple[str, ...]) -> None:
    data = run_trial(controller(provider=HorizonFakeProvider(order)), trial_id="permutation").data
    metrics = obj(data["metrics"])
    assert metrics == {
        "unique_destination_coverage": 3,
        "repeat_destination_count": 0,
        "task_completed": True,
        "decisions_to_completion": 6,
        "decision_attempt_count": 18,
        "engine_submission_count": 18,
        "strict_no_repeat_success": True,
        "post_completion_repeat_count": 0,
    }
    assert obj(rows(data["decisions"])[-1]["action_request"])["action_type"] == "WAIT"
    assert obj(data["manifest"])["simulation_end"] == 24
    assert assess_trial(data)["status"] == "INCLUDED"


@pytest.mark.parametrize(
    "plan,coverage,repeats,completed,at,post",
    [
        (("inn", "inn", "bakery"), 2, 1, False, None, 0),
        (("inn", "bakery"), 2, 0, False, None, 0),
        (("inn", "bakery", "well", "inn"), 3, 1, True, 6, 1),
        ((), 0, 0, False, None, 0),
        (("inn", "inn", "bakery", "well"), 3, 1, True, 8, 0),
    ],
)
def test_metrics_distinguish_repeat_from_incomplete(
    plan: tuple[str, ...], coverage: int, repeats: int, completed: bool, at: int | None, post: int
) -> None:
    data = run_trial(controller(provider=HorizonFakeProvider(plan)), trial_id="metric-case").data
    metrics = obj(data["metrics"])
    assert (
        metrics["unique_destination_coverage"],
        metrics["repeat_destination_count"],
        metrics["task_completed"],
        metrics["decisions_to_completion"],
        metrics["post_completion_repeat_count"],
    ) == (coverage, repeats, completed, at, post)
    assert metrics["strict_no_repeat_success"] is (completed and repeats == 0)
    assert assess_trial(data)["status"] == "INCLUDED"


def test_missing_final_square_return() -> None:
    data = run_trial(controller(), trial_id="no-return", policy=TrialPolicy(max_decisions=5)).data
    metrics, manifest = obj(data["metrics"]), obj(data["manifest"])
    assert metrics["unique_destination_coverage"] == 3
    assert metrics["task_completed"] is False and metrics["decisions_to_completion"] is None
    assert manifest["status"] == "TERMINATED" and manifest["stop_reason"] == "MAX_DECISIONS"
    assert manifest["simulation_end"] == 10
    assert assess_trial(data)["status"] == "INCLUDED"


class FailureProvider:
    """Deterministic harness failures; no delivered progress cues are needed."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: ProviderRequest) -> RawModelResponse:
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError()
        if self.calls == 2:
            return RawModelResponse("invalid JSON")
        return RawModelResponse('{"action_type":"WAIT","payload":{"duration":1}}')


def test_failure_attempts_do_not_create_memory_or_submissions() -> None:
    data = run_trial(
        controller(provider=FailureProvider()),
        trial_id="failure",
        policy=TrialPolicy(max_decisions=3),
    ).data
    metrics, decisions = obj(data["metrics"]), rows(data["decisions"])
    assert metrics["decision_attempt_count"] == 3 and metrics["engine_submission_count"] == 1
    assert len(rows(data["event_traces"])) == 1
    assert [d["retrieved_count"] for d in decisions] == [0, 0, 0]
    assert [d["opportunity_attempt"] for d in decisions] == [1, 2, 3]
    assert len({d["decision_opportunity_id"] for d in decisions}) == 1
    assert assess_trial(data)["status"] == "INCLUDED"


class ConstantProvider:
    def __init__(self, output: str) -> None:
        self.output = output

    def generate(self, request: ProviderRequest) -> RawModelResponse:
        return RawModelResponse(self.output)


@pytest.mark.parametrize(
    "output,stop,traces",
    [
        ('{"action_type":"WAIT","payload":{"duration":25}}', "HORIZON_ACTION", 0),
        ('{"action_type":"REST","payload":{"duration":1}}', "UNSUPPORTED_ACTION", 0),
        ('{"action_type":"MOVE","payload":{"route_id":"absent"}}', "CONSECUTIVE_FAILURES", 3),
        ('{"action_type":"WAIT","payload":{"duration":true}}', "CONSECUTIVE_FAILURES", 0),
    ],
)
def test_precheck_and_rejection_semantics(output: str, stop: str, traces: int) -> None:
    data = run_trial(controller(provider=ConstantProvider(output)), trial_id="precheck").data
    manifest = obj(data["manifest"])
    assert manifest["status"] == "TERMINATED" and manifest["stop_reason"] == stop
    assert manifest["simulation_end"] == 0 and len(rows(data["event_traces"])) == traces
    assert obj(data["metrics"])["engine_submission_count"] == traces
    if traces:
        assert [d["retrieved_count"] for d in rows(data["decisions"])] == [0, 1, 2]
    assert assess_trial(data)["status"] == "INCLUDED"


def test_observation_overflow_terminates_without_call() -> None:
    data = run_trial(
        controller(), trial_id="overflow", monitor=BudgetMonitor(max_content_bytes=1)
    ).data
    assert obj(data["manifest"])["stop_reason"] == "OBSERVATION_UNAVAILABLE"
    assert obj(data["manifest"])["provider_calls"] == 0
    assert data["event_traces"] == data["action_requests"] == []
    assert assess_trial(data)["status"] == "INCLUDED"


@pytest.mark.parametrize("k", [0, 1, 2, 3])
def test_export_replay_audit_without_provider(
    k: int, four_trials: dict[int, Record], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "export"
    export_trial(four_trials[k], directory)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("replay/audit must not create a Controller or invoke a Provider")

    monkeypatch.setattr(LLMController, "__init__", forbidden)
    monkeypatch.setattr(HorizonFakeProvider, "generate", forbidden)
    assert replay_export(directory).final_simulation_time == 24
    audit = audit_export(directory)
    assert audit["status"] == "INCLUDED" and audit["stored_inclusion_matches"] is True
    assert audit["policy_version"] == AUDIT_VERSION
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
    assert historical_audit(directory)["status"] == "EXCLUDED"


@pytest.mark.parametrize(
    "where,key,value",
    [
        ("manifest", "scenario_composition", "social-resources-24h-1"),
        ("manifest", "prompt_version", "alderwick-event-memory-decision-1"),
        ("manifest", "protocol_version", RECENCY_WINDOW_PROTOCOL_VERSION),
        ("manifest", "memory_policy", "No Event Memory"),
        ("manifest", "memory_policy_version", "recency-event-memory-k2-1"),
        ("manifest", "schema_version", 2),
        ("manifest", "simulation_end", 12),
        ("manifest", "npc_activation_cycle", ["marta"]),
        ("decision", "retrieved_event_trace_ids", []),
        ("decision", "retrieved_count", 0),
        ("decision", "serialized_event_memory", "[]"),
        ("decision", "event_memory_bytes", 2),
        ("decision", "prompt_sha256", "0" * 64),
        ("metrics", "unique_destination_coverage", 0),
        ("metrics", "repeat_destination_count", 1),
        ("metrics", "task_completed", False),
        ("metrics", "decisions_to_completion", 5),
        ("metrics", "decision_attempt_count", 6),
        ("metrics", "engine_submission_count", 6),
        ("metrics", "strict_no_repeat_success", False),
        ("metrics", "post_completion_repeat_count", 1),
    ],
)
def test_resealed_tampering_rejected(
    where: str, key: str, value: JsonValue, four_trials: dict[int, Record]
) -> None:
    data = four_trials[3].data
    target = rows(data["decisions"])[4] if where == "decision" else obj(data[where])
    target[key] = value
    obj(data["manifest"])["record_sha256"] = record_digest(data)
    assert assess_trial(data)["status"] == "EXCLUDED"


@pytest.mark.parametrize(
    "tamper", ["prompt", "observation", "nested", "receipt", "initial", "schedule"]
)
def test_deeper_resealed_corruption(tamper: str, four_trials: dict[int, Record]) -> None:
    data = four_trials[3].data
    decision = rows(data["decisions"])[4]
    if tamper == "prompt":
        provider_request = obj(decision["provider_request"])
        provider_request["prompt"] = "Visit Inn next.\n" + cast(str, provider_request["prompt"])
        decision["prompt_sha256"] = sha256(
            cast(str, provider_request["prompt"]).encode()
        ).hexdigest()
    elif tamper == "observation":
        obj(obj(decision["observation"])["content"])["visited_places"] = ["inn", "bakery"]
    elif tamper == "nested":
        obj(obj(rows(data["event_traces"])[2]["observation"])["content"])["last_action"] = "inn"
    elif tamper == "receipt":
        obj(decision["receipt"])["status"] = "REJECTED"
    elif tamper == "initial":
        obj(data["initial_state"])["visited_places"] = []
    else:
        obj(obj(data["replay_input"])["schedule"])["scenario_version"] = "1"
    obj(data["manifest"])["record_sha256"] = record_digest(data)
    assert assess_trial(data)["status"] == "EXCLUDED"


def test_fake_cursor_ignores_memory_and_condition(four_trials: dict[int, Record]) -> None:
    baseline = rows(four_trials[0].data["decisions"])
    for k in (0, 1, 2, 3):
        fake = HorizonFakeProvider()
        for decision, expected in zip(
            rows(four_trials[k].data["decisions"]), baseline, strict=True
        ):
            request = ProviderRequest(**obj(decision["provider_request"]))  # type: ignore[arg-type]
            inputs = json.loads(request.prompt.split("\nINPUT_JSON\n")[1])
            # No history/condition/provenance is available even as a fallback.
            request = replace(
                request, prompt=PROFILE.render({"observation": inputs["observation"]})
            )
            assert fake.generate(request).text == expected["raw_output"]


def test_historical_prompt_bytes_and_default_profiles() -> None:
    assert (
        sha256(PROMPT.encode()).hexdigest()
        == "95c5a4b5d8e1bca0e9f173e486f27b754ced0c017aa8f3c1aef885061bf2fa7d"
    )
    assert (
        sha256(event_memory_prompt({}).encode()).hexdigest()
        == "980d67f933c38e5bc8970b9b0c1c28382ee914f9c55bc81bdab2e35d0b8e5815"
    )
    fake = ConstantProvider('{"action_type":"WAIT","payload":{"duration":1}}')
    legacy = LLMController(fake)
    event = LLMController(fake, event_memory=NoMemory())
    assert legacy.prompt_version == PROMPT_VERSION == "alderwick-decision-2"
    assert event.prompt_version == EVENT_MEMORY_PROMPT_VERSION
    assert legacy.prompt_profile is event.prompt_profile is None
    kernel = create_alderwick_kernel(social=True, resources=True, experiment=True)
    kernel.boot()
    try:
        app, _ = create_alderwick_application(kernel, social=True, resources=True, experiment=True)
        observation = app.game_for("stranger").observe()
        assert "last_action" in canonical_json(observation.content)
        for c in (legacy, event):
            c.decide(observation)
            assert c.last_decision is not None
            prompt = cast(str, obj(c.last_decision.data["provider_request"])["prompt"])
            inputs = json.loads(prompt.split("\nINPUT_JSON\n")[1])
            expected = (
                event_memory_prompt(inputs)
                if c is event
                else PROMPT + "\nINPUT_JSON\n" + canonical_json(inputs)
            )
            assert prompt == expected
    finally:
        kernel.close()


@pytest.mark.parametrize(
    "protocol", [M8_PROTOCOL, EVENT_MEMORY_PROTOCOL_VERSION, RECENCY_WINDOW_PROTOCOL_VERSION]
)
def test_old_runner_rejects_new_profile(protocol: str) -> None:
    with pytest.raises(ValueError, match="default prompt"):
        historical_trial(controller(), trial_id="wrong-runner", protocol_version=protocol)


@pytest.mark.parametrize("bad", ["legacy", "missing-profile", "changed-profile", "live", "k4"])
def test_new_runner_rejects_invalid_configuration_before_provider(bad: str) -> None:
    class NeverProvider:
        @property
        def identity(self) -> ProviderIdentity:
            return (
                ProviderIdentity("openai", "test", "live")
                if bad == "live"
                else ProviderIdentity("fake", "test")
            )

        def generate(self, request: ProviderRequest) -> RawModelResponse:
            raise AssertionError("must reject before provider")

    if bad == "legacy":
        with pytest.raises(ValueError):
            LLMController(NeverProvider(), prompt_profile=PROFILE)
        return
    c = LLMController(
        NeverProvider(),
        event_memory=RecencyEventMemory(4 if bad == "k4" else 1),
        prompt_profile=None
        if bad == "missing-profile"
        else replace(PROFILE, instructions="changed")
        if bad == "changed-profile"
        else PROFILE,
    )
    with pytest.raises(ValueError):
        run_trial(c, trial_id="invalid")


def test_new_export_not_admitted_by_old_memory_audit(four_trials: dict[int, Record]) -> None:
    assert assess_event_memory(four_trials[3].data)["status"] == "EXCLUDED"
    assert obj(four_trials[3].data["manifest"])["protocol_version"] == PROTOCOL_VERSION


def test_cli_has_no_live_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["memory_horizon", "--live"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


@pytest.mark.parametrize("bound", ["max_decisions", "max_provider_calls"])
def test_decision_and_call_bounds(bound: str) -> None:
    policy = TrialPolicy(**{bound: 1})
    data = run_trial(controller(), trial_id="bound", policy=policy).data
    assert obj(data["manifest"])["stop_reason"] == bound.upper()
    assert obj(data["metrics"])["decision_attempt_count"] == 1
    assert assess_trial(data)["status"] == "INCLUDED"


def test_tick_23_move_is_not_submitted() -> None:
    class LateMove:
        def __init__(self) -> None:
            self.calls = 0

        def generate(self, request: ProviderRequest) -> RawModelResponse:
            self.calls += 1
            return RawModelResponse(
                '{"action_type":"WAIT","payload":{"duration":1}}'
                if self.calls <= 23
                else '{"action_type":"MOVE","payload":{"route_id":"village-square-to-inn"}}'
            )

    data = run_trial(controller(provider=LateMove()), trial_id="late").data
    assert obj(data["manifest"])["simulation_end"] == 23
    assert obj(data["manifest"])["stop_reason"] == "HORIZON_ACTION"
    assert len(rows(data["event_traces"])) == 23
    assert obj(data["metrics"])["engine_submission_count"] == 23
    assert assess_trial(data)["status"] == "INCLUDED"


def test_wall_timeout_before_first_call(monkeypatch: pytest.MonkeyPatch) -> None:
    import journeymap.experiments.memory_horizon as experiment

    times = iter((0.0, 2.0, 3.0))
    monkeypatch.setattr(experiment, "monotonic", lambda: next(times))
    data = run_trial(controller(), trial_id="wall", policy=TrialPolicy(wall_timeout_seconds=1)).data
    assert obj(data["manifest"])["stop_reason"] == "WALL_TIMEOUT"
    assert obj(data["manifest"])["provider_calls"] == 0
    assert assess_trial(data)["status"] == "INCLUDED"


def test_resealed_file_metrics_and_missing_inventory(
    four_trials: dict[int, Record], tmp_path: Path
) -> None:
    data = four_trials[3].data
    obj(data["metrics"])["task_completed"] = False
    obj(data["manifest"])["record_sha256"] = record_digest(data)
    directory = tmp_path / "tampered"
    export_trial(Record.capture(data), directory)
    assert audit_export(directory)["status"] == "EXCLUDED"
    (directory / "events.jsonl").unlink()
    assert audit_export(directory)["reasons"] == ["EXPORT_INTEGRITY"]


def test_fresh_controller_required(four_trials: dict[int, Record]) -> None:
    c = controller()
    run_trial(c, trial_id="first")
    with pytest.raises(ValueError, match="fresh"):
        run_trial(c, trial_id="second")


# Independent schema oracle: do not use the production projection here.
def semantic_experience(trace: JsonObject) -> JsonObject:
    action, receipt = obj(trace["action_request"]), obj(trace["actor_visible_receipt"])
    return {
        "observation": {"content": obj(trace["observation"])["content"]},
        "action": {"action_type": action["action_type"], "payload": action["payload"]},
        "receipt": {"status": receipt["status"], "reason_code": receipt["reason_code"]},
    }


def strings_in(value: JsonValue) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(strings_in(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(*(strings_in(v) for v in value))
    return {value} if isinstance(value, str) else set()


@pytest.mark.parametrize("k", [0, 1, 2, 3])
def test_v2_exact_payload_and_recursive_provenance_exclusion(
    k: int, four_trials: dict[int, Record]
) -> None:
    data = four_trials[k].data
    traces = rows(data["event_traces"])
    forbidden = {
        "observation_id",
        "run_id",
        "observation_sequence",
        "simulation_time",
        "content_digest",
        "event_trace_id",
        "sequence",
        "decision_opportunity_id",
        "opportunity_sequence",
        "attempt",
        "opened_at",
        "closed_at",
        "memory_eligible",
        "action_request_id",
        "based_on_observation_id",
        "submitted_at",
        "correlation_id",
        "started_at",
        "resolved_at",
        "actor_id",
        "schema_version",
        "last_action",
        "last_receipt",
        "memory_policy",
        "memory_policy_version",
        "k",
        "condition",
        "visited_places",
        "completed_targets",
        "next_target",
    }
    # Opaque provenance values must not survive under renamed keys either.
    for trace in traces:
        for record in (
            trace,
            obj(trace["observation"]),
            obj(trace["action_request"]),
            obj(trace["actor_visible_receipt"]),
        ):
            forbidden.update(
                value
                for key, value in record.items()
                if key in forbidden and isinstance(value, str) and key != "actor_id"
            )
    for index, decision in enumerate(rows(data["decisions"])):
        prompt = cast(str, obj(decision["provider_request"])["prompt"])
        inputs = json.loads(prompt.split("\nINPUT_JSON\n")[1])
        selected = traces[max(0, index - k) : index] if k else []
        expected: JsonObject = {
            "observation": {"content": obj(decision["observation"])["content"]},
            "event_memory": [semantic_experience(t) for t in selected],
        }
        assert inputs == expected == decision["model_visible_input"]
        serialized = canonical_json(expected)
        assert decision["model_visible_input_canonical"] == serialized
        assert decision["model_visible_input_bytes"] == len(serialized.encode("utf-8"))
        assert prompt == PROFILE.instructions + "\nINPUT_JSON\n" + serialized
        assert decision["prompt_sha256"] == sha256(prompt.encode()).hexdigest()
        assert not (strings_in(inputs) & forbidden)
    assert "tick 0" not in PROFILE.instructions and "tick 24" not in PROFILE.instructions
    assert "simulation_time" not in PROFILE.instructions


def test_v2_square_bytes_and_only_selected_history_differs(four_trials: dict[int, Record]) -> None:
    baseline = rows(four_trials[0].data["decisions"])
    square = canonical_json(obj(baseline[0]["model_visible_input"])["observation"]).encode()
    for trial in four_trials.values():
        for index, (decision, reference) in enumerate(
            zip(rows(trial.data["decisions"]), baseline, strict=True)
        ):
            inputs, other = (
                obj(decision["model_visible_input"]),
                obj(reference["model_visible_input"]),
            )
            assert set(inputs) == {"observation", "event_memory"}
            assert (
                canonical_json(inputs["observation"]).encode()
                == canonical_json(other["observation"]).encode()
            )
            if index in (0, 2, 4) or index >= 6:
                assert canonical_json(inputs["observation"]).encode() == square


@pytest.mark.parametrize("k,indices", [(0, []), (1, [3]), (2, [2, 3]), (3, [1, 2, 3])])
def test_v2_e1_e4_semantic_window(
    k: int, indices: list[int], four_trials: dict[int, Record]
) -> None:
    data = four_trials[k].data
    decision, traces = rows(data["decisions"])[4], rows(data["event_traces"])
    assert obj(decision["model_visible_input"])["event_memory"] == [
        semantic_experience(traces[i]) for i in indices
    ]
    assert decision["retrieved_event_trace_ids"] == [traces[i]["event_trace_id"] for i in indices]


def test_v2_projection_is_pure_detached_and_retains_raw_archive(
    four_trials: dict[int, Record],
) -> None:
    data = four_trials[3].data
    decision = rows(data["decisions"])[4]
    raw: JsonObject = {
        "observation": decision["observation"],
        "event_memory": json.loads(cast(str, decision["serialized_event_memory"])),
    }
    before = canonical_json(raw)
    projected = semantic_input(raw)
    assert canonical_json(raw) == before
    assert projected == semantic_input(raw)
    obj(obj(projected["observation"])["content"])["mutation"] = True
    obj(rows(projected["event_memory"])[0]["action"])["payload"] = {"mutated": True}
    assert canonical_json(raw) == before
    for trace, d in zip(rows(data["event_traces"]), rows(data["decisions"]), strict=True):
        assert trace["observation"] == d["observation"]
        assert trace["action_request"] == d["action_request"]
        assert trace["actor_visible_receipt"] == d["receipt"]
        assert set(trace) == {
            "event_trace_id",
            "sequence",
            "run_id",
            "actor_id",
            "decision_opportunity_id",
            "opportunity_sequence",
            "attempt",
            "observation",
            "action_request",
            "actor_visible_receipt",
            "opened_at",
            "closed_at",
            "memory_eligible",
            "schema_version",
        }
        assert set(obj(trace["observation"])) == {
            "observation_id",
            "run_id",
            "actor_id",
            "observation_sequence",
            "simulation_time",
            "schema_version",
            "content_digest",
            "content",
        }
    assert data["observations"] == [d["observation"] for d in rows(data["decisions"])]


@pytest.mark.parametrize(
    "tamper", ["projection", "canonical", "bytes", "coherent-prompt", "leak", "boolean-alias"]
)
def test_v2_resealed_projection_tamper(tamper: str, four_trials: dict[int, Record]) -> None:
    data = four_trials[3].data
    decision = rows(data["decisions"])[8 if tamper == "boolean-alias" else 4]
    inputs = obj(decision["model_visible_input"])
    if tamper == "boolean-alias":
        history = rows(inputs["event_memory"])
        wait = next(obj(t["action"]) for t in history if obj(t["action"])["action_type"] == "WAIT")
        obj(wait["payload"])["duration"] = True
    elif tamper == "bytes":
        decision["model_visible_input_bytes"] = 1
    elif tamper == "canonical":
        decision["model_visible_input_canonical"] = "{}"
    else:
        if tamper == "leak":
            obj(inputs["observation"])["simulation_time"] = 8
        else:
            obj(rows(inputs["event_memory"])[0]["action"])["payload"] = {"route_id": "invented"}
        if tamper in ("coherent-prompt", "leak"):
            serialized = canonical_json(inputs)
            decision["model_visible_input_canonical"] = serialized
            decision["model_visible_input_bytes"] = len(serialized.encode())
            prompt = PROFILE.render(inputs)
            obj(decision["provider_request"])["prompt"] = prompt
            decision["prompt_sha256"] = sha256(prompt.encode()).hexdigest()
    obj(data["manifest"])["record_sha256"] = record_digest(data)
    assert assess_trial(data)["status"] == "EXCLUDED"


@pytest.mark.parametrize("duration", [2, 12, 24])
def test_v2_nonfixed_wait_is_rejected_without_clipping(duration: int) -> None:
    data = run_trial(
        controller(
            provider=ConstantProvider(
                json.dumps({"action_type": "WAIT", "payload": {"duration": duration}})
            )
        ),
        trial_id="fixed-wait",
    ).data
    assert obj(data["manifest"])["stop_reason"] == "WAIT_DURATION"
    assert obj(data["manifest"])["simulation_end"] == 0
    assert data["action_requests"] == data["event_traces"] == []
    assert assess_trial(data)["status"] == "INCLUDED"


def test_v2_wait_at_tick_23_is_safe_and_constant() -> None:
    data = run_trial(
        controller(provider=ConstantProvider('{"action_type":"WAIT","payload":{"duration":1}}')),
        trial_id="wait-only",
    ).data
    decisions = rows(data["decisions"])
    assert len(decisions) == 24
    assert decisions[-1]["simulation_time"] == 23
    assert obj(data["manifest"])["simulation_end"] == 24
    assert {canonical_json(obj(d["model_visible_input"])["observation"]) for d in decisions} == {
        canonical_json(obj(decisions[0]["model_visible_input"])["observation"])
    }
    assert assess_trial(data)["status"] == "INCLUDED"


def test_v2_public_rejection_experience() -> None:
    data = run_trial(
        controller(
            provider=ConstantProvider('{"action_type":"MOVE","payload":{"route_id":"absent"}}')
        ),
        trial_id="rejection-projection",
    ).data
    trace = rows(data["event_traces"])[0]
    experience = rows(obj(rows(data["decisions"])[1]["model_visible_input"])["event_memory"])[0]
    assert experience == semantic_experience(trace)
    assert obj(experience["receipt"])["status"] == "REJECTED"
    assert obj(experience["receipt"])["reason_code"] is not None


def test_v1_protocol_prompt_and_audit_remain_readable(tmp_path: Path) -> None:
    c = LLMController(
        ConstantProvider('{"action_type":"WAIT","payload":{"duration":12}}'),
        event_memory=RecencyEventMemory(3),
        prompt_profile=PROFILE_V1,
    )
    trial = run_trial(c, trial_id="v1-regression", protocol_version=PROTOCOL_VERSION_V1)
    data = trial.data
    decision = rows(data["decisions"])[0]
    manifest = obj(data["manifest"])
    assert manifest["prompt_version"] == PROFILE_V1.version == "alderwick-memory-horizon-decision-1"
    assert manifest["simulation_end"] == 24 and manifest["decisions"] == 2
    assert sha256(PROFILE_V1.instructions.encode()).hexdigest() == (
        "b428f687dd9cf4faa54dba7436c023b9b2aa3d334f88d51df409fd71c166d7ed"
    )
    assert "model_visible_input" not in decision
    assert PROFILE_V1.input_projection is None
    assert obj(decision["provider_request"])["prompt"] == PROFILE_V1.render(
        {"observation": decision["observation"], "event_memory": []}
    )
    second = rows(data["decisions"])[1]
    assert obj(second["provider_request"])["prompt"] == PROFILE_V1.render(
        {
            "observation": second["observation"],
            "event_memory": cast(list[JsonValue], data["event_traces"])[:1],
        }
    )
    assert assess_trial(data) == {
        "status": "INCLUDED",
        "policy_version": AUDIT_VERSION_V1,
        "reasons": [],
    }
    directory = tmp_path / "v1"
    export_trial(trial, directory)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    assert replay_export(directory).final_simulation_time == 24
    assert audit_export(directory)["policy_version"] == AUDIT_VERSION_V1
    assert audit_export(directory)["stored_inclusion_matches"] is True
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
