"""M5 actor interaction contracts and canonical failure atomicity."""

from dataclasses import replace

import pytest
from test_alderwick import execution, obj

from journeymap.bootstrap import create_application, create_kernel
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.core.handlers import (
    ActionHandlerNotFoundError,
    ActionRegistry,
    ActionRequest,
    ActionStatus,
    ResolutionContext,
    SystemEventRegistry,
    TransitionPlan,
    ValidationContext,
)
from journeymap.core.kernel import SimulationKernel
from journeymap.core.scheduler import ScheduledEvent, ScheduledEventSpec
from journeymap.modules.social import SocialModule
from journeymap.scenarios.alderwick.fixture import initial_world


def social_world() -> JsonObject:
    state = initial_world()
    obj(obj(obj(state["movement"])["positions"])["hugh"])["location_id"] = "village-square"
    return state


def payload(action: str) -> JsonObject:
    data: dict[str, JsonObject] = {
        "ASK": {
            "target_actor_id": "thomas",
            "subject_ref": "east-bridge",
            "predicate": "condition",
        },
        "INFORM": {"target_actor_id": "thomas", "claim_record_id": "trusted-record"},
        "REQUEST": {
            "target_actor_id": "thomas",
            "request_kind": "test-only",
            "request_payload": {"x": [1]},
        },
    }
    return data[action]


def request(action: str, data: JsonObject | None = None) -> ActionRequest:
    return ActionRequest(
        "social-1",
        "run-default",
        "hugh",
        "opaque",
        0,
        action,
        1,
        payload(action) if data is None else data,
    )


class ChangeTarget:
    """Test-only independent system changes while an interaction consumes time."""

    handler_id = "test.change-target.v1"

    def validate(self, event: ScheduledEvent, context: ValidationContext) -> None:
        pass

    def resolve(self, event: ScheduledEvent, context: ResolutionContext) -> TransitionPlan:
        state = context.state
        actor = str(event.payload.get("actor", "thomas"))
        kind = event.payload["kind"]
        positions = obj(obj(state["movement"])["positions"])
        if kind == "entity":
            del obj(state["entities"])[actor]
        elif kind == "position":
            del positions[actor]
        elif kind == "invalid":
            obj(positions[actor])["actor_id"] = "wrong"
        elif kind == "location":
            del obj(obj(state["movement"])["locations"])["village-square"]
        else:
            obj(positions[actor])["location_id"] = "inn"
        return TransitionPlan(set_values=state)


def factory(state: JsonObject | None = None) -> SimulationKernel:
    actions, systems = ActionRegistry(), SystemEventRegistry()
    SocialModule().register_actions(actions)
    systems.register("ChangeTarget", 1, ChangeTarget())
    return create_kernel(
        initial_state=social_world() if state is None else state,
        action_registry=actions,
        system_event_registry=systems,
    )


@pytest.mark.parametrize(
    "action,event_type",
    [
        ("ASK", "ActorAsked"),
        ("INFORM", "ActorInformed"),
        ("REQUEST", "ActorRequested"),
    ],
)
def test_social_success_only_commits_the_interaction(action: str, event_type: str) -> None:
    kernel = factory()
    kernel.boot()
    try:
        before = kernel.state_snapshot, kernel.state_digest, kernel.rng_snapshot
        original = request(action)
        result = kernel.submit_action(original)
        assert result.status == ActionStatus.SUCCEEDED
        assert (result.started_at, result.resolved_at) == (0, 1)
        assert (kernel.state_snapshot, kernel.state_digest, kernel.rng_snapshot) == before
        (event,) = kernel.events
        assert (event.event_type, event.schema_version, event.source_kind) == (
            event_type,
            1,
            "ACTION",
        )
        assert event.source_ref == original.action_request_id
        assert (
            result.transition is not None and event.transition_id == result.transition.transition_id
        )
        assert event.payload == {
            **original.payload,
            "sender_actor_id": "hugh",
            "location_id": "village-square",
        }
        original.payload.clear()
        event.payload.clear()
        assert kernel.events[0].payload["target_actor_id"] == "thomas"
    finally:
        kernel.close()


@pytest.mark.parametrize("action", ["ASK", "INFORM", "REQUEST"])
@pytest.mark.parametrize(
    "fault,reason",
    [
        ("unknown-sender", "UNKNOWN_ACTOR"),
        ("invalid-sender", "INVALID_ENTITY"),
        ("unknown-target", "UNKNOWN_TARGET"),
        ("invalid-target", "INVALID_TARGET"),
        ("self", "SELF_TARGET"),
        ("sender-missing", "MISSING_POSITION"),
        ("sender-invalid", "INVALID_POSITION"),
        ("target-missing", "TARGET_MISSING_POSITION"),
        ("target-invalid", "TARGET_INVALID_POSITION"),
        ("range", "OUT_OF_RANGE"),
        ("sender-location", "INVALID_POSITION"),
        ("target-location", "TARGET_INVALID_POSITION"),
    ],
)
def test_canonical_interaction_rejection_is_atomic(action: str, fault: str, reason: str) -> None:
    state, data = social_world(), payload(action)
    positions = obj(obj(state["movement"])["positions"])
    entities = obj(state["entities"])
    if fault == "self":
        data["target_actor_id"] = "hugh"
    elif fault.startswith("unknown"):
        del entities["hugh" if fault.endswith("sender") else "thomas"]
    elif fault.startswith("invalid"):
        entities["hugh" if fault.endswith("sender") else "thomas"] = []
    else:
        actor = "hugh" if fault.startswith("sender") else "thomas"
        if fault.endswith("missing"):
            del positions[actor]
        elif fault.endswith("invalid"):
            obj(positions[actor])["actor_id"] = "wrong"
        else:
            obj(positions[actor])["location_id"] = "inn" if fault == "range" else "absent"
    kernel = factory(state)
    kernel.boot()
    try:
        before = execution(kernel)
        result = kernel.submit_action(request(action, data))
        assert (result.status, result.reason_code) == (ActionStatus.REJECTED, reason)
        assert result.transition is None and result.emitted_event_ids == ()
        assert execution(kernel) == before
    finally:
        kernel.close()


@pytest.mark.parametrize("action", ["ASK", "INFORM", "REQUEST"])
@pytest.mark.parametrize("fault", ["missing", "extra", "target-empty", "target-type"])
def test_exact_payload_schema(action: str, fault: str) -> None:
    data = payload(action)
    if fault == "missing":
        del data["target_actor_id"]
    elif fault == "extra":
        data["set_values"] = {"alderwick": {"condition": "intact"}}
    else:
        data["target_actor_id"] = "" if fault == "target-empty" else False
    kernel = factory()
    kernel.boot()
    try:
        before = execution(kernel)
        assert kernel.submit_action(request(action, data)).reason_code == "INVALID_PAYLOAD"
        assert execution(kernel) == before
    finally:
        kernel.close()


@pytest.mark.parametrize(
    "action,field,bad",
    [
        ("ASK", "subject_ref", []),
        ("ASK", "predicate", ""),
        ("INFORM", "claim_record_id", {}),
        ("INFORM", "reply_to_event_id", None),
        ("REQUEST", "request_kind", 1),
        ("REQUEST", "request_payload", []),
    ],
)
def test_action_specific_payload_types(action: str, field: str, bad: JsonValue) -> None:
    kernel = factory()
    kernel.boot()
    try:
        data = payload(action)
        data[field] = bad
        before = execution(kernel)
        assert kernel.submit_action(request(action, data)).reason_code == "INVALID_PAYLOAD"
        assert execution(kernel) == before
    finally:
        kernel.close()


@pytest.mark.parametrize("action", ["ASK", "INFORM", "REQUEST"])
def test_wrong_version_has_no_fallback(action: str) -> None:
    kernel = factory()
    kernel.boot()
    try:
        app, research = create_application(kernel, social=True)
        game = app.game_for("hugh")
        observation = game.observe()
        invalid = replace(
            request(action), schema_version=2, based_on_observation_id=observation.observation_id
        )
        before = execution(kernel)
        assert game.submit(invalid).reason_code == "UNKNOWN_ACTION"
        assert research.action_traces[-1].boundary_reason == "UNKNOWN_ACTION"
        with pytest.raises(ActionHandlerNotFoundError):
            kernel.submit_action(invalid)
        assert execution(kernel) == before
    finally:
        kernel.close()


@pytest.mark.parametrize("action", ["ASK", "INFORM", "REQUEST"])
@pytest.mark.parametrize(
    "actor,kind,reason",
    [
        ("thomas", "entity", "UNKNOWN_TARGET"),
        ("hugh", "entity", "UNKNOWN_ACTOR"),
        ("thomas", "position", "TARGET_MISSING_POSITION"),
        ("hugh", "invalid", "INVALID_POSITION"),
        ("thomas", "location", "INVALID_POSITION"),
        ("thomas", "move", "OUT_OF_RANGE"),
    ],
)
def test_completion_revalidation_retains_independent_system_commit(
    action: str,
    actor: str,
    kind: str,
    reason: str,
) -> None:
    kernel, control = factory(), factory()
    for instance in (kernel, control):
        instance.boot()
        instance.schedule(
            ScheduledEventSpec(1, 0, "ChangeTarget", 1, {"actor": actor, "kind": kind})
        )
    try:
        control.advance_to(1)
        result = kernel.submit_action(request(action))
        assert (result.status, result.reason_code) == (ActionStatus.FAILED, reason)
        assert (result.started_at, result.resolved_at) == (0, 1)
        assert execution(kernel) == execution(control)
        assert kernel.events == ()
    finally:
        kernel.close()
        control.close()
