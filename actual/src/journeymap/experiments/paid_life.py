"""Approved paid life session; offline engine behavior and owner-thread mutation."""

from collections.abc import Callable
from dataclasses import asdict
from hashlib import sha256
from time import monotonic

from journeymap.adapters.life_http import (
    LifeHTTPProvider,
    PaidFailure,
    paid_body,
    paid_request,
)
from journeymap.adapters.life_llm import LifeLLMConfig
from journeymap.adapters.provider import ProviderRequest
from journeymap.application.concurrent import ConcurrentApplication, ConcurrentReceipt
from journeymap.core.canonical import JsonObject, canonical_json
from journeymap.core.handlers import ActionRequest
from journeymap.core.observations import Observation
from journeymap.experiments.life import json_data
from journeymap.experiments.life_decisions import LifeDecisionService
from journeymap.experiments.mixed_life import MixedLifeSession
from journeymap.scenarios.life import LifeConfig


class PaidDecisionService(LifeDecisionService):
    def __init__(
        self, provider: LifeHTTPProvider, *, clock: Callable[[], float] = monotonic
    ) -> None:
        self.paid = provider
        p = provider.plan
        super().__init__(
            LifeLLMConfig(max_output_tokens=p.max_output_tokens, timeout_seconds=p.timeout_seconds),
            provider,
            horizon=p.horizon,
            clock=clock,
        )

    def _journal(self, stage: str, data: JsonObject) -> None:
        try:
            self.paid.owner(stage, data)
        except PaidFailure:
            self._fail("JOURNAL_UNAVAILABLE")

    def begin(self, observation: Observation) -> None:
        request = paid_request(observation, self.paid.plan)
        self.paid.owner(
            "DECISION_STARTED",
            json_data(
                {
                    "sequence": len(self.state.calls) + 1,
                    "observation_id": observation.observation_id,
                    "request_digest": sha256(
                        canonical_json(json_data(asdict(request))).encode()
                    ).hexdigest(),
                }
            ),
        )
        super().begin(observation)

    def poll(self) -> None:
        before = self.state.status
        pending = self.in_flight
        super().poll()
        if (
            before == "WAITING_FOR_DECISION"
            and self.state.status == "PAUSED_ERROR"
            and self.in_flight
        ):
            self._journal(
                "OWNER_TIMEOUT",
                {
                    "sequence": len(self.state.calls),
                    "world_paused": True,
                    "reservation_released": False,
                },
            )
        if pending and not self.in_flight:
            call = self.state.calls[-1]
            self._journal(
                "OWNER_PACKET",
                {
                    "sequence": call.sequence,
                    "late_discarded": call.discarded_late,
                    "status": call.status,
                    "error": call.error,
                },
            )

    def resolve(self) -> ActionRequest | None:
        action = super().resolve()
        call = self.state.calls[-1]
        self._journal(
            "RESPONSE_CLASSIFIED",
            {"sequence": call.sequence, "error": call.error, "candidate": call.candidate},
        )
        if self.state.status == "PAUSED_ERROR" and action is not None:
            # Failed classification journal must not submit an unrecorded action.
            self._update(action=None, candidate=None)
            return None
        return action

    def accepted(self, receipt: ConcurrentReceipt) -> None:
        super().accepted(receipt)
        call = self.state.calls[-1]
        self._journal(
            "ACTOR_RECEIPT",
            json_data(
                {
                    "sequence": call.sequence,
                    "action": asdict(call.action) if call.action else None,
                    "receipt": asdict(receipt),
                }
            ),
        )

    def refresh_results(self, app: ConcurrentApplication) -> None:
        old = {c.sequence: c.final_result for c in self.state.calls}
        super().refresh_results(app)
        for call in self.state.calls:
            if call.final_result is not None and old.get(call.sequence) != call.final_result:
                self._journal(
                    "ACTOR_COMPLETION",
                    json_data({"sequence": call.sequence, "result": asdict(call.final_result)}),
                )


class PaidLifeSession(MixedLifeSession):
    def __init__(
        self, provider: LifeHTTPProvider, *, clock: Callable[[], float] = monotonic
    ) -> None:
        self.paid = provider
        self._finished = False
        p = provider.plan
        super().__init__(
            LifeConfig(seed=p.seed, horizon=p.horizon),
            LifeLLMConfig(max_output_tokens=p.max_output_tokens, timeout_seconds=p.timeout_seconds),
        )
        self.decider.close()
        try:
            self.decider = PaidDecisionService(provider, clock=clock)
        except Exception:
            super().close()
            raise

    def _recorded_request(self, observation: Observation) -> ProviderRequest:
        return paid_request(observation, self.paid.plan)

    def _recorded_body(self, request: ProviderRequest) -> JsonObject:
        return paid_body(request)

    def replay_recorded(self) -> JsonObject:
        from journeymap.experiments.paid_replay import replay_paid_report

        return replay_paid_report(self.export_report()) | {"paid_journal_checked": True}

    def controller_report(self) -> JsonObject:
        report = super().controller_report()
        rows, snapshot = self.paid.journal.snapshot()
        report.update(
            {
                "mode": "LLM_ADAPTER_LIVE",
                "fixture": None,
                "binding": self.paid.journal.bound,
                "usage": self.paid.accounting(),
                "journal": json_data({"entries": rows})["entries"],
                "audit_snapshot": snapshot
                | {
                    "call_count": len(self.decider.state.calls),
                    "worker_in_flight": self.decider.in_flight,
                    "owner_finished": self._finished,
                },
            }
        )
        return report

    def export_report(self) -> JsonObject:
        return json_data(
            {
                "report_schema": "life-paid-report-2",
                "controller": self.controller_report(),
                "life": self.report(),
                "inputs": [asdict(i) for i in self.inputs],
                "execution": self.kernel.execution_snapshot,
                "events": [asdict(e) for e in self.kernel.events],
                "results": [asdict(r) for r in self.kernel.action_results],
            }
        )

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        self.paid.closed = True
        self.paid.journal.finish(self.decider.in_flight or bool(self.paid.budget.reserved_cost))

    def drain(self, seconds: float = 120) -> bool:
        deadline = monotonic() + seconds
        while self.decider.in_flight and monotonic() < deadline:
            self.decider.wait()
            self.decider.poll()
        return not self.decider.in_flight

    def close(self) -> None:
        try:
            self.finish()
        finally:
            super().close()
            # A still-running worker may append its late accounting. No new
            # request can start; the consumed registry row is never reset.
            if not self.decider.in_flight:
                self.paid.journal.close()
