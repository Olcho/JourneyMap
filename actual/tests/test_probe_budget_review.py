"""Actual frozen admission gates under explicit synthetic usage, never estimates."""

import pytest
from test_probe_execution import raw

from journeymap.adapters.llm import strict_json
from journeymap.adapters.probe_execution import Ledger, ProbePlan, classify, object_
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.experiments import life_reasoning_probe as probe


def frozen_ledger_trace(input_tokens: int, output_tokens: int) -> JsonObject:
    ledger, seen = Ledger(), set[str]()
    trace: list[JsonValue] = []
    for index in range(72):
        blocked = ledger.blocked()
        if blocked is not None:
            break
        ledger.reserve()
        doc = object_(strict_json(raw(id=f"budget-fixture-{index}")))
        doc["usage"] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": output_tokens},
        }
        result = classify(
            {"raw_response": canonical_json(doc), "http_status": 200, "elapsed_ms": 1}, seen
        )
        assert result["classification"] == "READY"
        ledger.settle(result)
        trace.append({"slot": index + 1, "ledger": ledger.data})
    return {
        "usage_kind": "literal synthetic scenario, not provider usage or input estimate",
        "input_per_slot": input_tokens,
        "output_per_slot": output_tokens,
        "attempted": ledger.attempts,
        "stop": ledger.blocked(),
        "ledger": ledger.data,
        "trace": trace,
    }


@pytest.mark.parametrize(
    "input_tokens,output_tokens,attempts,stop",
    [
        (1000, 100, 72, "REQUEST_LIMIT"),
        (1000, 8192, 18, "COST_LIMIT"),
        (1500, 100, 67, "INPUT_TOKEN_LIMIT"),
        (100001, 0, 1, "INPUT_TOKEN_LIMIT"),
        (400000, 8192, 1, "INPUT_TOKEN_LIMIT"),
        (0, 8192, 18, "COST_LIMIT"),
    ],
)
def test_frozen_actual_ledger_stops_at_the_predicted_slot(
    input_tokens: int,
    output_tokens: int,
    attempts: int,
    stop: str,
) -> None:
    result = frozen_ledger_trace(input_tokens, output_tokens)
    assert result["attempted"] == attempts and result["stop"] == stop


def test_exact_input_output_and_cost_reservation_boundaries() -> None:
    assert Ledger(input_tokens=100_000).blocked() is None
    assert Ledger(input_tokens=100_001).blocked() == "INPUT_TOKEN_LIMIT"
    assert Ledger(output_tokens=589_824 - 8192).blocked() is None
    assert Ledger(output_tokens=589_824 - 8192 + 1).blocked() == "OUTPUT_TOKEN_LIMIT"
    assert Ledger(known_cost_nano_usd=663_136_000).blocked() is None
    assert Ledger(known_cost_nano_usd=663_136_001).blocked() == "COST_LIMIT"


def test_future_caps_are_arithmetic_review_not_supported_plan_changes() -> None:
    plan = ProbePlan()
    assert plan.cost_limit_nano_usd == 1_000_000_000 and plan.total_input_tokens == 500_000
    assert 72 * plan.max_output_tokens == 589_824
    assert 500_000 * plan.input_rate + 589_824 * plan.output_rate == 3_029_208_000
    assert 72 * plan.context_input_reserve == 28_800_000
    assert 72 * plan.reservation == 24_254_208_000
    with pytest.raises(ValueError, match="new version"):
        ProbePlan(cost_limit_nano_usd=3_030_000_000)
    with pytest.raises(ValueError, match="new version"):
        ProbePlan(total_input_tokens=28_800_000)
    assert probe.CONDITIONS == ("high_baseline", "medium_baseline", "medium_structured")
