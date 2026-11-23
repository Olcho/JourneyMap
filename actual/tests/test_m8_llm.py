"""M8 adversarial adapter contracts without network or paid API calls."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import fields
from typing import cast
from unittest.mock import MagicMock

import pytest

from journeymap.adapters.llm import (
    LLMController,
    observation_json,
    parameters_v1,
    parse_candidate,
    strict_json,
)
from journeymap.adapters.memory import MemoryContext
from journeymap.adapters.openai_provider import OpenAIProvider
from journeymap.adapters.provider import ProviderRequest, RawModelResponse
from journeymap.application.turns import run_controller_turn
from journeymap.bootstrap import create_alderwick_application, create_alderwick_kernel
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.core.controller import GamePort
from journeymap.core.kernel import SimulationKernel
from journeymap.core.observations import Observation
from journeymap.scenarios.alderwick.experiment import perceive_experiment
from journeymap.scenarios.alderwick.resources import resource_world


class FakeProvider:
    def __init__(self, raw: str | None = None, *, error: bool = False) -> None:
        self.raw = raw
        self.error = error
        self.requests: list[ProviderRequest] = []
        self.response_id: str | None = None

    def generate(self, request: ProviderRequest) -> RawModelResponse:
        self.requests.append(request)
        if self.error:
            raise TimeoutError("secret-test-value must never be persisted")
        raw = (
            self.raw
            if self.raw is not None
            else json.dumps(
                {
                    "action_type": "WAIT",
                    "payload": {"duration": 1},
                }
            )
        )
        return RawModelResponse(
            raw, {"Authorization": "secret-test-value", "response_id": self.response_id}
        )


@contextmanager
def game_fixture() -> Iterator[tuple[SimulationKernel, GamePort]]:
    kernel = create_alderwick_kernel(social=True, resources=True, experiment=True)
    kernel.boot()
    try:
        app, _ = create_alderwick_application(kernel, social=True, resources=True, experiment=True)
        yield kernel, app.game_for("stranger")
    finally:
        kernel.close()


def candidate(
    observation: Observation, action: str = "WAIT", payload: JsonObject | None = None
) -> JsonObject:
    return {
        "action_type": action,
        "payload": payload if payload is not None else {"duration": 1},
    }


VALID_PAYLOADS: tuple[tuple[str, JsonObject], ...] = (
    ("MOVE", {"route_id": "a-to-b"}),
    ("WAIT", {"duration": 1}),
    ("ASK", {"target_actor_id": "a", "subject_ref": "b", "predicate": "condition"}),
    ("INFORM", {"target_actor_id": "a", "claim_record_id": "k"}),
    (
        "REQUEST",
        {
            "target_actor_id": "a",
            "request_kind": "q",
            "request_payload": {"nested": [True, None, {"x": 2}]},
        },
    ),
    ("REST", {"duration": 1}),
    ("CONSUME", {"item_id": "bread", "quantity": 1}),
    ("BUY", {"offer_id": "bread", "quantity": 1}),
)


@pytest.mark.parametrize(("action", "payload"), VALID_PAYLOADS)
def test_all_eight_exact_m7_payloads(action: str, payload: JsonObject) -> None:
    observation = Observation("r", "a", 1, 0, {"sections": []})
    request = parse_candidate(candidate(observation, action, payload), observation)
    assert request.payload == payload and request.action_type == action


@pytest.mark.parametrize(("action", "payload"), VALID_PAYLOADS)
@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_each_payload_rejects_missing_and_extra(
    action: str, payload: JsonObject, mutation: str
) -> None:
    observation = Observation("r", "a", 1, 0, {"sections": []})
    altered = payload.copy()
    if mutation == "missing":
        del altered[next(iter(altered))]
    else:
        altered["utterance"] = "extra"
    with pytest.raises(ValueError):
        parse_candidate(candidate(observation, action, altered), observation)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "I move",
        "{}",
        "[]",
        "null",
        "```json\n{}\n```",
        '{"x":1,"x":2}',
        '{"x":NaN}',
        '{"x":Infinity}',
        '{"x":1e999}',
        '{"x":"\\ud800"}',
        "\ud800",
        "{}{}",
    ],
)
def test_invalid_output_is_recorded_without_world_mutation(raw: str) -> None:
    with game_fixture() as (kernel, game):
        before = (kernel.state_snapshot, kernel.simulation_time, kernel.events, kernel.rng_snapshot)
        controller = LLMController(FakeProvider(raw))
        turn = run_controller_turn(game, controller)
        assert turn.receipt is None
        assert before == (
            kernel.state_snapshot,
            kernel.simulation_time,
            kernel.events,
            kernel.rng_snapshot,
        )
        assert kernel.action_results == ()
        assert controller.last_decision is not None
        assert controller.last_decision.data["raw_output"] == raw
        assert controller.last_decision.data["failure"] == "INVALID_OUTPUT"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("actor_id", "hugh"),
        ("run_id", "other"),
        ("based_on_observation_id", "old"),
        ("submitted_at", 1),
        ("submitted_at", True),
        ("schema_version", True),
        ("schema_version", 2),
        ("action_type", "TALK"),
        ("action_type", "OBSERVE"),
        ("action_type", "CollapseEastBridge"),
        ("action_request_id", "duplicated-id"),
        ("correlation_id", ""),
        ("payload", {"duration": True}),
        ("payload", {"duration": 0}),
        ("payload", {"duration": 1, "world": {}}),
        ("extra", None),
    ],
)
def test_bad_envelope_binding_or_schema_never_submits(key: str, value: JsonValue) -> None:
    with game_fixture() as (kernel, game):
        observation = game.observe()
        altered = candidate(observation)
        altered[key] = value
        controller = LLMController(FakeProvider(json.dumps(altered)))
        # Keep the exact observed binding under test, not a second Observation.
        turn = run_controller_turn(GamePort(lambda: observation, game.submit), controller)
        assert turn.receipt is None and kernel.action_results == ()
        assert kernel.simulation_time == 0 and kernel.events == ()


def test_provider_exception_detachment_and_no_secret_persistence() -> None:
    with game_fixture() as (kernel, game):
        controller = LLMController(FakeProvider(error=True))
        turn = run_controller_turn(game, controller)
        assert turn.failure_code == "CONTROLLER_ERROR"
        assert kernel.simulation_time == 0 and kernel.action_results == ()
        record = controller.last_decision
        assert record is not None and record.data["failure"] == "PROVIDER_ERROR"
        assert "secret-test-value" not in record.serialized
        changed = record.data
        changed["failure"] = "rewritten"
        assert record.data["failure"] == "PROVIDER_ERROR"


def test_current_observation_only_prompt_and_public_failure_feedback() -> None:
    provider = FakeProvider()
    with game_fixture() as (_, game):
        controller = LLMController(provider)
        first = run_controller_turn(game, controller)
        second = run_controller_turn(game, controller)
        assert first.receipt is not None and second.receipt is not None
        assert {field.name for field in fields(provider.requests[0])} == {
            "prompt",
            "prompt_version",
            "model",
            "configuration_version",
            "parameters",
            "schema_version",
        }
        data = json.loads(provider.requests[1].prompt.split("\nINPUT_JSON\n", 1)[1])
        assert data["memory"] == []
        assert second.observation is not None
        assert data["observation"] == observation_json(second.observation)
        text = provider.requests[1].prompt
        for hidden in (
            "CollapseEastBridge",
            "SurvivalTick",
            "state_digest_after",
            "rng",
            "scheduler",
            "hugh-return",
            "thomas-stale",
            "secret-test-value",
        ):
            assert hidden not in text
        assert controller.last_decision is not None
        assert "Authorization" not in controller.last_decision.serialized


def test_duplicate_response_is_rejected_at_new_observation_and_original_receipt_is_idempotent() -> (
    None
):
    with game_fixture() as (kernel, game):
        provider = FakeProvider()
        provider.response_id = "same-response-id"
        controller = LLMController(provider)
        first = run_controller_turn(game, controller)
        assert first.request is not None and first.receipt is not None
        assert controller.last_decision is not None
        provider.raw = cast(str, controller.last_decision.data["raw_output"])
        before = (kernel.simulation_time, kernel.events, kernel.action_results)
        second = run_controller_turn(game, controller)
        assert second.receipt is None
        assert game.submit(first.request) == first.receipt
        assert before == (kernel.simulation_time, kernel.events, kernel.action_results)


def test_memory_cannot_fabricate_context_and_receives_only_delivered_history() -> None:
    contexts: list[MemoryContext] = []

    class BadMemory:
        def select(self, context: MemoryContext) -> tuple[Observation, ...]:
            contexts.append(context)
            if not context.prior:
                return ()
            return (Observation(context.current.run_id, "hugh", 1, 0, {"sections": []}),)

    with game_fixture() as (kernel, game):
        provider = FakeProvider()
        controller = LLMController(provider, memory=BadMemory())
        first = run_controller_turn(game, controller)
        second = run_controller_turn(game, controller)
        assert contexts[1].prior == (first.observation,)
        assert second.receipt is None and len(provider.requests) == 1
        assert kernel.simulation_time == 1


def test_parser_exception_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args: object) -> None:
        raise RuntimeError("secret-test-value")

    monkeypatch.setattr("journeymap.adapters.llm.parse_candidate", broken)
    with game_fixture() as (kernel, game):
        controller = LLMController(FakeProvider())
        run_controller_turn(game, controller)
        assert kernel.action_results == () and controller.last_decision is not None
        assert controller.last_decision.data["parsed_candidate"] is not None
        assert "secret-test-value" not in controller.last_decision.serialized


def test_local_perception_ignores_remote_private_and_future_state() -> None:
    world = resource_world()
    before = perceive_experiment(world, "stranger")
    world["debug"] = {"future": "secret-test-value"}
    movement = cast(JsonObject, world["movement"])
    routes = cast(JsonObject, movement["routes"])
    cast(JsonObject, routes["east-road-to-east-bridge"])["passable"] = False
    cast(JsonObject, world["trade"])["wallets"] = {"stranger": 10, "hugh": "PRIVATE"}
    assert perceive_experiment(world, "stranger") == before
    assert before["local"] == {
        "actor_ids": [],
        "exits": [
            {
                "route_id": "west-gate-to-village-square",
                "destination": "village-square",
                "traversal_cost": 2,
            }
        ],
    }


@pytest.mark.parametrize(
    "parameters",
    [
        {"api_key": "secret"},
        {"temperature": True},
        {"temperature": float("nan")},
        {"max_output_tokens": True},
        {"max_output_tokens": 0},
        {"top_p": 2},
    ],
)
def test_configuration_allowlist(parameters: JsonObject) -> None:
    with pytest.raises(ValueError):
        parameters_v1(parameters)


def test_optional_inform_and_arbitrary_request_remain_exact_m7() -> None:
    observation = Observation("r", "a", 1, 0, {"sections": []})
    payload: JsonObject = {"target_actor_id": "b", "claim_record_id": "k", "reply_to_event_id": "e"}
    assert (
        parse_candidate(candidate(observation, "INFORM", payload), observation).payload == payload
    )
    payload["reply_to_event_id"] = None
    with pytest.raises(ValueError):
        parse_candidate(candidate(observation, "INFORM", payload), observation)
    with pytest.raises(ValueError):
        strict_json('{"a":{"x":1,"x":2}}')


@pytest.mark.parametrize("mode", ["valid", "refusal", "incomplete", "http", "timeout", "echo"])
def test_http_adapter_uses_fixed_endpoint_and_never_persists_credentials(
    mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-test-value")
    connection = MagicMock()
    factory = MagicMock(return_value=connection)
    monkeypatch.setattr("journeymap.adapters.openai_provider.http.client.HTTPSConnection", factory)
    content = {"type": "output_text", "text": "{}"}
    if mode == "echo":
        content["text"] = "secret-test-value"
    elif mode == "refusal":
        content = {"type": "refusal", "refusal": "declined"}
    document = {
        "id": "response-1",
        "model": "test",
        "status": "incomplete" if mode == "incomplete" else "completed",
        "output": [{"type": "message", "content": [content]}],
        "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
    }
    connection.getresponse.return_value.status = 401 if mode == "http" else 200
    connection.getresponse.return_value.read.return_value = json.dumps(document).encode()
    if mode == "timeout":
        connection.request.side_effect = TimeoutError("secret-test-value")
    request = ProviderRequest(
        "JSON task",
        "1",
        "gpt-5.6-sol",
        "1",
        {"reasoning": {"effort": "medium"}, "max_output_tokens": 4096},
    )
    if mode in ("http", "timeout", "echo"):
        with pytest.raises(RuntimeError) as error:
            OpenAIProvider().generate(request)
        assert "secret-test-value" not in str(error.value)
    else:
        response = OpenAIProvider().generate(request)
        assert "secret-test-value" not in repr(response)
        assert response.metadata["refusal"] is (mode == "refusal")
    factory.assert_called_once_with("api.openai.com", timeout=30)
    call = connection.request.call_args
    assert call.args == ("POST", "/v1/responses")
    body = json.loads(call.kwargs["body"])
    assert body["store"] is False
    assert body["text"]["format"]["type"] == "json_schema"
    assert body["text"]["format"]["strict"] is True
    assert body["reasoning"] == {"effort": "medium"}
    assert body["model"] == "gpt-5.6-sol" and "temperature" not in body
    assert "tools" not in body and "secret-test-value" not in canonical_json(body)
    connection.close.assert_called_once()


def test_missing_key_import_and_construction_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = OpenAIProvider()
    with pytest.raises(RuntimeError, match="CREDENTIAL"):
        provider.generate(ProviderRequest("JSON", "1", "test", "1"))
