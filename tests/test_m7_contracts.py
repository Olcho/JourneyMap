"""Explicit v1 examples and reusable Controller conformance, without providers."""

from collections.abc import Callable
from dataclasses import asdict, fields, replace
from typing import cast

import pytest
from test_game_and_research import execution, future, intent, make_kernel

from journeymap.adapters.human import HumanController
from journeymap.adapters.scripted import ScriptedController
from journeymap.adapters.social_npc import SocialNpcController
from journeymap.application.contracts import (
    ACTION_V1_VALIDATORS,
    normalize_request,
    validate_action_contract,
)
from journeymap.application.reasons import PRIVATE_PURCHASE_REASONS, VISIBLE_DOMAIN_REASONS
from journeymap.application.session import SimulationApplication
from journeymap.application.turns import run_controller_turn
from journeymap.bootstrap import (
    create_alderwick_application,
    create_alderwick_kernel,
    create_application,
)
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json
from journeymap.core.controller import Controller, ControllerActionResult, GameSubmissionError
from journeymap.core.handlers import (
    ActionRequest,
    ActionResult,
    ActionStatus,
    ActionValidationError,
)
from journeymap.core.observations import Observation

# Small compatibility fixture: required/optional keys are asserted independently
# of the implementation's parser table.
EXAMPLES: dict[str, JsonObject] = {
    "MOVE": {"route_id": "west-gate-to-village-square"},
    "WAIT": {"duration": 1},
    "ASK": {"target_actor_id": "thomas", "subject_ref": "east-bridge", "predicate": "condition"},
    "INFORM": {"target_actor_id": "thomas", "claim_record_id": "claim"},
    "REQUEST": {"target_actor_id": "thomas", "request_kind": "help", "request_payload": {}},
    "REST": {"duration": 1},
    "CONSUME": {"item_id": "bread", "quantity": 1},
    "BUY": {"offer_id": "edwin-bread", "quantity": 1},
}


def sample(action: str, payload: JsonObject | None = None) -> ActionRequest:
    return ActionRequest(
        "r", "run", "actor", "obs", 0, action, 1, EXAMPLES[action] if payload is None else payload
    )


@pytest.mark.parametrize("action", EXAMPLES)
def test_exact_v1_payload_contract_examples_and_malformed_variants(action: str) -> None:
    assert set(ACTION_V1_VALIDATORS) == {(a, 1) for a in EXAMPLES}
    request = sample(action)
    validate_action_contract(normalize_request(request))
    for key in request.payload:
        missing = request.payload.copy()
        del missing[key]
        with pytest.raises(ActionValidationError, match="INVALID_PAYLOAD"):
            validate_action_contract(sample(action, missing))
        bad_values: tuple[JsonValue, ...] = (None, True, 1, "")
        if key in {"duration", "quantity"}:
            bad_values = (None, True, 0, -1, 1.0, "1")
        elif key == "request_payload":
            bad_values = (None, True, [], "")
        for bad in bad_values:
            with pytest.raises(ActionValidationError):
                validate_action_contract(sample(action, {**request.payload, key: bad}))
    for key in ("extra", "utterance", "set_values", "due_time", "schema_version"):
        with pytest.raises(ActionValidationError, match="INVALID_PAYLOAD"):
            validate_action_contract(sample(action, {**request.payload, key: "untrusted"}))
    with pytest.raises(ActionValidationError, match="UNKNOWN_ACTION"):
        validate_action_contract(replace(request, schema_version=2))


def test_inform_optional_reply_and_request_nested_data_are_exact() -> None:
    validate_action_contract(sample("INFORM", {**EXAMPLES["INFORM"], "reply_to_event_id": "event"}))
    for bad in (None, "", 1):
        with pytest.raises(ActionValidationError, match="INVALID_PAYLOAD"):
            validate_action_contract(
                sample("INFORM", {**EXAMPLES["INFORM"], "reply_to_event_id": bad})
            )
    payload: JsonObject = {
        **EXAMPLES["REQUEST"],
        "request_payload": {"arbitrary": [True, None, {"world": "only a claim", "amount": 1.5}]},
    }
    validate_action_contract(normalize_request(sample("REQUEST", payload)))


@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), float("-inf"), {1: 2}, (1,), {1}, object(), "\ud800"]
)
def test_mutated_noncanonical_output_never_enters_live_engine(bad: object) -> None:
    kernel = make_kernel()
    kernel.boot()
    try:
        kernel.schedule(future(0))
        app, research = create_application(kernel)
        game = app.game_for("a")
        request = intent(game.observe())
        request.payload["bad"] = cast(JsonValue, bad)
        before = execution(kernel)
        with pytest.raises(GameSubmissionError, match=r"^INVALID_REQUEST$"):
            game.submit(request)
        assert execution(kernel) == before and research.action_traces == ()
        # Invalid non-normalizable input does not reserve an identity.
        request.payload.pop("bad")
        assert game.submit(request).status is ActionStatus.SUCCEEDED
    finally:
        kernel.close()


def test_cyclic_payload_and_non_object_envelope_fail_closed() -> None:
    request = sample("WAIT")
    request.payload["cycle"] = request.payload
    with pytest.raises(GameSubmissionError, match=r"^INVALID_REQUEST$"):
        normalize_request(request)
    for field, bad in (
        ("payload", []),
        ("correlation_id", ""),
        ("submitted_at", True),
        ("schema_version", True),
    ):
        request = sample("WAIT")
        object.__setattr__(request, field, bad)
        with pytest.raises(GameSubmissionError, match=r"^INVALID_REQUEST$"):
            normalize_request(request)


def test_action_request_and_receipt_golden_envelopes() -> None:
    assert asdict(sample("WAIT")) == {
        "action_request_id": "r",
        "run_id": "run",
        "actor_id": "actor",
        "based_on_observation_id": "obs",
        "submitted_at": 0,
        "action_type": "WAIT",
        "schema_version": 1,
        "payload": {"duration": 1},
        "correlation_id": None,
    }
    receipt = ControllerActionResult(
        "r", "run", "actor", ActionStatus.FAILED, "ROUTE_IMPASSABLE", 0, 4
    )
    assert asdict(receipt) == {
        "action_request_id": "r",
        "run_id": "run",
        "actor_id": "actor",
        "status": "FAILED",
        "reason_code": "ROUTE_IMPASSABLE",
        "started_at": 0,
        "resolved_at": 4,
        "schema_version": 1,
    }


def assert_controller_conforms(controller: Controller, observation: Observation) -> None:
    before = observation.content
    first = controller.decide(observation)
    second = controller.decide(observation)
    assert type(first) is ActionRequest
    assert normalize_request(first) == first == second
    validate_action_contract(first)
    assert (first.run_id, first.actor_id, first.based_on_observation_id, first.submitted_at) == (
        observation.run_id,
        observation.actor_id,
        observation.observation_id,
        observation.simulation_time,
    )
    assert observation.content == before
    first.payload.clear()
    assert controller.decide(observation) == second
    assert {name for name in dir(controller) if not name.startswith("_")} == {"decide"}
    assert not hasattr(controller, "__dict__")
    for capability in ("game", "kernel", "research", "rng", "scheduler", "persistence", "world"):
        assert not hasattr(controller, capability)


@pytest.mark.parametrize(
    "factory",
    [ScriptedController, lambda: HumanController(ScriptedController().decide), SocialNpcController],
)
def test_reusable_controller_conformance_over_live_observations(
    factory: Callable[[], Controller],
) -> None:
    kernel = create_alderwick_kernel(social=True, resources=True)
    kernel.boot()
    try:
        app, _ = create_alderwick_application(kernel, social=True, resources=True)
        for actor in ("stranger", "hugh", "thomas"):
            before = execution(kernel)
            assert_controller_conforms(factory(), app.game_for(actor).observe())
            assert execution(kernel) == before
        result = run_controller_turn(app.game_for("stranger"), factory())
        assert result.failure_code is None and result.receipt is not None
    finally:
        kernel.close()


@pytest.mark.parametrize(
    "case",
    [
        "none",
        "dict",
        "object",
        "exception",
        "payload",
        "cycle",
        "actor",
        "run",
        "observation",
        "time",
        "version",
        "unknown",
        "extra",
        "bool",
        "empty_id",
    ],
)
def test_controller_failures_cannot_consume_even_due_now_system_events(case: str) -> None:
    class Bad:
        def decide(self, observation: Observation) -> ActionRequest:
            request = intent(observation)
            if case == "exception":
                raise RuntimeError("PRIVATE")
            arbitrary = {"none": None, "dict": {"duration": 1}, "object": object()}
            if case in arbitrary:
                return cast(ActionRequest, arbitrary[case])
            if case == "payload":
                request.payload["bad"] = float("nan")
            elif case == "cycle":
                request.payload["cycle"] = request.payload
            elif case == "empty_id":
                object.__setattr__(request, "action_request_id", "")
            elif case == "extra":
                request.payload["extra"] = "bad"
            elif case == "bool":
                request.payload["duration"] = True
            else:
                changes: dict[str, dict[str, object]] = {
                    "actor": {"actor_id": "b"},
                    "run": {"run_id": "other"},
                    "observation": {"based_on_observation_id": "unknown"},
                    "time": {"submitted_at": 99},
                    "version": {"schema_version": 2},
                    "unknown": {"action_type": "HIDDEN"},
                }
                for field, value in changes[case].items():
                    object.__setattr__(request, field, value)
            return request

    kernel = make_kernel()
    kernel.boot()
    try:
        kernel.schedule(future(0))
        app, research = create_application(kernel)
        before = execution(kernel)
        result = run_controller_turn(app.game_for("a"), Bad())
        assert result.failure_code is not None and result.receipt is None
        assert result.failure_code in {
            "CONTROLLER_ERROR",
            "INVALID_CONTROLLER_OUTPUT",
            "UNKNOWN_ACTION",
            "INVALID_PAYLOAD",
            "INVALID_DURATION",
        }
        assert execution(kernel) == before
        assert research.action_traces == () and len(research.observations) == 1
        assert "PRIVATE" not in repr(result)
    finally:
        kernel.close()


@pytest.mark.parametrize(
    "factory",
    [ScriptedController, lambda: HumanController(ScriptedController().decide), SocialNpcController],
)
def test_readers_reject_unknown_observation_version(factory: Callable[[], Controller]) -> None:
    observation = Observation("run", "actor", 1, 0, {"sections": []}, schema_version=2)
    with pytest.raises(ValueError, match="unsupported Observation"):
        factory().decide(observation)


@pytest.mark.parametrize(
    "reason", sorted(PRIVATE_PURCHASE_REASONS | {"PRIVATE", "SELF_TRANSFER", "INVALID_PRICE"})
)
@pytest.mark.parametrize("status", [ActionStatus.REJECTED, ActionStatus.FAILED])
def test_receipt_sanitizes_private_purchase_and_uncontracted_diagnostics(
    reason: str, status: ActionStatus
) -> None:
    raw = ActionResult(
        "r", "PRIVATE_HANDLER", None, "PRIVATE_DIGEST", ("PRIVATE_EVENT",), status, reason, 0, 1
    )
    receipt = SimulationApplication._public_result("actor", sample("BUY"), raw)
    assert receipt.reason_code == f"ACTION_{status.value}"
    serialized = repr(asdict(receipt))
    assert "PRIVATE" not in serialized
    assert {field.name for field in fields(receipt)} == {
        "action_request_id",
        "run_id",
        "actor_id",
        "status",
        "reason_code",
        "started_at",
        "resolved_at",
        "schema_version",
    }


def test_visible_reason_allowlist_is_an_explicit_frozen_contract() -> None:
    assert (
        frozenset(
            [
                "INVALID_PAYLOAD",
                "INVALID_DURATION",
                "UNKNOWN_ACTOR",
                "INVALID_ENTITY",
                "MISSING_POSITION",
                "INVALID_POSITION",
                "UNKNOWN_ROUTE",
                "INVALID_ROUTE",
                "UNKNOWN_LOCATION",
                "INVALID_LOCATION",
                "WRONG_ORIGIN",
                "ROUTE_IMPASSABLE",
                "INVALID_TRAVERSAL_COST",
                "INVALID_PASSABILITY",
                "ROUTE_CHANGED",
                "SELF_TARGET",
                "UNKNOWN_TARGET",
                "INVALID_TARGET",
                "TARGET_MISSING_POSITION",
                "TARGET_INVALID_POSITION",
                "OUT_OF_RANGE",
                "INVALID_QUANTITY",
                "UNKNOWN_ITEM",
                "UNKNOWN_INVENTORY_OWNER",
                "INVALID_INVENTORY_STATE",
                "INSUFFICIENT_QUANTITY",
                "MISSING_SURVIVAL_STATE",
                "INVALID_SURVIVAL_STATE",
                "ITEM_NOT_CONSUMABLE",
                "INVALID_WALLET",
                "MISSING_WALLET",
                "UNKNOWN_OFFER",
                "INVALID_OFFER",
                "OFFER_UNAVAILABLE",
                "SELF_PURCHASE",
                "INSUFFICIENT_FUNDS",
                "INSUFFICIENT_STOCK",
                "OFFER_CHANGED",
                "UNKNOWN_SELLER",
                "INVALID_SELLER",
                "SELLER_MISSING_POSITION",
                "SELLER_INVALID_POSITION",
            ]
        )
        == VISIBLE_DOMAIN_REASONS
    )
    assert canonical_json(EXAMPLES["WAIT"]) == '{"duration":1}'


def test_turn_record_survives_observation_failure_and_post_commit_submission_failure() -> None:
    from journeymap.application.observations import ObservationPipeline
    from journeymap.core.events import EventBus, EventEnvelope

    kernel = make_kernel()
    kernel.boot()
    try:
        app, research = create_application(
            kernel, pipeline=ObservationPipeline(max_content_bytes=1)
        )
        before = execution(kernel)
        result = run_controller_turn(app.game_for("a"), ScriptedController())
        assert result.failure_code == "OBSERVATION_UNAVAILABLE"
        assert result.observation is None and research.observations == ()
        assert execution(kernel) == before
    finally:
        kernel.close()

    bus = EventBus()

    def broken(event: EventEnvelope) -> None:
        raise RuntimeError("PRIVATE")

    bus.subscribe(event_type=None, priority=0, module_id="t", subscriber_id="t", subscriber=broken)
    kernel = make_kernel(bus=bus)
    kernel.boot()
    try:
        app, research = create_application(kernel)
        game = app.game_for("a")
        controller = HumanController(lambda o: intent(o, action="MOVE", payload={"route_id": "xy"}))
        result = run_controller_turn(game, controller)
        assert result.failure_code == "SUBMISSION_ERROR"
        assert result.request is not None and result.receipt is None
        assert research.action_results[-1].status is ActionStatus.SUCCEEDED
        before = execution(kernel)
        assert game.submit(result.request).status is ActionStatus.SUCCEEDED
        assert execution(kernel) == before
    finally:
        kernel.close()
