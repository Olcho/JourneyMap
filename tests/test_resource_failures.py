"""Adversarial M6 candidates exercise the unchanged common commit boundary."""

from dataclasses import replace

import pytest
from test_alderwick import execution, obj
from test_resources import kernel_for, request, tick

from journeymap.bootstrap import create_alderwick_application, create_alderwick_kernel
from journeymap.core.canonical import JsonObject
from journeymap.core.controller import GameSubmissionError
from journeymap.core.events import EventBus, EventEnvelope
from journeymap.core.handlers import (
    ActionRequest,
    ActionStatus,
    ActionTiming,
    ActionValidationError,
    ResolutionContext,
    TimedActionHandler,
    TransitionPlan,
    ValidationContext,
)
from journeymap.core.scheduler import ScheduledEventSpec
from journeymap.modules.inventory.transitions import transfer_candidate
from journeymap.modules.survival.handlers import ConsumeHandler
from journeymap.modules.survival.models import SurvivalState, survival_candidate
from journeymap.modules.trade.handlers import BuyHandler
from journeymap.modules.trade.models import payment_candidate


class FaultHandler:
    """Faults run after completion validation, including after real candidates."""

    def __init__(self, action: str, fault: str, patch: pytest.MonkeyPatch) -> None:
        self.inner: TimedActionHandler = BuyHandler() if action == "BUY" else ConsumeHandler()
        self.handler_id = self.inner.handler_id
        self.action, self.fault, self.patch = action, fault, patch
        self.enabled = True

    def validate(self, request: ActionRequest, context: ValidationContext) -> None:
        self.inner.validate(request, context)

    def prepare(self, request: ActionRequest, context: ValidationContext) -> ActionTiming:
        return self.inner.prepare(request, context)

    def validate_completion(
        self,
        request: ActionRequest,
        timing: ActionTiming,
        context: ValidationContext,
    ) -> None:
        self.inner.validate_completion(request, timing, context)

    def resolve(self, request: ActionRequest, context: ResolutionContext) -> TransitionPlan:
        from journeymap.modules.survival import handlers as survival
        from journeymap.modules.trade import handlers as trade

        if not self.enabled:
            return self.inner.resolve(request, context)
        context.rng.next_u64()
        with self.patch.context() as patch:
            if self.fault in {"after_inventory", "seller_wallet"}:
                payment = payment_candidate

                def fail_payment(
                    state: JsonObject, buyer: str, seller: str, total: int
                ) -> JsonObject:
                    if self.fault == "after_inventory":
                        raise ActionValidationError("INJECTED_TRADE_FAILURE")
                    obj(obj(state["trade"])["wallets"])[seller] = -1
                    return payment(state, buyer, seller, total)

                patch.setattr(trade, "payment_candidate", fail_payment)
            elif self.fault == "transfer":
                transfer = transfer_candidate

                def fail_transfer(
                    state: JsonObject, source: str, target: str, item: str, quantity: int
                ) -> JsonObject:
                    transfer(state, source, target, item, quantity)
                    raise ValueError("after transfer candidate")

                patch.setattr(trade, "transfer_candidate", fail_transfer)
            elif self.fault == "stock_invalid":
                obj(obj(obj(context.state["inventory"])["owners"])["edwin"])["bread"] = -1
            elif self.fault == "after_decrement":

                def fail_survival(state: JsonObject, actor: str) -> None:
                    raise ActionValidationError("INJECTED_SURVIVAL_FAILURE")

                patch.setattr(survival, "actor_survival", fail_survival)
            elif self.fault == "survival_candidate":
                candidate = survival_candidate

                def fail_candidate(
                    state: JsonObject, actor: str, value: SurvivalState
                ) -> JsonObject:
                    candidate(state, actor, value)
                    raise ValueError("after survival candidate")

                patch.setattr(survival, "survival_candidate", fail_candidate)
            plan = self.inner.resolve(request, context)
            if self.fault == "resolve":
                raise ValueError("after all candidates")
            if self.fault == "state_serialization":
                plan.set_values["bad"] = float("nan")
            if self.fault == "event_serialization":
                plan.events[-1].payload["bad"] = float("nan")
            return plan


@pytest.mark.parametrize(
    "action,fault",
    [
        ("BUY", "after_inventory"),
        ("BUY", "seller_wallet"),
        ("BUY", "stock_invalid"),
        ("BUY", "transfer"),
        ("CONSUME", "after_decrement"),
        ("CONSUME", "survival_candidate"),
        *(
            (action, fault)
            for action in ("BUY", "CONSUME")
            for fault in ("resolve", "state_serialization", "event_serialization")
        ),
    ],
)
def test_candidate_defect_preserves_exact_independent_progress_and_next_ids(
    action: str,
    fault: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    faulty = FaultHandler(action, fault, monkeypatch)
    kernel = kernel_for(
        buy=faulty if action == "BUY" else None, consume=faulty if action == "CONSUME" else None
    )
    control = kernel_for()
    try:
        for target in (kernel, control):
            tick(target)
            tick(target, 2)
            target.schedule(ScheduledEventSpec(1, 10, "TestReplace", 1, {"unrelated": "committed"}))
        control.advance_to(1)
        with pytest.raises(ValueError):
            kernel.submit_action(request(action))
        assert execution(kernel) == execution(control)
        assert len(kernel.action_results) == 0  # resolve defects are not validation FAILED results
        faulty.enabled = False
        retry = replace(request(action), submitted_at=1)
        succeeded = kernel.submit_action(retry)
        assert succeeded == control.submit_action(retry)
        assert execution(kernel) == execution(control)
        assert succeeded.status == ActionStatus.SUCCEEDED
    finally:
        kernel.close()
        control.close()


@pytest.mark.parametrize("fault", ["state", "payload", "event", "resolve"])
def test_system_tick_failure_leaves_schedule_time_rng_and_sequences_untouched(
    fault: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from journeymap.modules.survival.handlers import SurvivalTickHandler

    original = SurvivalTickHandler.resolve

    def broken(
        self: SurvivalTickHandler, event: object, context: ResolutionContext
    ) -> TransitionPlan:
        plan = original(self, event, context)  # type: ignore[arg-type]
        context.rng.next_u64()
        if fault == "resolve":
            raise ValueError("after tick candidate")
        plan.events[-1].payload["bad"] = float("nan")
        return plan

    kernel = kernel_for()
    try:
        if fault == "state":
            # Invalid later actor after earlier candidate rows would be processed.
            state = kernel.state_snapshot
            obj(obj(state["survival"])["actors"])["thomas"] = {"hunger": -1, "fatigue": 1}
            kernel.close()
            kernel = kernel_for(state)
        if fault in {"event", "resolve"}:
            monkeypatch.setattr(SurvivalTickHandler, "resolve", broken)
        kernel.schedule(
            ScheduledEventSpec(1, 0, "SurvivalTick", 1, {"extra": 1} if fault == "payload" else {})
        )
        before = execution(kernel)
        for _ in range(2):
            with pytest.raises(ValueError):
                kernel.advance_to(1)
            assert execution(kernel) == before
            assert kernel.action_results == ()
    finally:
        kernel.close()


@pytest.mark.parametrize(
    "action,event_type",
    [
        ("BUY", "ItemPurchased"),
        ("CONSUME", "ItemConsumed"),
        ("REST", "ActorRested"),
    ],
)
def test_live_post_commit_delivery_failure_keeps_both_modules_and_success_trace(
    action: str,
    event_type: str,
) -> None:
    from test_resource_integration import live_intent

    bus = EventBus()
    calls = []

    def broken(event: EventEnvelope) -> None:
        calls.append(event.event_id)
        raise ValueError("PRIVATE DIAGNOSTIC")

    bus.subscribe(
        event_type=event_type,
        priority=0,
        module_id="test",
        subscriber_id="fault",
        subscriber=broken,
    )
    kernel = create_alderwick_kernel(resources=True, event_bus=bus)
    control = create_alderwick_kernel(resources=True)
    kernel.boot()
    control.boot()
    try:
        app, research = create_alderwick_application(kernel, resources=True)
        control_app, _ = create_alderwick_application(control, resources=True)
        for target_app in (app, control_app):
            game = target_app.game_for("stranger")
            for route in ("west-gate-to-village-square", "village-square-to-bakery"):
                game.submit(live_intent(game, "MOVE", {"route_id": route}))
            if action == "CONSUME":
                game.submit(live_intent(game, "BUY", {"offer_id": "edwin-bread", "quantity": 1}))
        game, control_game = app.game_for("stranger"), control_app.game_for("stranger")
        from test_resources import payload

        expected = control_game.submit(live_intent(control_game, action, payload(action)))
        with pytest.raises(GameSubmissionError, match=r"^ENGINE_ERROR$"):
            game.submit(live_intent(game, action, payload(action)))
        assert expected.status == ActionStatus.SUCCEEDED
        assert execution(kernel) == execution(control)
        assert research.action_traces[-1].result == kernel.action_results[-1]
        before = kernel.state_snapshot
        game.observe()
        kernel.advance_to(kernel.simulation_time + 1)
        assert kernel.state_snapshot == before and len(calls) == 1
    finally:
        kernel.close()
        control.close()
