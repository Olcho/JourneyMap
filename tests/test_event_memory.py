"""Phase 1 offline correctness; all Provider outputs are deterministic fixtures."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from journeymap.adapters.event_memory import (
    EVENT_MEMORY_PROTOCOL_VERSION,
    EventMemoryContext,
    EventTraceArchive,
    RecencyEventMemory,
)
from journeymap.adapters.llm import LLMController, Record
from journeymap.adapters.memory import NoMemory
from journeymap.adapters.provider import ProviderRequest, RawModelResponse
from journeymap.application.turns import ControllerTurnResult, run_controller_turn
from journeymap.bootstrap import create_alderwick_application, create_alderwick_kernel
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.core.controller import GamePort
from journeymap.core.events import EventBus, EventEnvelope
from journeymap.core.handlers import ActionRequest
from journeymap.core.observations import Observation
from journeymap.examples.alderwick_llm import ProtocolFakeProvider
from journeymap.experiments.alderwick import (
    TrialPolicy,
    audit_export,
    export_trial,
    replay_export,
    run_trial,
)
from journeymap.experiments.event_memory import assess_event_memory
from journeymap.experiments.inclusion import record_digest
from journeymap.scenarios.alderwick.fixture import scenario_schedule


class FakeProvider:
    def __init__(self, outputs: list[str | Exception | RawModelResponse] | None = None) -> None:
        self.outputs = outputs or ['{"action_type":"WAIT","payload":{"duration":1}}'] * 4
        self.inputs: list[JsonObject] = []

    def generate(self, request: ProviderRequest) -> RawModelResponse:
        self.inputs.append(json.loads(request.prompt.split("\nINPUT_JSON\n")[1]))
        output = self.outputs[len(self.inputs) - 1]
        if isinstance(output, Exception):
            raise output
        if isinstance(output, RawModelResponse):
            return output
        return RawModelResponse(output)


@contextmanager
def game_fixture() -> Iterator[GamePort]:
    kernel = create_alderwick_kernel(social=True, resources=True, experiment=True)
    kernel.boot()
    try:
        app, _ = create_alderwick_application(kernel, social=True, resources=True, experiment=True)
        yield app.game_for("stranger")
    finally:
        kernel.close()


def complete(game: GamePort, controller: LLMController) -> ControllerTurnResult:
    turn = run_controller_turn(game, controller)
    if turn.receipt is not None:
        assert turn.observation is not None and turn.request is not None
        controller.close_event_trace(
            turn.observation, turn.request, turn.receipt, engine_submitted=True
        )
    return turn


def test_first_offline_recency_correctness() -> None:
    runs = []
    for _ in range(2):
        provider = FakeProvider()
        controller = LLMController(provider, event_memory=RecencyEventMemory())
        with game_fixture() as game:
            turns = [complete(game, controller) for _ in range(3)]
        traces = controller.event_traces
        assert len(traces) == 3
        assert provider.inputs[0]["event_memory"] == []
        assert provider.inputs[1]["event_memory"] == [traces[0].to_json()]
        assert provider.inputs[2]["event_memory"] == [traces[1].to_json()]
        for index, trace in enumerate(traces):
            assert trace.sequence == trace.opportunity_sequence == index + 1
            assert trace.observation == turns[index].observation
            assert trace.actor_visible_receipt == turns[index].receipt
            assert trace.closed_at == index + 1
            assert trace.run_id == trace.observation.run_id
            assert trace.actor_id == "stranger"
            assert trace.to_json() not in cast(
                list[JsonObject], provider.inputs[index]["event_memory"]
            )
        runs.append([trace.to_json() for trace in traces])
    assert runs[0] == runs[1]


def obj(value: JsonValue) -> JsonObject:
    assert isinstance(value, dict)
    return value


def rows(value: JsonValue) -> list[JsonObject]:
    assert isinstance(value, list)
    return [obj(item) for item in value]


def trial_with(provider: FakeProvider) -> Record:
    return run_trial(
        LLMController(provider, event_memory=RecencyEventMemory()),
        trial_id="event-failure",
        protocol_version=EVENT_MEMORY_PROTOCOL_VERSION,
        policy=TrialPolicy(max_decisions=len(provider.outputs)),
    )


@pytest.mark.parametrize(
    "output",
    [
        TimeoutError("private transport detail"),
        "not json",
        '{"action_type":"WAIT","payload":{"duration":true}}',
        '{"action_type":"WAIT","payload":{"duration":1},"actor_id":"hugh"}',
        RawModelResponse("{}", {"refusal": True}),
        RawModelResponse("{}", {"status": "incomplete"}),
        RawModelResponse("{}", {"provider": "openai", "model": "wrong-model"}),
    ],
)
def test_failures_are_research_attempts_without_events(
    output: str | Exception | RawModelResponse,
) -> None:
    provider = FakeProvider([output, output])
    data = trial_with(provider).data
    decisions = rows(data["decisions"])
    assert len(decisions) == len(rows(data["observations"])) == 2
    assert data["event_traces"] == data["action_requests"] == []
    assert [d["opportunity_attempt"] for d in decisions] == [1, 2]
    assert decisions[0]["decision_opportunity_id"] == decisions[1]["decision_opportunity_id"]
    assert all(d["failure"] is not None and d["closed_event_trace_id"] is None for d in decisions)
    assert all(item["event_memory"] == [] for item in provider.inputs)
    assert assess_event_memory(data)["status"] == "INCLUDED"


def test_failure_then_success_same_opportunity_closes_once() -> None:
    provider = FakeProvider(
        [TimeoutError(), "broken", '{"action_type":"WAIT","payload":{"duration":1}}']
    )
    data = trial_with(provider).data
    decisions = rows(data["decisions"])
    traces = rows(data["event_traces"])
    assert [d["opportunity_attempt"] for d in decisions] == [1, 2, 3]
    assert len({d["decision_opportunity_id"] for d in decisions}) == 1
    assert len(traces) == 1 and traces[0]["attempt"] == 3
    assert traces[0]["observation"] == decisions[2]["observation"]


def test_rejected_receipt_is_retrieved_at_same_tick_with_changed_observation() -> None:
    provider = FakeProvider(['{"action_type":"MOVE","payload":{"route_id":"absent"}}'] * 3)
    data = trial_with(provider).data
    traces, decisions = rows(data["event_traces"]), rows(data["decisions"])
    assert len(traces) == 3
    assert all(obj(t["actor_visible_receipt"])["status"] == "REJECTED" for t in traces)
    assert all(t["opened_at"] == t["closed_at"] == 0 for t in traces)
    assert [d["opportunity_sequence"] for d in decisions] == [1, 2, 3]
    assert (
        obj(decisions[0]["observation"])["content_digest"]
        != obj(decisions[1]["observation"])["content_digest"]
    )
    assert provider.inputs[1]["event_memory"] == [traces[0]]
    assert provider.inputs[2]["event_memory"] == [traces[1]]
    assert assess_event_memory(data)["status"] == "INCLUDED"


def test_horizon_no_submit_has_record_but_no_event() -> None:
    data = trial_with(FakeProvider(['{"action_type":"WAIT","payload":{"duration":25}}'])).data
    decision = rows(data["decisions"])[0]
    assert decision["protocol_failure"] == "HORIZON_ACTION"
    assert decision["action_request"] is not None and decision["parser_outcome"] == "ACCEPTED"
    assert data["event_traces"] == data["action_requests"] == data["action_traces"] == []
    assert assess_event_memory(data)["status"] == "INCLUDED"


@pytest.mark.parametrize("changed", ["actor", "run", "future", "order", "eligible", "self"])
def test_event_context_rejects_scope_future_self_or_order_contamination(changed: str) -> None:
    controller = LLMController(FakeProvider(), event_memory=RecencyEventMemory())
    with game_fixture() as game:
        complete(game, controller)
        observation = game.observe()
    trace = controller.event_traces[0]
    altered = {
        "actor": replace(trace, actor_id="hugh"),
        "run": replace(trace, run_id="other-run"),
        "future": replace(trace, closed_at=999),
        "order": replace(trace, sequence=0),
        "eligible": replace(trace, memory_eligible=False),
        "self": replace(trace, opportunity_sequence=2),
    }[changed]
    with pytest.raises(ValueError):
        EventMemoryContext(observation, "next", 2, 1, (altered,))


@pytest.mark.parametrize("scope", ["actor", "run"])
def test_controller_reuse_fails_closed_across_scope(scope: str) -> None:
    provider = FakeProvider()
    controller = LLMController(provider, event_memory=RecencyEventMemory())
    controller.decide(Observation("r", "a", 1, 0, {"sections": []}))
    with pytest.raises(RuntimeError):
        controller.decide(
            Observation(
                "other" if scope == "run" else "r",
                "b" if scope == "actor" else "a",
                2,
                0,
                {"sections": []},
            )
        )
    assert len(provider.inputs) == 1 and controller.event_traces == ()


def test_archive_immutable_and_exact_receipt_retry_idempotent() -> None:
    controller = LLMController(FakeProvider(), event_memory=RecencyEventMemory())
    with game_fixture() as game:
        turn = complete(game, controller)
        assert (
            turn.request is not None and turn.observation is not None and turn.receipt is not None
        )
        repeated = game.submit(turn.request)
        trace = controller.close_event_trace(
            turn.observation, turn.request, repeated, engine_submitted=True
        )
    assert len(controller.event_traces) == 1
    snapshot = trace.to_json()
    trace.action_request.payload["duration"] = 99
    trace.observation.content["sections"] = []
    obj(trace.to_json()["action_request"])["payload"] = {"duration": 999}
    assert trace.to_json() == snapshot
    with pytest.raises(FrozenInstanceError):
        trace.closed_at = 999  # type: ignore[misc]
    with pytest.raises(ValueError):
        controller.close_event_trace(
            turn.observation,
            turn.request,
            replace(repeated, actor_id="hugh"),
            engine_submitted=True,
        )


def test_changed_information_opens_opportunity_even_without_prior_closure() -> None:
    archive = EventTraceArchive()
    first = archive.begin(Observation("r", "a", 1, 0, {"sections": []}))
    retry = archive.begin(Observation("r", "a", 2, 0, {"sections": []}))
    changed = archive.begin(
        Observation(
            "r",
            "a",
            3,
            0,
            {
                "sections": [
                    {"module_id": "visible", "contributor_id": "new", "content": {"fact": True}}
                ]
            },
        )
    )
    assert first.decision_opportunity_id == retry.decision_opportunity_id
    assert changed.opportunity_sequence == 2 and changed.attempt == 1
    assert archive.traces == ()


@pytest.fixture(scope="module", params=[NoMemory, RecencyEventMemory])
def full_trial(request: pytest.FixtureRequest) -> Record:
    return run_trial(
        LLMController(ProtocolFakeProvider(), model="fixture", event_memory=request.param()),
        trial_id="event-full-" + request.param.__name__,
        protocol_version=EVENT_MEMORY_PROTOCOL_VERSION,
    )


def test_full_offline_trial_provenance_and_provider_free_replay(
    full_trial: Record, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = full_trial.data
    manifest = obj(data["manifest"])
    traces, decisions = rows(data["event_traces"]), rows(data["decisions"])
    assert manifest["simulation_end"] == 24 and manifest["replay_equal"] is True
    assert traces and len(traces) == len(decisions)
    for i, decision in enumerate(decisions):
        selected = (
            [] if i == 0 or manifest["memory_policy"] == NoMemory.policy_id else [traces[i - 1]]
        )
        serialized = canonical_json(cast(JsonValue, selected))
        assert decision["serialized_event_memory"] == serialized
        assert decision["event_memory_bytes"] == len(serialized.encode("utf-8"))
        assert decision["retrieved_count"] == len(selected)
        assert decision["retrieved_event_trace_ids"] == [t["event_trace_id"] for t in selected]
        prompt = cast(str, obj(decision["provider_request"])["prompt"])
        assert decision["prompt_sha256"] == sha256(prompt.encode("utf-8")).hexdigest()
        assert set(json.loads(prompt.split("\nINPUT_JSON\n")[1])) == {"observation", "event_memory"}
        for secret in (
            "state_digest_after",
            "world_result",
            "pending_scheduled_events",
            "provider_metadata",
            "transition_id",
            "rng_snapshot",
        ):
            assert secret not in prompt
    assert assess_event_memory(data)["status"] == "INCLUDED"
    directory = tmp_path / "export"
    export_trial(full_trial, directory)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("replay/audit cannot call a provider")

    monkeypatch.setattr(ProtocolFakeProvider, "generate", forbidden)
    monkeypatch.setattr(LLMController, "decide", forbidden)
    assert replay_export(directory).final_simulation_time == 24
    assert audit_export(directory)["status"] == "INCLUDED"
    assert (directory / "event_traces.jsonl").is_file()


@pytest.mark.parametrize(
    "mutation",
    [
        "trace",
        "receipt",
        "count",
        "ids",
        "bytes",
        "serialized",
        "prompt",
        "lineage",
        "submission",
        "inventory",
        "policy",
    ],
)
def test_resealed_corruption_is_rejected(full_trial: Record, mutation: str) -> None:
    data = full_trial.data
    decision = rows(data["decisions"])[1]
    trace = rows(data["event_traces"])[0]
    if mutation == "trace":
        trace["closed_at"] = 999
    elif mutation == "receipt":
        obj(trace["actor_visible_receipt"])["status"] = "FAILED"
    elif mutation == "count":
        decision["retrieved_count"] = 99
    elif mutation == "ids":
        decision["retrieved_event_trace_ids"] = ["forged"]
    elif mutation == "bytes":
        decision["event_memory_bytes"] = 0
    elif mutation == "serialized":
        decision["serialized_event_memory"] = "forged"
    elif mutation == "prompt":
        obj(decision["provider_request"])["prompt"] = "forged"
    elif mutation == "lineage":
        decision["opportunity_attempt"] = 99
    elif mutation == "submission":
        rows(data["action_traces"])[0]["engine_submitted"] = False
    elif mutation == "inventory":
        data["event_traces"] = []
    else:
        obj(data["manifest"])["memory_policy_version"] = "future"
    obj(data["manifest"])["record_sha256"] = record_digest(data)
    assert assess_event_memory(data)["status"] == "EXCLUDED"


@pytest.mark.parametrize("collapse_tick", [1, 2])
def test_real_engine_failed_completion_is_a_retrievable_experience(collapse_tick: int) -> None:
    kernel = create_alderwick_kernel()
    kernel.boot()
    provider = FakeProvider(
        [
            '{"action_type":"MOVE","payload":{"route_id":"east-road-to-east-bridge"}}',
            '{"action_type":"WAIT","payload":{"duration":1}}',
        ]
    )
    controller = LLMController(provider, event_memory=RecencyEventMemory())
    try:
        kernel.schedule(scenario_schedule(collapse_tick).events[0])
        app, research = create_alderwick_application(kernel)
        game = app.game_for("hugh")
        failed = complete(game, controller)
        assert failed.receipt is not None and failed.receipt.status == "FAILED"
        assert failed.receipt.resolved_at == 2
        assert research.events[0].event_type == "BridgeCollapsed"
        assert research.action_traces[0].engine_submitted
        complete(game, controller)
        trace = controller.event_traces[0]
        assert provider.inputs[1]["event_memory"] == [trace.to_json()]
        assert trace.actor_visible_receipt.reason_code == "ROUTE_IMPASSABLE"
    finally:
        kernel.close()


def test_postcommit_delivery_failure_does_not_infer_receipt_from_world_result() -> None:
    bus = EventBus()

    def broken(event: EventEnvelope) -> None:
        raise RuntimeError("private delivery detail")

    bus.subscribe(
        event_type="ActorMoved",
        priority=0,
        module_id="test",
        subscriber_id="broken",
        subscriber=broken,
    )
    kernel = create_alderwick_kernel(event_bus=bus)
    kernel.boot()
    provider = FakeProvider(
        ['{"action_type":"MOVE","payload":{"route_id":"west-gate-to-village-square"}}']
    )
    controller = LLMController(provider, event_memory=RecencyEventMemory())
    try:
        app, research = create_alderwick_application(kernel)
        turn = complete(app.game_for("stranger"), controller)
        assert turn.failure_code == "SUBMISSION_ERROR" and turn.receipt is None
        assert research.action_results[0].status == "SUCCEEDED"
        assert research.action_traces[0].engine_submitted
        assert controller.event_traces == ()
    finally:
        kernel.close()


def test_invalid_controller_output_never_closes_an_experience() -> None:
    class InvalidController(LLMController):
        def decide(self, observation: Observation) -> ActionRequest:
            request = super().decide(observation)
            return replace(request, actor_id="hugh")

    controller = InvalidController(FakeProvider(), event_memory=RecencyEventMemory())
    with game_fixture() as game:
        turn = complete(game, controller)
    assert turn.failure_code == "INVALID_CONTROLLER_OUTPUT" and turn.receipt is None
    assert controller.last_decision is not None and controller.event_traces == ()


@pytest.mark.parametrize("version", ["legacy", "event", "unknown"])
def test_protocol_requires_explicit_matching_memory_mode(version: str) -> None:
    controller = LLMController(
        FakeProvider(), event_memory=RecencyEventMemory() if version != "event" else None
    )
    with pytest.raises(ValueError):
        run_trial(
            controller,
            trial_id="mismatch",
            protocol_version=(
                "alderwick-24h-2"
                if version == "legacy"
                else EVENT_MEMORY_PROTOCOL_VERSION
                if version == "event"
                else "unknown"
            ),
        )


def test_no_memory_retains_research_archive_but_no_observation_history() -> None:
    controller = LLMController(FakeProvider(), event_memory=NoMemory())
    with game_fixture() as game:
        complete(game, controller)
        complete(game, controller)
    assert len(controller.event_traces) == 2
    assert controller._history == []
    assert controller.last_decision is not None
    assert controller.last_decision.data["retrieved_count"] == 0


def test_failed_attempts_never_replace_the_last_closed_memory() -> None:
    wait = '{"action_type":"WAIT","payload":{"duration":1}}'
    provider = FakeProvider([wait, TimeoutError(), "invalid", wait, wait])
    controller = LLMController(provider, event_memory=RecencyEventMemory())
    decisions = []
    with game_fixture() as game:
        for _ in range(5):
            complete(game, controller)
            assert controller.last_decision is not None
            decisions.append(controller.last_decision.data)
    traces = controller.event_traces
    assert len(traces) == 3
    assert all(p["event_memory"] == [traces[0].to_json()] for p in provider.inputs[1:4])
    assert provider.inputs[4]["event_memory"] == [traces[1].to_json()]
    assert [d["opportunity_attempt"] for d in decisions] == [1, 1, 2, 3, 1]


def test_hidden_world_and_provider_metadata_canaries_never_enter_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from journeymap.scenarios.alderwick.resources import resource_world

    def private_world() -> JsonObject:
        world = resource_world()
        world["private"] = {"secret": "UNOBSERVABLE_WORLD_CANARY", "future": [111, 222]}
        return world

    monkeypatch.setattr("journeymap.scenarios.alderwick.resources.resource_world", private_world)
    response = RawModelResponse(
        '{"action_type":"WAIT","payload":{"duration":1}}', {"private": "PROVIDER_INTERNAL_CANARY"}
    )
    provider = FakeProvider([response, response])
    controller = LLMController(provider, event_memory=RecencyEventMemory())
    with game_fixture() as game:
        complete(game, controller)
        complete(game, controller)
    serialized = json.dumps([t.to_json() for t in controller.event_traces]) + json.dumps(
        provider.inputs
    )
    assert "UNOBSERVABLE_WORLD_CANARY" not in serialized
    assert "PROVIDER_INTERNAL_CANARY" not in serialized


def test_memory_cannot_fabricate_actor_visible_trace() -> None:
    from journeymap.adapters.event_memory import EventTrace

    class FabricatingMemory(RecencyEventMemory):
        def select_events(self, context: EventMemoryContext) -> tuple[EventTrace, ...]:
            return (replace(context.prior[-1], attempt=999),) if context.prior else ()

    provider = FakeProvider()
    controller = LLMController(provider, event_memory=FabricatingMemory())
    with game_fixture() as game:
        complete(game, controller)
        failed = complete(game, controller)
    assert failed.receipt is None and len(provider.inputs) == 1
    assert len(controller.event_traces) == 1


@pytest.mark.parametrize("confirmation", [False, None, 1])
def test_direct_close_requires_positive_submission_confirmation(confirmation: object) -> None:
    controller = LLMController(FakeProvider(), event_memory=RecencyEventMemory())
    with game_fixture() as game:
        observation = game.observe()
        request = controller.decide(observation)
        receipt = game.submit(request)
        with pytest.raises(ValueError, match="engine submission"):
            controller.close_event_trace(
                observation,
                request,
                receipt,
                engine_submitted=confirmation,  # type: ignore[arg-type]
            )
        with pytest.raises(TypeError, match="engine_submitted"):
            controller.close_event_trace(observation, request, receipt)  # type: ignore[call-arg]
        assert controller.event_traces == ()
        controller.close_event_trace(observation, request, receipt, engine_submitted=True)
        assert len(controller.event_traces) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reason_code", {"private": "PROVIDER_INTERNAL_CANARY"}),
        ("reason_code", "PRIVATE_DIAGNOSTIC_CANARY"),
        ("reason_code", "ACTION_FAILED"),
        ("started_at", False),
        ("resolved_at", True),
        ("resolved_at", -1),
        ("schema_version", True),
        ("status", "UNKNOWN_STATUS"),
        ("status", "FAILED"),
    ],
)
def test_direct_close_rejects_malformed_receipts_without_archive_mutation(
    field: str,
    value: object,
) -> None:
    controller = LLMController(FakeProvider(), event_memory=RecencyEventMemory())
    with game_fixture() as game:
        observation = game.observe()
        request = controller.decide(observation)
        receipt = game.submit(request)
        malformed = replace(receipt, **{field: value})  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="actor-visible receipt"):
            controller.close_event_trace(observation, request, malformed, engine_submitted=True)
        assert controller.event_traces == ()
        controller.close_event_trace(observation, request, receipt, engine_submitted=True)
        assert len(controller.event_traces) == 1


def test_direct_boundary_denial_receipt_is_not_an_engine_experience() -> None:
    provider = FakeProvider(
        [
            '{"action_type":"INFORM","payload":{"target_actor_id":"hugh","claim_record_id":"unknown"}}'
        ]
    )
    controller = LLMController(provider, event_memory=RecencyEventMemory())
    kernel = create_alderwick_kernel(social=True, resources=True, experiment=True)
    kernel.boot()
    try:
        app, research = create_alderwick_application(
            kernel, social=True, resources=True, experiment=True
        )
        game = app.game_for("stranger")
        observation = game.observe()
        request = controller.decide(observation)
        receipt = game.submit(request)
        assert receipt.status == "REJECTED" and not research.action_traces[-1].engine_submitted
        with pytest.raises(ValueError, match="engine submission"):
            controller.close_event_trace(
                observation,
                request,
                receipt,
                engine_submitted=research.action_traces[-1].engine_submitted,
            )
        assert controller.event_traces == ()
    finally:
        kernel.close()


def test_audit_malformed_normalized_request_returns_exclusion(full_trial: Record) -> None:
    data = full_trial.data
    decision = rows(data["decisions"])[0]
    request = obj(decision["action_request"])
    request["correlation_id"] = 123
    for trace in rows(data["action_traces"]):
        if obj(trace["request"])["action_request_id"] == request["action_request_id"]:
            trace["request"] = dict(request)
    for action in rows(data["action_requests"]):
        if action["action_request_id"] == request["action_request_id"]:
            action["correlation_id"] = 123
    obj(data["manifest"])["record_sha256"] = record_digest(data)
    assert assess_event_memory(data)["status"] == "EXCLUDED"
