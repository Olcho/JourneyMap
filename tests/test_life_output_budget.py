"""8192-only life plan, isolated research limits, and offline paid accounting."""

import copy
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from test_paid_life import Clock, FakeHTTPS, FakeResponse, approval, compose, current_request
from test_paid_life import offline as offline

from journeymap.adapters.life_admission import PROTOCOL, binding
from journeymap.adapters.life_http import PaidFailure, admit_provider
from journeymap.adapters.life_llm import LifeLLMConfig, life_body
from journeymap.adapters.life_responses import (
    VERSION,
    Budget,
    LifeExecutionPlan,
    LifeResponsesPreflightProvider,
    PreflightStop,
    RecordingResponsesTransport,
    Usage,
    synthetic_response,
)
from journeymap.adapters.llm import parameters_v1
from journeymap.adapters.openai_provider import request_body
from journeymap.experiments.paid_life import PaidLifeSession
from journeymap.experiments.paid_replay import replay_paid_report

LEGACY_PLAN = "89447928f2357efef57f0f9cbc9ce8a53beced16cdb7a5f6136301d34b95380d"
LEGACY_SOURCE = "b0cd3e00bc45b27bc7e8ffe63bc3481e4ff23d8eb8c61e48ea46fb02f66d5a06"
LEGACY_PROTOCOL = "fe36110945c5b323c583268e067fb73e462ad004ffc87f07117d30f4f5d8b322"
WAIT = '{"action_type":"WAIT","payload":{"duration":1}}'


def test_fixed_plan_changes_only_per_request_output_budget() -> None:
    import hashlib

    from journeymap.core.canonical import canonical_json
    from journeymap.experiments.life import json_data

    plan = LifeExecutionPlan()
    legacy = asdict(plan) | {"max_output_tokens": 4096}
    assert hashlib.sha256(canonical_json(json_data(legacy)).encode()).hexdigest() == LEGACY_PLAN
    assert plan.max_output_tokens == 8192 and plan.digest != LEGACY_PLAN
    assert PROTOCOL == "life-paid-responses-2" and VERSION == "life-responses-preflight-2"
    for maximum in (4096, 16384, 8192.0, True):
        with pytest.raises(ValueError, match="unsupported execution plan"):
            LifeExecutionPlan(max_output_tokens=maximum)  # type: ignore[arg-type]


def test_life_body_preserves_old_wire_and_does_not_expand_research_limit() -> None:
    request = current_request()
    legacy = replace(
        request, parameters={"max_output_tokens": 4096, "reasoning": {"effort": "high"}}
    )
    from journeymap.adapters.life_llm import life_response_format

    assert life_body(legacy) == request_body(legacy, format_override=life_response_format())
    assert life_body(request) == life_body(legacy) | {"max_output_tokens": 8192}
    with pytest.raises(ValueError, match="invalid max_output_tokens"):
        parameters_v1(request.parameters)
    with pytest.raises(ValueError, match="invalid max_output_tokens"):
        request_body(request)


@pytest.mark.parametrize("maximum", [15, 8193, 16384, True, 8192.0])
def test_life_wire_and_config_reject_invalid_caps(maximum: Any) -> None:
    request = replace(
        current_request(),
        parameters={"max_output_tokens": maximum, "reasoning": {"effort": "high"}},
    )
    with pytest.raises(ValueError, match="invalid life model parameters"):
        life_body(request)
    with pytest.raises(ValueError, match="invalid life output/timeout limit"):
        LifeLLMConfig(max_output_tokens=maximum)


@pytest.mark.parametrize(
    "parameters",
    [
        {"max_output_tokens": 8192, "reasoning": {"effort": "medium"}},
        {"max_output_tokens": 8192, "reasoning": {"effort": "high"}, "tools": []},
        {"max_output_tokens": 8192},
    ],
)
def test_life_wire_keeps_strict_parameter_allowlist(parameters: Any) -> None:
    with pytest.raises(ValueError, match="invalid life model parameters"):
        life_body(replace(current_request(), parameters=parameters))


@pytest.mark.parametrize("maximum", [4096, 16384])
def test_mismatched_output_limit_is_rejected_before_send(tmp_path: Path, maximum: int) -> None:
    fake = FakeHTTPS([])
    provider = compose(tmp_path, fake)
    request = replace(
        current_request(),
        parameters={"max_output_tokens": maximum, "reasoning": {"effort": "high"}},
    )
    try:
        provider._credential = lambda: pytest.fail("credential must not be read")
        with pytest.raises(PaidFailure, match="ADMISSION_MISMATCH"):
            provider.generate(request)
        assert provider.budget.attempts == 0 and not fake.created
        preflight = LifeResponsesPreflightProvider(RecordingResponsesTransport(()))
        offline_request = replace(
            request, model="offline-fixture", configuration_version="life-offline-1"
        )
        with pytest.raises(ValueError, match="unexpected offline life envelope"):
            preflight.generate(offline_request)
        assert not preflight.transport.bodies
    finally:
        provider.journal.close()


@pytest.mark.parametrize(
    "field,legacy",
    [
        ("plan_digest", LEGACY_PLAN),
        ("source_digest", LEGACY_SOURCE),
        ("protocol_digest", LEGACY_PROTOCOL),
    ],
)
def test_old_binding_cannot_authorize_new_plan(tmp_path: Path, field: str, legacy: str) -> None:
    run, registry = str(uuid4()), tmp_path / "runs.sqlite3"
    grant = replace(approval(run, registry), bound=binding(run, registry) | {field: legacy})
    fake = FakeHTTPS([])
    with pytest.raises(PermissionError, match="mismatched"):
        admit_provider(
            run, grant, registry, credential=lambda: pytest.fail("credential"), connection=fake
        )
    assert not registry.exists() and not fake.created


def test_total_output_allows_sixteen_full_responses_then_stops() -> None:
    budget = Budget(LifeExecutionPlan())
    for _ in range(16):
        budget.reserve({})
        assert budget.reserved_output == 8192 and budget.reserved_cost == 336_864_000
        budget.settle(Usage(1000, 8192, 0, 8192, 9192))
    assert budget.output_tokens == 131072 and budget.cost_nano_usd == 601_824_000
    with pytest.raises(PreflightStop, match="OUTPUT_TOKEN_LIMIT"):
        budget.reserve({})
    assert budget.attempts == 16 and budget.reserved_output == 0


@pytest.mark.parametrize("excess", [0, 1])
def test_output_reservation_boundary(excess: int) -> None:
    budget = Budget(LifeExecutionPlan(), output_tokens=122880 + excess)
    if excess:
        with pytest.raises(PreflightStop, match="OUTPUT_TOKEN_LIMIT"):
            budget.reserve({})
        assert budget.attempts == 0
    else:
        budget.reserve({})
        assert budget.output_tokens + budget.reserved_output == 131072


@pytest.mark.parametrize(
    "status,output,reasoning,error,tick",
    [
        ("completed", 6000, 5950, None, 1),
        ("completed", 8192, 8142, None, 1),
        ("incomplete", 8192, 8192, "INCOMPLETE", 0),
        ("completed", 8193, 8143, "USAGE_EXCEEDS_RESERVATION", 0),
    ],
)
def test_large_usage_accounting_and_replay_without_retry(
    tmp_path: Path, status: str, output: int, reasoning: int, error: str | None, tick: int
) -> None:
    document = json.loads(synthetic_response("" if status == "incomplete" else WAIT, status=status))
    document["usage"].update(output_tokens=output, total_tokens=1000 + output)
    document["usage"]["output_tokens_details"]["reasoning_tokens"] = reasoning
    fake = FakeHTTPS([FakeResponse(json.dumps(document))])
    clock = Clock()
    provider = compose(tmp_path, fake, clock)
    session = PaidLifeSession(provider, clock=clock)
    try:
        if error:
            with pytest.raises(ValueError, match="PAUSED_ERROR"):
                session.run_until(1)
            assert session.decider.state.calls[0].error == error
            assert not session.application.attempts
        else:
            session.run_until(1)
            assert session.decider.state.calls[0].final_result is not None
        assert session.kernel.simulation_time == tick and len(fake.sent) == 1
        assert json.loads(fake.sent[0][2])["max_output_tokens"] == 8192
        if output <= 8192:
            assert provider.budget.cost_nano_usd == 615_000 + output * 4500
            assert provider.budget.reserved_cost == 0
        else:
            assert provider.budget.cost_nano_usd == 0
            assert provider.budget.reserved_cost == 336_864_000
        session.finish()
        report = session.export_report()
        result = replay_paid_report(report)
        assert result["provider_recalled"] is False and result["engine_equal"] is True
        assert len(fake.sent) == 1
        # The new replay must not silently migrate or rebind a legacy report.
        changed = copy.deepcopy(report)
        controller = changed["controller"]
        assert isinstance(controller, dict)
        bound = controller["binding"]
        assert isinstance(bound, dict)
        bound.update(
            source_digest=LEGACY_SOURCE, plan_digest=LEGACY_PLAN, protocol_digest=LEGACY_PROTOCOL
        )
        with pytest.raises(ValueError, match="source/protocol/plan mismatch"):
            replay_paid_report(changed)
    finally:
        session.close()
