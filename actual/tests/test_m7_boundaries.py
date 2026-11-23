"""Provider/Memory dependency boundaries and actor-only extension envelopes."""

from dataclasses import asdict, fields
from pathlib import Path
from typing import cast

import pytest
from test_architecture import imported_names
from test_game_and_research import execution
from test_resources import kernel_for, local_world

from journeymap.adapters.memory import MemoryContext, MemoryPolicy, NoMemory
from journeymap.adapters.provider import Provider, ProviderRequest, RawModelResponse
from journeymap.bootstrap import create_application
from journeymap.core.canonical import JsonObject
from journeymap.core.handlers import ActionRequest
from journeymap.core.observations import Observation


def test_core_and_domain_never_import_provider_memory_or_controller_adapters() -> None:
    root = Path(__file__).parents[1] / "src" / "journeymap"
    for directory in ("core", "modules"):
        for path in (root / directory).rglob("*.py"):
            assert not any(
                name.startswith(("journeymap.adapters", "openai", "anthropic"))
                for name in imported_names(path)
            )
    allowed = {
        "journeymap.core.canonical",
        "journeymap.core.observations",
        "journeymap.core.handlers",
    }
    for filename in ("human.py", "provider.py", "memory.py"):
        imports = {
            name
            for name in imported_names(root / "adapters" / filename)
            if name.startswith("journeymap")
        }
        assert imports <= allowed


def test_provider_accepts_only_versioned_configuration_and_returns_raw_data() -> None:
    class FakeProvider:
        def generate(self, request: ProviderRequest) -> RawModelResponse:
            assert {f.name for f in fields(request)} == {
                "prompt",
                "prompt_version",
                "model",
                "configuration_version",
                "parameters",
                "schema_version",
            }
            return RawModelResponse("unparsed", {"provider_request_id": "fake-1"})

    provider: Provider = FakeProvider()
    parameters: JsonObject = {"temperature": 0}
    request = ProviderRequest("actor-visible context", "prompt-1", "fake", "config-1", parameters)
    parameters.clear()
    assert request.parameters == {"temperature": 0}
    response = provider.generate(request)
    assert asdict(response) == {
        "text": "unparsed",
        "metadata": {"provider_request_id": "fake-1"},
        "schema_version": 1,
    }
    with pytest.raises(ValueError):
        ProviderRequest("prompt", "1", "fake", "1", {"capability": cast(JsonObject, object())})
    with pytest.raises(ValueError):
        RawModelResponse("raw", {"bad": float("inf")})
    with pytest.raises(ValueError):
        ProviderRequest("prompt", "1", "fake", "1", schema_version=2)


def test_memory_accepts_only_ordered_actor_context_and_no_memory_selects_nothing() -> None:
    first = Observation("r", "a", 1, 0, {"sections": []})
    second = Observation("r", "a", 2, 1, {"sections": []})
    current = Observation("r", "a", 3, 2, {"sections": []})
    memory: MemoryPolicy = NoMemory()
    context = MemoryContext(current, (first, second))
    assert memory.select(context) == ()
    assert {f.name for f in fields(context)} == {"current", "prior"}
    for prior in (
        (second, first),
        (first, first),
        (current,),
        (Observation("r", "b", 1, 0, {"sections": []}),),
        (Observation("other", "a", 1, 0, {"sections": []}),),
        (Observation("r", "a", 1, 3, {"sections": []}),),
        (Observation("r", "a", 1, 0, {"sections": []}, 2),),
    ):
        with pytest.raises(ValueError):
            MemoryContext(current, prior)


@pytest.mark.parametrize(
    "private", ["missing-wallet", "invalid-wallet", "missing-stock", "invalid-stock"]
)
def test_private_seller_resource_shape_has_no_distinct_actor_diagnostic(private: str) -> None:
    state = local_world()
    trade, inventory = state["trade"], state["inventory"]
    assert isinstance(trade, dict) and isinstance(inventory, dict)
    wallets, owners = trade["wallets"], inventory["owners"]
    assert isinstance(wallets, dict) and isinstance(owners, dict)
    if private == "missing-wallet":
        del wallets["edwin"]
    elif private == "invalid-wallet":
        wallets["edwin"] = "PRIVATE"
    elif private == "missing-stock":
        del owners["edwin"]
    else:
        owners["edwin"] = "PRIVATE"
    kernel = kernel_for(state)
    try:
        app, research = create_application(kernel)
        game = app.game_for("stranger")
        observation = game.observe()
        before = execution(kernel)
        receipt = game.submit(
            ActionRequest(
                "buy",
                observation.run_id,
                "stranger",
                observation.observation_id,
                0,
                "BUY",
                1,
                {"offer_id": "edwin-bread", "quantity": 1},
            )
        )
        assert receipt.reason_code == "ACTION_REJECTED"
        assert research.action_traces[-1].result is not None
        assert execution(kernel)[:-1] == before[:-1]
        assert "PRIVATE" not in repr(asdict(receipt))
    finally:
        kernel.close()
