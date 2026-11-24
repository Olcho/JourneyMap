"""Recording transport tests: no credentials, DNS or external HTTP permitted."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from journeymap.adapters.life_llm import LifeLLMConfig, life_candidate, life_request
from journeymap.adapters.life_responses import (
    MODEL,
    Budget,
    LifeExecutionPlan,
    LifeResponsesPreflightProvider,
    PreflightStop,
    RecordingResponsesTransport,
    Usage,
    preflight_provider,
    synthetic_response,
)
from journeymap.adapters.provider import ProviderRequest, provider_identity
from journeymap.examples.life_preflight import run_preflight
from journeymap.experiments.checkpoint import save_checkpoint
from journeymap.experiments.life import LifeSession
from journeymap.experiments.mixed_life import MixedLifeSession
from journeymap.scenarios.life import LifeConfig

WAIT = '{"action_type":"WAIT","payload":{"duration":1}}'


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    get = os.environ.get

    def deny(*args: object, **kwargs: object) -> Any:
        raise AssertionError("real credentials/network forbidden")

    def guarded(key: str, default: Any = None) -> Any:
        return deny() if key == "OPENAI_API_KEY" else get(key, default)

    monkeypatch.setattr(os.environ, "get", guarded)
    monkeypatch.setattr("socket.socket", deny)
    monkeypatch.setattr("socket.getaddrinfo", deny)


@pytest.fixture
def decision_request() -> ProviderRequest:
    session = LifeSession(LifeConfig(horizon=24))
    try:
        return life_request(session.application.game_for("ada").observe(), 8192)
    finally:
        session.close()


def provider(raw: str | None = None) -> LifeResponsesPreflightProvider:
    return preflight_provider(
        RecordingResponsesTransport(((200, raw or synthetic_response(WAIT)),))
    )


def test_wire_is_life_schema_only_and_usage_has_no_reasoning_double_charge(
    decision_request: ProviderRequest,
) -> None:
    p = provider()
    response = p.generate(decision_request)
    body = cast(dict[str, Any], p.transport.bodies[0].data)
    assert set(body) == {
        "model",
        "input",
        "store",
        "text",
        "reasoning",
        "max_output_tokens",
        "service_tier",
    }
    assert body["service_tier"] == "default"
    assert (
        body["model"] == MODEL
        and body["input"] == decision_request.prompt
        and body["store"] is False
    )
    assert body["reasoning"] == {"effort": "high"} and body["max_output_tokens"] == 8192
    assert body["text"]["format"]["name"] == "journeymap_life_candidate_v1"
    assert body["text"]["format"]["strict"] is True
    assert "WORK" in body["text"]["format"]["schema"]["properties"]["action_type"]["enum"]
    assert p.budget.cost_nano_usd == 800 * 750 + 200 * 75 + 100 * 4500
    assert p.budget.input_tokens == 1000 and p.budget.output_tokens == 100
    assert p.budget.reserved_cost == 0
    assert response.metadata["wire_model"] == MODEL and "model" not in response.metadata
    assert provider_identity(p)["kind"] == "fixture"
    assert p.records[0].data["raw_response"] == synthetic_response(WAIT)


@pytest.mark.parametrize(
    "status,refusal,expected", [("completed", True, "REFUSAL"), ("incomplete", False, "INCOMPLETE")]
)
def test_classified_failure_charges_reported_usage_then_stops(
    decision_request: ProviderRequest, status: str, refusal: bool, expected: str
) -> None:
    p = provider(synthetic_response("", status=status, refusal=refusal))
    raw = p.generate(decision_request)
    assert p.budget.stop == expected and p.budget.cost_nano_usd > 0
    assert raw.metadata["refusal"] == refusal
    with pytest.raises(PreflightStop, match=expected):
        p.generate(decision_request)
    assert len(p.transport.bodies) == 1


@pytest.mark.parametrize(
    "change,reason,settled",
    [
        ({"model": "gpt-5.4-mini"}, "IDENTITY_MISMATCH", True),
        ({"id": None}, "INVALID_RESPONSE_ID", True),
        ({"service_tier": "priority"}, "SERVICE_TIER_MISMATCH", True),
        ({"status": "failed"}, "RESPONSE_FAILED", True),
        ({"usage": None}, "USAGE_UNAVAILABLE", False),
        ({"usage": {"input_tokens": True}}, "USAGE_UNAVAILABLE", False),
        ({"output": [{"type": "function_call"}]}, "UNEXPECTED_OUTPUT", True),
    ],
)
def test_response_failures_stop_without_retry(
    decision_request: ProviderRequest, change: dict[str, Any], reason: str, settled: bool
) -> None:
    doc = json.loads(synthetic_response(WAIT)) | change
    p = provider(json.dumps(doc))
    with pytest.raises(PreflightStop, match=reason):
        p.generate(decision_request)
    assert p.budget.attempts == 1 and bool(p.budget.reserved_cost) != settled
    with pytest.raises(PreflightStop):
        p.generate(decision_request)
    assert len(p.transport.bodies) == 1


@pytest.mark.parametrize("raw", ["{", "[" * 1500 + "0" + "]" * 1500, '{"x":1,"x":2}'])
def test_bad_response_envelopes_keep_full_reservation(
    decision_request: ProviderRequest, raw: str
) -> None:
    p = provider(raw)
    with pytest.raises(PreflightStop, match="INVALID_RESPONSE"):
        p.generate(decision_request)
    assert p.budget.reserved_cost == 336_864_000
    assert p.records[0].data["raw_response"] == raw


@pytest.mark.parametrize(
    "failure,reason",
    [
        (TimeoutError("private detail"), "TRANSPORT_UNCERTAIN"),
        ((429, "not persisted as key"), "HTTP_ERROR"),
    ],
)
def test_uncertain_transport_is_counted_reserved_and_terminal(
    decision_request: ProviderRequest, failure: Exception | tuple[int, str], reason: str
) -> None:
    p = preflight_provider(RecordingResponsesTransport((failure,)))
    with pytest.raises(PreflightStop, match=reason):
        p.generate(decision_request)
    assert p.budget.reserved_cost == 336_864_000 and p.budget.attempts == 1
    assert "private detail" not in json.dumps(p.records[0].data)


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("attempts", 32, "REQUEST_LIMIT"),
        ("input_tokens", 100_001, "INPUT_TOKEN_LIMIT"),
        ("output_tokens", 122_881, "OUTPUT_TOKEN_LIMIT"),
        ("cost_nano_usd", 663_136_001, "COST_LIMIT"),
    ],
)
def test_pre_send_caps_block_transport(
    decision_request: ProviderRequest, field: str, value: int, reason: str
) -> None:
    p = provider()
    setattr(p.budget, field, value)
    with pytest.raises(PreflightStop, match=reason):
        p.generate(decision_request)
    assert not p.transport.bodies


def test_budget_exact_boundary_and_usage_validation() -> None:
    b = Budget(LifeExecutionPlan(), cost_nano_usd=663_136_000)
    b.reserve({})
    assert b.cost_nano_usd + b.reserved_cost == 1_000_000_000
    with pytest.raises(PreflightStop, match="USAGE_EXCEEDS_RESERVATION"):
        b.settle(Usage(400001, 0, 0, 0, 400001))
    assert b.reserved_cost == 336_864_000
    value = json.loads(synthetic_response(WAIT))["usage"]
    for mutation in ({"input_tokens": True}, {"total_tokens": 1}, {"output_tokens": -1}):
        with pytest.raises(ValueError, match="INVALID_USAGE"):
            Usage.parse(value | mutation)


def test_input_size_identity_plan_and_transport_selection_fail_before_send(
    decision_request: ProviderRequest,
) -> None:
    p = provider()
    with pytest.raises(PreflightStop, match="INPUT_TOO_LARGE"):
        p.generate(replace(decision_request, prompt="x" * 65536))
    assert p.budget.attempts == 0 and not p.transport.bodies
    for modified in (
        replace(decision_request, model="live"),
        replace(decision_request, parameters={}),
    ):
        with pytest.raises(ValueError, match="unexpected offline"):
            provider().generate(modified)
    with pytest.raises(ValueError, match="unsupported execution plan"):
        LifeExecutionPlan(model="other")
    with pytest.raises(PermissionError):
        preflight_provider(RecordingResponsesTransport(()), kind="live")


def test_full_mixed_preflight_matches_normal_behavior_and_replay_without_recall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from journeymap.adapters.life import LifeController

    original = LifeController.decide
    baseline = MixedLifeSession(LifeConfig(horizon=24))
    try:
        baseline.run_until(24)
        expected_report = baseline.report()
    finally:
        baseline.close()

    def no_ada(self: LifeController, observation: Any) -> Any:
        if observation.actor_id == "ada":
            raise AssertionError("Ada fallback forbidden")
        return original(self, observation)

    monkeypatch.setattr(LifeController, "decide", no_ada)
    result = run_preflight()
    assert result["tick"] == 24 and result["events"] == 171 and result["requests"] == 12
    assert result["pending_actions"] == 3
    assert result["life_report"] == expected_report
    assert result["real_api_calls"] == result["real_key_reads"] == 0
    assert result["replay"] == {
        "provider_recalled": False,
        "live_inputs_equal": True,
        "recorded_responses_checked": 12,
    }


def test_candidate_errors_use_existing_life_failure_boundary_and_custom_save_is_rejected(
    tmp_path: Path,
) -> None:
    for text, expected in (
        ("{", "INVALID_CANDIDATE"),
        ("[" * 1500 + "0" + "]" * 1500, "JSON_DEPTH_EXCEEDED"),
    ):
        p = provider(synthetic_response(text))
        session = MixedLifeSession(
            LifeConfig(horizon=24), LifeLLMConfig(max_output_tokens=8192), provider=p
        )
        try:
            with pytest.raises(ValueError, match="PAUSED_ERROR"):
                session.run_until(1)
            assert session.decider.state.calls[0].error == expected
            assert session.kernel.simulation_time == 0 and not session.application.attempts
            assert session.replay_recorded()["provider_recalled"] is False
            with pytest.raises(ValueError, match="custom provider"):
                save_checkpoint(session, tmp_path / "unsupported.json")
        finally:
            session.close()


def test_wire_schema_cross_pair_is_locally_rejected() -> None:
    session = LifeSession(LifeConfig(horizon=24))
    try:
        observation = session.application.game_for("ada").observe()
        with pytest.raises(ValueError):
            life_candidate('{"action_type":"WORK","payload":{"duration":1}}', observation)
    finally:
        session.close()
