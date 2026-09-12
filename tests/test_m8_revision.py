"""First-official-trial review: candidate authority, strict wire codec and hours."""

import json
from dataclasses import fields
from typing import cast

import pytest
from test_m8_llm import VALID_PAYLOADS, FakeProvider, candidate, game_fixture

from journeymap.adapters.decision_schema import decision_schema
from journeymap.adapters.llm import DEFAULT_MODEL, LLMController, decode_candidate, parse_candidate
from journeymap.adapters.provider import ProviderRequest, RawModelResponse
from journeymap.application.turns import run_controller_turn
from journeymap.bootstrap import create_alderwick_application, create_alderwick_kernel
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.core.controller import ControllerActionResult
from journeymap.core.observations import Observation
from journeymap.experiments.alderwick import run_trial
from journeymap.scenarios.alderwick.experiment import experiment_schedule
from journeymap.scenarios.alderwick.resources import resource_schedule


@pytest.mark.parametrize(("action", "payload"), VALID_PAYLOADS)
def test_candidate_binding_is_always_controller_owned(action: str, payload: JsonObject) -> None:
    observation = Observation("trusted-run", "trusted-actor", 19, 11, {"sections": []})
    intent = candidate(observation, action, payload)
    request = parse_candidate(intent, observation)
    assert request.action_request_id == observation.observation_id + ":llm-v2"
    assert request.run_id == observation.run_id and request.actor_id == observation.actor_id
    assert request.based_on_observation_id == observation.observation_id
    assert request.submitted_at == 11 and request.schema_version == 1
    assert request.correlation_id is None
    assert intent == {"action_type": action, "payload": payload}


@pytest.mark.parametrize(
    "nested",
    [
        {},
        {"a": [True, None, {"b": "한글😀", "n": 1.25}]},
        {"unknown-key": {"arbitrary": [[1], [], {}]}},
        {"authority": {"actor_id": "data-only", "submitted_at": False}},
    ],
)
def test_request_subobject_codec_preserves_all_m7_json_meaning(nested: JsonObject) -> None:
    wire: JsonObject = {
        "action_type": "REQUEST",
        "payload": {
            "target_actor_id": "hugh",
            "request_kind": "advice",
            "request_payload_json": json.dumps(nested),
        },
    }
    parsed = decode_candidate(wire)
    observation = Observation("r", "a", 1, 0, {"sections": []})
    request = parse_candidate(parsed, observation)
    assert request.payload["request_payload"] == nested
    assert "request_payload_json" in cast(JsonObject, wire["payload"])
    assert "request_payload_json" not in request.payload


@pytest.mark.parametrize(
    "raw", ["[]", "null", '{"a":1,"a":2}', '{"a":NaN}', '{"a":"\\ud800"}', "bad"]
)
def test_request_subobject_invalid_json_cannot_reach_engine(raw: str) -> None:
    provider = FakeProvider(
        json.dumps(
            {
                "action_type": "REQUEST",
                "payload": {
                    "target_actor_id": "hugh",
                    "request_kind": "advice",
                    "request_payload_json": raw,
                },
            }
        )
    )
    with game_fixture() as (kernel, game):
        controller = LLMController(provider)
        assert run_controller_turn(game, controller).receipt is None
        assert kernel.action_results == () and controller.last_decision is not None
        assert controller.last_decision.data["parsed_wire_candidate"] is not None
        assert controller.last_decision.data["action_request"] is None


def test_schema_obeys_strict_subset_and_excludes_authority() -> None:
    schema = decision_schema()
    assert schema["type"] == "object" and "anyOf" not in schema
    assert set(cast(JsonObject, schema["properties"])) == {"action_type", "payload"}

    def check(node: JsonValue) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(cast(list[str], node["required"])) == set(
                    cast(JsonObject, node["properties"])
                )
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(schema)
    # Every value-bearing property is required within each branch. INFORM has
    # two branches so omission stays omission; it is never converted from null.
    branches = cast(
        list[JsonObject],
        cast(JsonObject, cast(JsonObject, schema["properties"])["payload"])["anyOf"],
    )
    inform = [b for b in branches if "claim_record_id" in cast(JsonObject, b["properties"])]
    assert len(inform) == 2
    assert {len(cast(JsonObject, b["properties"])) for b in inform} == {2, 3}


def test_m8_schedule_is_distinct_and_m6_fixture_unchanged() -> None:
    old, new = resource_schedule(), experiment_schedule()
    assert old.scenario_version == "resources-1" and len(old.events) == 21
    assert new.scenario_version == "social-resources-24h-1" and len(new.events) == 25
    assert old.events[0] == new.events[0]
    assert [event.due_time for event in new.events if event.event_type == "SurvivalTick"] == list(
        range(1, 25)
    )
    assert old.events[-1].due_time == 20 and new.events[-1].due_time == 24


def test_receipt_projection_is_only_m7_public_fields_and_same_actor() -> None:
    kernel = create_alderwick_kernel(social=True, resources=True, experiment=True)
    kernel.boot()
    try:
        app, _ = create_alderwick_application(kernel, social=True, resources=True, experiment=True)
        game = app.game_for("stranger")
        turn = run_controller_turn(
            game,
            LLMController(
                FakeProvider(
                    json.dumps(
                        {"action_type": "CONSUME", "payload": {"item_id": "bread", "quantity": 1}}
                    )
                )
            ),
        )
        assert turn.receipt is not None and turn.receipt.status == "REJECTED"
        run_controller_turn(app.game_for("hugh"), LLMController(FakeProvider()))
        observation = game.observe()
        sections = cast(list[JsonObject], observation.content["sections"])
        section = next(item for item in sections if item["contributor_id"] == "last_receipt")
        last = cast(JsonObject, cast(JsonObject, section["content"])["last_action"])
        receipt = cast(JsonObject, last["receipt"])
        assert set(receipt) == {field.name for field in fields(ControllerActionResult)}
        assert receipt["actor_id"] == "stranger" and receipt["status"] == "REJECTED"
        assert "state_digest" not in json.dumps(receipt) and "hugh" not in json.dumps(receipt)
    finally:
        kernel.close()


def test_identical_intent_from_new_responses_is_a_new_bound_request() -> None:
    with game_fixture() as (kernel, game):
        controller = LLMController(FakeProvider())
        first, second = run_controller_turn(game, controller), run_controller_turn(game, controller)
        assert first.request is not None and second.request is not None
        assert first.request.action_request_id != second.request.action_request_id
        assert kernel.simulation_time == 2


@pytest.mark.parametrize(
    "metadata", [{"refusal": True}, {"status": "incomplete"}, {"status": "failed"}]
)
def test_refusal_and_incomplete_output_cannot_submit_even_if_json_is_valid(
    metadata: JsonObject,
) -> None:
    class RefusingProvider:
        def generate(self, request: ProviderRequest) -> RawModelResponse:
            return RawModelResponse('{"action_type":"WAIT","payload":{"duration":1}}', metadata)

    with game_fixture() as (kernel, game):
        assert run_controller_turn(game, LLMController(RefusingProvider())).receipt is None
        assert kernel.action_results == ()


@pytest.mark.parametrize("model", ["gpt-5.6-sol", "gpt-5.6-terra"])
def test_model_alias_and_returned_identity_are_separately_recorded(model: str) -> None:
    class IdentityProvider(FakeProvider):
        def generate(self, request: ProviderRequest) -> RawModelResponse:
            raw = super().generate(request)
            return RawModelResponse(raw.text, {"model": "returned-identity-fixture"})

    from journeymap.experiments.alderwick import TrialPolicy

    trial = run_trial(
        LLMController(IdentityProvider(), model=model),
        trial_id="identity",
        policy=TrialPolicy(max_decisions=1),
    )
    manifest = cast(JsonObject, trial.data["manifest"])
    assert manifest["model"] == model and manifest["response_models"] == [
        "returned-identity-fixture"
    ]
    assert manifest["parameters"] == {"reasoning": {"effort": "medium"}, "max_output_tokens": 4096}
    assert DEFAULT_MODEL == "gpt-5.6-sol"
