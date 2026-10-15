"""Opt-in one-worker offline Provider orchestration on the real life engine."""

from collections.abc import Callable
from dataclasses import asdict, replace
from time import monotonic

from journeymap.adapters.life_http import LifeHTTPProvider
from journeymap.adapters.life_llm import LifeLLMConfig, life_body, life_candidate, life_request
from journeymap.adapters.provider import Provider, ProviderRequest
from journeymap.application.concurrent import ConcurrentApplication
from journeymap.application.concurrent_replay import LiveInput, replay_live_inputs
from journeymap.bootstrap_life import create_life_application, create_life_kernel
from journeymap.core.canonical import JsonObject
from journeymap.core.concurrent import ConcurrentKernel
from journeymap.core.observations import Observation
from journeymap.experiments.life import LifeSession, json_data
from journeymap.experiments.life_decisions import LifeDecisionService
from journeymap.scenarios.life import LifeConfig


class MixedLifeSession(LifeSession):
    def __init__(
        self,
        config: LifeConfig,
        llm: LifeLLMConfig | None = None,
        *,
        provider: Provider | None = None,
        _restoring: bool = False,
        decision_clock: Callable[[], float] = monotonic,
    ) -> None:
        if isinstance(provider, LifeHTTPProvider):
            raise PermissionError("approved live composition requires PaidLifeSession")
        super().__init__(config, _restoring=_restoring)
        self.llm_config = llm or LifeLLMConfig()
        self.decider = LifeDecisionService(
            self.llm_config, provider, horizon=config.horizon, clock=decision_clock
        )
        # Remaining activation order survives a Provider wait and checkpoint.
        self._activation: list[str] | None = None

    def progress(self, horizon: int, now: float) -> bool:
        if (
            type(horizon) is not int
            or not self.kernel.simulation_time <= horizon <= self.config.horizon
        ):
            raise ValueError("invalid mixed life horizon")
        self.decider.poll()
        while self.kernel.simulation_time < horizon:
            if self.decider.state.status in {"PAUSED_ERROR", "LIMIT_REACHED"}:
                return False
            tick = self.kernel.simulation_time
            self._sample_tick(tick)
            if self._activation is None:
                actors = sorted(self.controllers)
                pivot = self._cursor % len(actors)
                self._cursor += 1
                busy = {f.request.actor_id for f in self.kernel.in_flight}
                self._activation = [a for a in actors[pivot:] + actors[:pivot] if a not in busy]
            while self._activation:
                actor = self._activation[0]
                game = self.application.game_for(actor)
                if actor == self.llm_config.actor and self.decider.state.status != "IDLE":
                    if self.decider.state.status != "READY":
                        return False
                    request = self.decider.resolve()
                    self._activation.pop(0)
                    if request is None:
                        return False
                else:
                    if actor == self.llm_config.actor and not self.decider.can_begin():
                        return False
                    observation = game.observe()
                    self.observations.append(observation)
                    self.inputs.append(
                        LiveInput(
                            "observe",
                            tick,
                            actor,
                            observation_id=observation.observation_id,
                            observation_digest=observation.content_digest,
                        )
                    )
                    if actor == self.llm_config.actor:
                        self.decider.begin(observation)
                        return False
                    request = self.controllers[actor].decide(observation)
                    self._activation.pop(0)
                receipt = game.submit(request)
                self.inputs.append(
                    LiveInput("submit", tick, actor, request=request, receipt=receipt)
                )
                if actor == self.llm_config.actor:
                    self.decider.accepted(receipt)
                self.advance(tick)
                self.decider.refresh_results(self.application)
                if self.decider.state.status in {"PAUSED_ERROR", "LIMIT_REACHED"}:
                    return False
            self._activation = None
            self._sample_tick(tick + 1)
            self.decider.refresh_results(self.application)
        return True

    def run_until(self, horizon: int) -> None:
        while not self.progress(horizon, monotonic()):
            if self.decider.state.status in {"PAUSED_ERROR", "LIMIT_REACHED"}:
                raise ValueError(self.decider.state.status)
            self.decider.wait()

    def replay_recorded(self) -> JsonObject:
        """Validate responses/bindings and replay inputs without Provider calls."""
        observed = {o.observation_id: o for o in self.observations}
        for call in self.decider.state.calls:
            observation = observed[call.observation_id]
            if call.request != self._recorded_request(
                observation
            ) or call.body != self._recorded_body(call.request):
                raise ValueError("recorded provider input mismatch")
            if call.action is not None:
                if call.raw is None:
                    raise ValueError("missing recorded response")
                candidate, action = life_candidate(str(call.raw.data["text"]), observation)
                if candidate != call.candidate or action != call.action:
                    raise ValueError("recorded candidate/binding mismatch")
                submitted = next(
                    (i for i in self.inputs if i.kind == "submit" and i.request == call.action),
                    None,
                )
                if submitted is None or submitted.receipt != call.receipt:
                    raise ValueError("recorded engine receipt mismatch")
                if (
                    call.final_result is not None
                    and self.application.game_for(call.actor_id).result(
                        call.action.action_request_id
                    )
                    != call.final_result
                ):
                    raise ValueError("recorded final result mismatch")
            if call.error == "TIMEOUT" and call.discarded_late:
                if (
                    call.status != "ERROR"
                    or call.action is not None
                    or call.candidate is not None
                    or call.receipt is not None
                    or call.final_result is not None
                ):
                    raise ValueError("recorded late response was executed")
            elif call.raw is not None and call.status != "READY":
                probe = LifeDecisionService(self.llm_config, horizon=self.config.horizon)
                probe.state.calls = [replace(call, action=None, candidate=None, error=None)]
                probe.state.ready = call.raw
                probe.state.observation = observation
                probe.state.status = "READY"
                parsed = probe.resolve()
                if parsed != call.action or probe.state.calls[-1].error != call.error:
                    raise ValueError("recorded response classification mismatch")

        def factory() -> tuple[ConcurrentKernel, ConcurrentApplication]:
            kernel = create_life_kernel(self.config)
            return kernel, create_life_application(kernel)

        replay = replay_live_inputs(factory, tuple(self.inputs))
        equal = (
            replay.final_execution == self.kernel.execution_snapshot
            and replay.attempts == self.application.attempts
            and replay.observations == tuple(self.observations)
        )
        if not equal:
            raise ValueError("mixed live replay mismatch")
        return {
            "provider_recalled": False,
            "live_inputs_equal": True,
            "recorded_responses_checked": len(self.decider.state.calls),
        }

    def _recorded_request(self, observation: Observation) -> ProviderRequest:
        return life_request(observation, self.llm_config.max_output_tokens, self.config.horizon)

    def _recorded_body(self, request: ProviderRequest) -> JsonObject:
        return life_body(request)

    def controller_report(self) -> JsonObject:
        return json_data(
            {
                "mode": "LLM_ADAPTER_OFFLINE",
                "fixture": self.llm_config.fixture_version,
                "actor": self.llm_config.actor,
                "requests_started": self.decider.state.requests_started,
                "request_limit": self.llm_config.request_limit,
                "status": self.decider.state.status,
                "calls": [asdict(c) for c in self.decider.state.calls],
                "usage": "mock; real tokens and prices not measured",
            }
        )

    def close(self) -> None:
        self.decider.close()
        super().close()
