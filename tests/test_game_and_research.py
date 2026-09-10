"""Adversarial M3 Knowledge Leak/Authority gates and unchanged engine replay."""

from collections.abc import Callable, Iterator
from dataclasses import asdict, fields, replace
from typing import cast

import pytest

from journeymap.application.observations import ObservationPipeline
from journeymap.application.perception import perceive
from journeymap.application.research import ResearchView
from journeymap.application.session import SimulationApplication
from journeymap.bootstrap import create_application, create_kernel
from journeymap.core.actions import WaitHandler
from journeymap.core.canonical import JsonObject, JsonValue, canonical_json, state_digest
from journeymap.core.controller import GamePort, GameSubmissionError
from journeymap.core.entities import Entity, entity_state
from journeymap.core.events import EventBus, EventDraft, EventEnvelope
from journeymap.core.handlers import (
    ActionRegistry,
    ActionRequest,
    ActionStatus,
    ActionValidationError,
    HandlerNotFoundError,
    ResolutionContext,
    SystemEventRegistry,
    TransitionPlan,
    ValidationContext,
)
from journeymap.core.kernel import SimulationKernel
from journeymap.core.observations import Observation, PerceptionContext
from journeymap.core.replay import ReplayHarness, ReplayInput
from journeymap.core.run import RunManifest
from journeymap.core.scheduler import ScenarioSchedule, ScheduledEvent, ScheduledEventSpec
from journeymap.modules.knowledge import KnowledgeLedger, KnowledgeModule, KnowledgeRecord
from journeymap.modules.movement import (
    ActorPosition,
    Location,
    MovementModule,
    Route,
    movement_state,
)


def obj(value: JsonValue) -> JsonObject:
    assert isinstance(value, dict)
    return value


def world(secret: str = "SECRET") -> JsonObject:
    state: JsonObject = {
        "entities": entity_state(
            (Entity("a", "person"), Entity("b", "person"), Entity("hidden", "object"))
        ),
        "movement": movement_state(
            locations=(Location("x"), Location("y"), Location("unseen")),
            routes=(Route("xy", "x", "y", 4), Route("hidden-route", "y", "unseen", 90)),
            positions=(ActorPosition("a", "x"), ActorPosition("b", "y")),
        ),
        "private": {"value": secret, "owner": "b"},
        "debug": {"seed": secret, "rng_state": secret},
    }
    # Secrets inside otherwise legitimate module/identity rows catch accidental
    # whole-record projection, not just a missing top-level denylist entry.
    obj(obj(state["entities"])["a"])["hidden_metadata"] = secret
    movement = obj(state["movement"])
    obj(obj(movement["positions"])["a"])["debug"] = secret
    obj(obj(movement["locations"])["x"])["unseen_fact"] = secret
    obj(obj(movement["routes"])["xy"])["private"] = secret
    return state


def known(actor: str, value: str) -> KnowledgeRecord:
    return KnowledgeRecord(
        f"knowledge-{actor}",
        "run-m3",
        actor,
        "unverified-subject",
        "claim",
        {"value": value},
        "INITIAL",
        f"scenario:initial:{actor}",
        0,
    )


class HiddenSystem:
    handler_id = "test.hidden.v1"

    def validate(self, event: ScheduledEvent, context: ValidationContext) -> None:
        pass

    def resolve(self, event: ScheduledEvent, context: ResolutionContext) -> TransitionPlan:
        movement = obj(context.state["movement"])
        obj(obj(movement["routes"])["xy"])["passable"] = False
        return TransitionPlan(
            set_values={"movement": movement, "private": {"changed": context.rng.next_u64()}},
            events=(EventDraft("HiddenChanged", 1, {"secret": "EVENT-SECRET"}),),
        )


def make_kernel(
    secret: str = "SECRET",
    *,
    run_id: str = "run-m3",
    bus: EventBus | None = None,
    action_handler: WaitHandler | None = None,
) -> SimulationKernel:
    state = world(secret)
    actions = ActionRegistry()
    movement = MovementModule()
    movement.register_actions(actions)
    actions.register("WAIT", 1, action_handler or WaitHandler())
    systems = SystemEventRegistry()
    systems.register("HIDDEN", 1, HiddenSystem())
    return create_kernel(
        modules=(movement, KnowledgeModule()),
        initial_state=state,
        manifest=RunManifest(run_id, "m3-fixture", "1", "test", 1, 918273, 0, state_digest(state)),
        action_registry=actions,
        system_event_registry=systems,
        event_bus=bus,
    )


def future(tick: int = 10) -> ScheduledEventSpec:
    return ScheduledEventSpec(tick, 0, "HIDDEN", 1, {"secret": "SCHEDULE-SECRET"})


@pytest.fixture
def session() -> Iterator[tuple[SimulationKernel, SimulationApplication, ResearchView]]:
    kernel = make_kernel()
    kernel.boot()
    application, research = create_application(
        kernel, initial_knowledge=(known("a", "A-PRIVATE"), known("b", "B-PRIVATE"))
    )
    kernel.schedule(future())
    yield kernel, application, research
    kernel.close()


def intent(
    observation: Observation, *, action: str = "WAIT", payload: JsonObject | None = None
) -> ActionRequest:
    return ActionRequest(
        "request-1",
        observation.run_id,
        observation.actor_id,
        observation.observation_id,
        observation.simulation_time,
        action,
        1,
        {"duration": 1} if payload is None else payload,
    )


def execution(kernel: SimulationKernel) -> tuple[object, ...]:
    return (
        kernel.state_snapshot,
        kernel.state_digest,
        kernel.simulation_time,
        kernel.rng_snapshot,
        kernel.transition_sequence,
        kernel.events,
        kernel.pending_scheduled_events,
        kernel.system_event_outcomes,
        kernel.action_results,
    )


def sections(observation: Observation) -> dict[str, JsonObject]:
    content = observation.content["sections"]
    assert isinstance(content, list)
    return {str(obj(section)["module_id"]): obj(obj(section)["content"]) for section in content}


def test_knowledge_leak_uses_positive_allowlists_and_hidden_counterexamples(
    session: tuple[SimulationKernel, SimulationApplication, ResearchView],
) -> None:
    kernel, application, research = session
    before = execution(kernel)
    for actor, location, value, forbidden in (
        ("a", "x", "A-PRIVATE", "B-PRIVATE"),
        ("b", "y", "B-PRIVATE", "A-PRIVATE"),
    ):
        observation = application.game_for(actor).observe()
        assert sections(observation) == {
            "core": {"self": {"entity_id": actor, "entity_type": "person"}},
            "movement": {"position": {"location_id": location}},
            "knowledge": {"records": [known(actor, value).to_json()]},
        }
        serialized = canonical_json(observation.content)
        for hidden in (
            "SECRET",
            forbidden,
            "unseen",
            "hidden-route",
            "rng",
            "seed",
            "debug",
            "scheduler",
        ):
            assert hidden not in serialized
    assert research.observations[0].content != research.observations[1].content
    assert execution(kernel) == before
    assert research.world_snapshot == world()
    assert research.manifest.seed == 918273
    assert research.pending_scheduled_events[0].payload == {"secret": "SCHEDULE-SECRET"}


def test_hidden_truth_schedule_and_other_knowledge_changes_do_not_change_own_observation() -> None:
    observed: list[Observation] = []
    digests: list[str] = []
    for value in ("SECRET-ONE", "SECRET-TWO"):
        kernel = make_kernel(value)
        kernel.boot()
        kernel.schedule(ScheduledEventSpec(100, 2, "HIDDEN", 1, {"changed": value}))
        application, _ = create_application(
            kernel, initial_knowledge=(known("a", "unchanged"), known("b", value))
        )
        observed.append(application.game_for("a").observe())
        digests.append(kernel.state_digest)
        kernel.close()
    assert digests[0] != digests[1]
    assert observed[0] == observed[1]


def test_new_contributor_receives_only_scoped_data_and_no_capabilities() -> None:
    received: list[PerceptionContext] = []
    pipeline = ObservationPipeline()

    def echo_everything(context: PerceptionContext) -> JsonObject:
        received.append(context)
        # Serializing the entire supplied context must still be safe; this also
        # rejects service objects rather than only searching for secret strings.
        assert {field.name for field in fields(context)} == {
            "run_id",
            "actor_id",
            "simulation_time",
            "perceived",
            "known",
        }
        return cast(JsonObject, asdict(context))

    pipeline.register(
        priority=0, module_id="test", contributor_id="echo", contributor=echo_everything
    )
    kernel = make_kernel()
    kernel.boot()
    kernel.schedule(future())
    app, _ = create_application(
        kernel, pipeline=pipeline, initial_knowledge=(known("b", "B-PRIVATE"),)
    )
    observation = app.game_for("a").observe()
    assert received[0].perceived == {
        "self": {"entity_id": "a", "entity_type": "person"},
        "movement": {"location_id": "x"},
    }
    assert received[0].known == {"records": []}
    assert "SECRET" not in canonical_json(observation.content)
    received[0].perceived.clear()
    assert app.game_for("a").observe().content == observation.content
    kernel.close()


def test_controller_has_only_bound_game_methods_and_actor_visible_receipts(
    session: tuple[SimulationKernel, SimulationApplication, ResearchView],
) -> None:
    kernel, app, research = session
    game = app.game_for("a")
    assert {name for name in dir(game) if not name.startswith("_")} == {"observe", "submit"}
    for capability in (
        "kernel",
        "state_snapshot",
        "world_snapshot",
        "schedule",
        "advance_to",
        "research",
        "system_event_registry",
        "rng_snapshot",
        "game_for",
        "knowledge",
        "events",
    ):
        assert not hasattr(game, capability)
    with pytest.raises(TypeError):
        cast(Callable[..., Observation], game.observe)("b")
    with pytest.raises(ValueError, match="unknown Game actor"):
        app.game_for("nonexistent")
    observation = game.observe()
    receipt = game.submit(intent(observation))
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
    assert receipt.status is ActionStatus.SUCCEEDED
    assert receipt.resolved_at == kernel.simulation_time == 1
    assert research.action_traces[0].result == kernel.action_results[0]
    assert not hasattr(research, "submit") and not hasattr(research, "advance_to")


@pytest.mark.parametrize(
    "forgery", ["actor", "run", "other-observation", "unknown", "foreign-observation", "time"]
)
def test_authority_denials_never_reach_kernel_and_are_traced(
    session: tuple[SimulationKernel, SimulationApplication, ResearchView],
    forgery: str,
) -> None:
    kernel, app, research = session
    game = app.game_for("a")
    observation = game.observe()
    other = app.game_for("b").observe()
    request = intent(observation)
    if forgery == "actor":
        request = replace(request, actor_id="b")
    elif forgery == "run":
        request = replace(request, run_id="another-run")
    elif forgery == "other-observation":
        request = replace(request, based_on_observation_id=other.observation_id)
    elif forgery == "time":
        request = replace(request, submitted_at=10)
    else:
        reference = "nonexistent"
        if forgery == "foreign-observation":
            foreign = make_kernel(run_id="foreign-run")
            foreign.boot()
            foreign_app, _ = create_application(foreign)
            reference = foreign_app.game_for("a").observe().observation_id
            foreign.close()
        request = replace(request, based_on_observation_id=reference)
    before = execution(kernel)
    result = game.submit(request)
    assert result.status is ActionStatus.REJECTED
    assert execution(kernel) == before
    trace = research.action_traces[-1]
    assert trace.request == request
    assert trace.result is None
    assert trace.boundary_reason == result.reason_code
    assert trace.controller_result == result
    assert result.actor_id == "a" and result.run_id == "run-m3"


@pytest.mark.parametrize(
    ("action", "payload", "reason"),
    [
        ("HIDDEN", {}, "UNKNOWN_ACTION"),
        ("unregistered", {}, "UNKNOWN_ACTION"),
        ("WAIT", {"duration": 1, "event_type": "HIDDEN", "due_time": 0}, "INVALID_PAYLOAD"),
        ("MOVE", {"route_id": "xy", "set_values": {"private": "overwritten"}}, "INVALID_PAYLOAD"),
    ],
)
def test_system_shaped_requests_and_mutation_payloads_cannot_gain_authority(
    session: tuple[SimulationKernel, SimulationApplication, ResearchView],
    action: str,
    payload: JsonObject,
    reason: str,
) -> None:
    kernel, app, research = session
    game = app.game_for("a")
    before = execution(kernel)
    result = game.submit(intent(game.observe(), action=action, payload=payload))
    assert result.status is ActionStatus.REJECTED and result.reason_code == reason
    # Registered validation failures add only the usual kernel ActionResult.
    assert execution(kernel)[:-1] == before[:-1]
    assert research.system_event_outcomes == ()
    assert len(research.action_traces) == 1
    with pytest.raises(TypeError, match="only ActionRequest"):
        game.submit(cast(ActionRequest, future()))
    assert len(research.action_traces) == 1


def test_initial_knowledge_is_not_seeded_or_rewritten_by_observe_move_or_system_events(
    session: tuple[SimulationKernel, SimulationApplication, ResearchView],
) -> None:
    kernel, app, research = session
    before = (research.knowledge_history("a"), research.knowledge_history("b"))
    game = app.game_for("a")
    result = game.submit(intent(game.observe(), action="MOVE", payload={"route_id": "xy"}))
    assert result.status is ActionStatus.SUCCEEDED
    assert sections(game.observe())["movement"] == {"position": {"location_id": "y"}}
    kernel.advance_to(10)
    assert research.events[-1].event_type == "HiddenChanged"
    assert (research.knowledge_history("a"), research.knowledge_history("b")) == before
    assert "EVENT-SECRET" not in canonical_json(game.observe().content)
    assert len(research.action_traces) == len(research.action_results) == 1
    assert len(research.system_event_outcomes) == 1
    record = research.knowledge_history("a")[0]
    obj(record.value).clear()
    assert research.knowledge_history("a") == before[0]
    snapshot = research.world_snapshot
    snapshot.clear()
    research.events[-1].payload.clear()
    assert research.world_snapshot and research.events[-1].payload


def test_trace_request_payloads_are_detached_and_observation_reuse_is_not_expiry_policy(
    session: tuple[SimulationKernel, SimulationApplication, ResearchView],
) -> None:
    kernel, app, research = session
    game = app.game_for("a")
    observation = game.observe()
    request = intent(observation)
    game.submit(request)
    request.payload["duration"] = 999
    first = research.action_traces[0]
    first.request.payload.clear()
    assert research.action_traces[0].request.payload == {"duration": 1}
    # Repeated IDs/reuse are preserved as separate attempts, not deduplicated.
    second_request = replace(intent(observation), submitted_at=kernel.simulation_time)
    game.submit(second_request)
    assert [trace.attempt_sequence for trace in research.action_traces] == [1, 2]
    assert all(
        trace.request.based_on_observation_id == observation.observation_id
        for trace in research.action_traces
    )
    assert [trace.result for trace in research.action_traces] == list(kernel.action_results)
    assert len(research.observations) == 1


def test_live_action_trace_replays_without_observation_stream_or_application() -> None:
    kernel = make_kernel()
    kernel.boot()
    kernel.schedule(future(2))
    app, research = create_application(kernel)
    game = app.game_for("a")
    game.submit(intent(game.observe(), action="MOVE", payload={"route_id": "xy"}))
    game.submit(replace(intent(game.observe()), action_request_id="wait"))
    assert [trace.result.status for trace in research.action_traces if trace.result] == [
        ActionStatus.FAILED,
        ActionStatus.SUCCEEDED,
    ]
    recorded = tuple(trace.request for trace in research.action_traces)
    inputs = ReplayInput(ScenarioSchedule("m3-fixture", "1", (future(2),)), recorded, 5)
    first = ReplayHarness(make_kernel).run(inputs)
    second = ReplayHarness(make_kernel).run(inputs)
    assert first == second
    assert first.action_results == kernel.action_results
    assert first.events == kernel.events
    assert first.system_event_outcomes == kernel.system_event_outcomes
    assert first.final_state == kernel.state_snapshot
    assert first.final_state_digest == kernel.state_digest
    assert first.rng_draw_count == kernel.rng_snapshot.draw_count
    assert first.final_simulation_time == kernel.simulation_time
    kernel.close()


def test_post_commit_delivery_error_retains_trace_and_hides_debug_details() -> None:
    bus = EventBus()

    def broken(event: EventEnvelope) -> None:
        raise RuntimeError("DEBUG-SECRET")

    bus.subscribe(
        event_type=None, priority=0, module_id="secret", subscriber_id="debug", subscriber=broken
    )
    kernel = make_kernel(bus=bus)
    kernel.boot()
    app, research = create_application(kernel)
    game = app.game_for("a")
    request = intent(game.observe(), action="MOVE", payload={"route_id": "xy"})
    with pytest.raises(GameSubmissionError, match=r"^ENGINE_ERROR$"):
        game.submit(request)
    trace = research.action_traces[0]
    assert trace.error_type == "EventDeliveryError"
    assert trace.result == kernel.action_results[0]
    assert trace.result.status is ActionStatus.SUCCEEDED
    assert trace.controller_result is None
    assert kernel.simulation_time == 4
    assert sections(game.observe())["movement"] == {"position": {"location_id": "y"}}
    kernel.close()


def test_system_registry_defect_is_not_reported_as_unknown_actor_action() -> None:
    kernel = make_kernel()
    kernel.boot()
    kernel.schedule(ScheduledEventSpec(1, 0, "SECRET-MISSING-SYSTEM", 1, {}))
    app, research = create_application(kernel)
    game = app.game_for("a")
    with pytest.raises(GameSubmissionError, match=r"^ENGINE_ERROR$"):
        game.submit(intent(game.observe()))
    trace = research.action_traces[0]
    assert trace.error_type == "HandlerNotFoundError"
    assert trace.result is None and trace.controller_result is None
    assert kernel.action_results == ()
    assert kernel.simulation_time == 0
    kernel.close()


def test_custom_handler_diagnostics_are_not_automatically_actor_visible() -> None:
    class PrivateReason(WaitHandler):
        def validate(self, request: ActionRequest, context: ValidationContext) -> None:
            raise ActionValidationError("DEBUG-SECRET")

    kernel = make_kernel(action_handler=PrivateReason())
    kernel.boot()
    app, research = create_application(kernel)
    game = app.game_for("a")
    result = game.submit(intent(game.observe()))
    assert result.reason_code == "ACTION_REJECTED"
    assert research.action_results[0].reason_code == "DEBUG-SECRET"
    kernel.close()


@pytest.mark.parametrize("cycle", [False, True])
def test_noncanonical_mutated_request_does_not_enter_engine_or_record_invalid_json(
    session: tuple[SimulationKernel, SimulationApplication, ResearchView],
    cycle: bool,
) -> None:
    kernel, app, research = session
    game = app.game_for("a")
    request = intent(game.observe())
    if cycle:
        request.payload["cycle"] = request.payload
    else:
        request.payload["duration"] = float("nan")
    before = execution(kernel)
    with pytest.raises(GameSubmissionError, match="INVALID_REQUEST"):
        game.submit(request)
    assert execution(kernel) == before
    assert research.action_traces == ()


def test_game_cannot_be_reentered_from_a_subscriber() -> None:
    bus = EventBus()
    game: GamePort | None = None

    def reenter(event: EventEnvelope) -> None:
        assert game is not None
        game.observe()

    bus.subscribe(
        event_type=None, priority=0, module_id="test", subscriber_id="reenter", subscriber=reenter
    )
    kernel = make_kernel(bus=bus)
    kernel.boot()
    app, research = create_application(kernel)
    game = app.game_for("a")
    with pytest.raises(GameSubmissionError, match="ENGINE_ERROR"):
        game.submit(intent(game.observe(), action="MOVE", payload={"route_id": "xy"}))
    assert len(research.observations) == 1
    assert len(research.action_traces) == 1
    kernel.close()


def test_boot_lifecycle_and_initial_knowledge_owner_validation() -> None:
    kernel = make_kernel()
    app, research = create_application(kernel)
    game = app.game_for("a")
    with pytest.raises(GameSubmissionError, match="GAME_UNAVAILABLE"):
        game.observe()
    with pytest.raises(ValueError, match="owner"):
        create_application(kernel, initial_knowledge=(known("absent", "value"),))
    assert len(research.observations) == 0
    kernel.boot()
    observation = game.observe()
    kernel.close()
    with pytest.raises(GameSubmissionError, match="GAME_UNAVAILABLE"):
        game.submit(intent(observation))


@pytest.mark.parametrize(
    "field",
    [
        "action_request_id",
        "run_id",
        "actor_id",
        "based_on_observation_id",
        "action_type",
        "correlation_id",
    ],
)
def test_live_envelope_rejects_mutable_identity_before_dispatch(
    session: tuple[SimulationKernel, SimulationApplication, ResearchView], field: str
) -> None:
    kernel, app, research = session
    game = app.game_for("a")
    observation = game.observe()
    mutable_id = ["caller-owned"]
    request = cast(Callable[..., ActionRequest], replace)(
        intent(observation), **{field: mutable_id}
    )
    before = execution(kernel)
    with pytest.raises(GameSubmissionError, match=r"^INVALID_REQUEST$"):
        game.submit(request)
    mutable_id.clear()
    assert execution(kernel) == before
    traces_before = research.action_traces
    assert traces_before == ()
    result = game.submit(intent(observation))
    assert result.status is ActionStatus.SUCCEEDED
    assert research.action_traces[0].attempt_sequence == 1
    assert research.action_traces[0].result == kernel.action_results[0]


@pytest.mark.parametrize("phase", ["validate", "resolve"])
def test_nested_registry_failure_is_an_engine_error_not_unknown_action(phase: str) -> None:
    class BrokenWait(WaitHandler):
        def validate(self, request: ActionRequest, context: ValidationContext) -> None:
            super().validate(request, context)
            if phase == "validate":
                ActionRegistry().get("INTERNAL-MISSING", 1)

        def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
            ActionRegistry().get("INTERNAL-MISSING", 1)
            return TransitionPlan()

    kernel = make_kernel(action_handler=BrokenWait())
    kernel.boot()
    scheduled = kernel.schedule(future(1))
    app, research = create_application(kernel)
    game = app.game_for("a")
    request = intent(game.observe())
    with pytest.raises(GameSubmissionError, match=r"^ENGINE_ERROR$"):
        game.submit(request)
    trace = research.action_traces[0]
    assert trace.request == request
    assert trace.error_type == "HandlerNotFoundError"
    assert trace.result is None and trace.controller_result is None
    assert trace.boundary_reason is None
    assert kernel.action_results == ()
    if phase == "validate":
        assert kernel.simulation_time == 0
        assert kernel.pending_scheduled_events == (scheduled,)
        assert research.system_event_outcomes == ()
    else:
        assert kernel.simulation_time == 1
        assert kernel.pending_scheduled_events == ()
        assert len(research.system_event_outcomes) == 1
    kernel.close()


def test_registry_misses_retain_the_original_lookup_exception_contract() -> None:
    for registry in (ActionRegistry(), SystemEventRegistry()):
        with pytest.raises(HandlerNotFoundError) as caught:
            registry.get("MISSING", 1)
        assert type(caught.value) is HandlerNotFoundError


def test_late_contributor_failure_preserves_world_history_and_interleaved_sequences() -> None:
    calls: list[str] = []
    fail = True
    pipeline = ObservationPipeline()

    def first(context: PerceptionContext) -> JsonObject:
        calls.append("first")
        return context.perceived

    def second(context: PerceptionContext) -> JsonObject:
        calls.append("second")
        if fail:
            raise RuntimeError("PRIVATE-DIAGNOSTIC")
        return context.known

    for priority, contributor in enumerate((first, second)):
        pipeline.register(
            priority=priority,
            module_id="test",
            contributor_id=str(priority),
            contributor=contributor,
        )
    kernel = make_kernel()
    kernel.boot()
    kernel.schedule(future(0))
    app, research = create_application(kernel, pipeline=pipeline)
    a, b = app.game_for("a"), app.game_for("b")
    before = execution(kernel)
    with pytest.raises(GameSubmissionError, match=r"^OBSERVATION_UNAVAILABLE$"):
        a.observe()
    assert calls == ["first", "second"]
    assert execution(kernel) == before
    histories_before = (research.observations, research.action_traces)
    assert histories_before == ((), ())
    fail = False
    records = (a.observe(), b.observe(), a.observe())
    assert [(r.actor_id, r.observation_sequence) for r in records] == [("a", 1), ("b", 2), ("a", 3)]
    assert records[0].content == records[2].content
    assert execution(kernel) == before  # Even a due-now system input stays pending.
    denied = a.submit(replace(intent(records[0]), actor_id="b"))
    assert denied.reason_code == "WRONG_ACTOR"
    assert execution(kernel) == before
    assert a.observe().observation_sequence == 4
    result = a.submit(intent(records[0]))
    assert result.status is ActionStatus.SUCCEEDED
    assert [trace.attempt_sequence for trace in research.action_traces] == [1, 2]
    assert len(research.system_event_outcomes) == 1
    kernel.close()


@pytest.mark.parametrize(
    ("path", "bad"),
    [
        (("entities", "a"), []),
        (("entities", "a", "entity_id"), "b"),
        (("entities", "a", "entity_type"), {"secret": "HIDDEN"}),
        (("movement", "positions", "a"), []),
        (("movement", "positions", "a", "actor_id"), "b"),
        (("movement", "positions", "a", "location_id"), {"secret": "HIDDEN"}),
        (("movement", "locations", "x", "location_id"), "y"),
    ],
)
def test_malformed_perception_records_fail_without_publishing_partial_facts(
    path: tuple[str, ...], bad: JsonValue
) -> None:
    state = world()
    target = state
    for key in path[:-1]:
        target = obj(target[key])
    target[path[-1]] = bad
    before = canonical_json(state)
    with pytest.raises(ValueError):
        perceive(
            run_id="run-m3",
            actor_id="a",
            simulation_time=0,
            world=state,
            knowledge=KnowledgeLedger("run-m3", 0),
        )
    assert canonical_json(state) == before


def test_unknown_actor_and_absent_position_are_distinct_perception_cases() -> None:
    state = world()
    ledger = KnowledgeLedger("run-m3", 0)
    with pytest.raises(ValueError, match="unknown observation actor"):
        perceive(
            run_id="run-m3", actor_id="absent", simulation_time=0, world=state, knowledge=ledger
        )
    del obj(obj(state["movement"])["positions"])["a"]
    context = perceive(
        run_id="run-m3", actor_id="a", simulation_time=0, world=state, knowledge=ledger
    )
    assert context.perceived == {
        "self": {"entity_id": "a", "entity_type": "person"},
        "movement": {},
    }


def test_system_delivery_failure_does_not_attach_a_prior_same_id_action_result() -> None:
    bus = EventBus()

    def broken(event: EventEnvelope) -> None:
        raise RuntimeError("PRIVATE-DIAGNOSTIC")

    bus.subscribe(
        event_type=None, priority=0, module_id="test", subscriber_id="fail", subscriber=broken
    )
    kernel = make_kernel(bus=bus)
    kernel.boot()
    app, research = create_application(kernel)
    game = app.game_for("a")
    first = game.submit(intent(game.observe()))
    assert first.status is ActionStatus.SUCCEEDED
    kernel.schedule(future(2))
    with pytest.raises(GameSubmissionError, match=r"^ENGINE_ERROR$"):
        game.submit(intent(game.observe()))
    previous, interrupted = research.action_traces
    assert previous.request.action_request_id == interrupted.request.action_request_id
    assert previous.result == kernel.action_results[0]
    assert interrupted.attempt_sequence == 2
    assert interrupted.result is None
    assert interrupted.controller_result is None
    assert interrupted.error_type == "EventDeliveryError"
    assert kernel.simulation_time == 2
    assert len(research.system_event_outcomes) == len(research.events) == 1
    assert kernel.pending_scheduled_events == ()
    assert len(kernel.action_results) == 1
    # Recovery starts a new attempt and never redelivers the committed event.
    game.submit(intent(game.observe()))
    assert research.action_traces[-1].attempt_sequence == 3
    assert len(kernel.action_results) == 2
    assert len(research.system_event_outcomes) == len(research.events) == 1
    kernel.close()


def test_live_future_submission_cannot_advance_time_even_when_domain_would_reject() -> None:
    kernel, control = make_kernel(), make_kernel()
    for instance in (kernel, control):
        instance.boot()
        instance.schedule(future(2))
    app, research = create_application(kernel)
    game = app.game_for("a")
    observation = game.observe()
    request = replace(intent(observation, payload={"duration": 0}), submitted_at=2)
    before = execution(kernel)
    assert game.submit(request).reason_code == "INVALID_SUBMISSION_TIME"
    assert execution(kernel) == before
    # Trusted kernel retains the M2 semantics: progress BEFORE domain rejection,
    # with no application or Observation history required by the control run.
    assert control.submit_action(request).reason_code == "INVALID_DURATION"
    assert control.simulation_time == 2 and len(control.system_event_outcomes) == 1
    # Scenario advancement followed by a current-tick action may reuse old input.
    kernel.advance_to(2)
    result = game.submit(replace(intent(observation), submitted_at=2))
    assert result.status is ActionStatus.SUCCEEDED and result.resolved_at == 3
    assert len(research.observations) == 1
    kernel.close()
    control.close()
