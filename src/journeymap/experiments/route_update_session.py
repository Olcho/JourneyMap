"""Trusted E04 application lifecycle; replay rebinds intents, never foreign requests."""

from typing import cast

from journeymap.adapters.event_memory import EventTraceArchive
from journeymap.adapters.llm import Record, observation_json, parse_candidate, strict_json
from journeymap.adapters.provider import ProviderRequest, RawModelResponse
from journeymap.adapters.route_update import (
    CONFIGURATION_VERSION,
    MODEL,
    PROFILE_VERSION,
    intent,
    obj,
    profile_for,
    request,
    semantic_input,
    wire,
)
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.experiments.alderwick import json_value
from journeymap.experiments.pickup_cue import engine_report, step_record, submit_step
from journeymap.experiments.route_update_metrics import (
    arrived,
    first_judgment,
    metrics,
    truth_baseline,
)
from journeymap.experiments.route_update_runtime import create_runtime
from journeymap.scenarios.route_update import ACTOR, interventions


class Session:
    def __init__(
        self,
        run_id: str,
        seed: int = 42,
        max_decisions: int = 6,
        max_ticks: int = 24,
        *,
        profile_version: str = PROFILE_VERSION,
    ) -> None:
        if (
            type(max_decisions) is not int
            or not 1 <= max_decisions <= 6
            or type(max_ticks) is not int
            or not 1 <= max_ticks <= 24
        ):
            raise ValueError("E04 bounded offline limits required")
        self._profile_version = profile_for(profile_version).version
        self.kernel, self.app, self.research = create_runtime(run_id, seed)
        self.game, self.archive = self.app.game_for(ACTOR), EventTraceArchive()
        self.max_decisions, self.max_ticks = max_decisions, max_ticks
        self.prefix: JsonObject | None = None
        self.steps: list[JsonObject] = []
        self.pending: JsonObject | None = None
        self.first: JsonObject | None = None
        self.baseline: JsonObject | None = None
        self.start: str | None = None
        self.condition: str | None = None
        self.checkpoint: JsonObject | None = None
        self.stop = "INITIAL"
        self.prefix_calls = self.prefix_replays = 0
        self.parent: JsonObject | None = None
        self.initial_state = self.kernel.state_snapshot

    @property
    def provider_request(self) -> ProviderRequest:
        return self._request

    def prepare(self, *, initial: bool = False) -> JsonObject:
        if (
            self.pending is not None
            or (initial and self.stop != "INITIAL")
            or (not initial and self.stop != "RUNNING")
        ):
            raise ValueError("invalid decision lifecycle")
        self.observation = self.game.observe()
        context = self.archive.begin(self.observation)
        raw: JsonObject = {
            "observation": observation_json(self.observation),
            "event_memory": [t.to_json() for t in context.prior],
        }
        semantic = semantic_input(raw)
        self._request = request(semantic, profile_version=self._profile_version)
        step = step_record("initial" if initial else "follow-up")
        step.update(
            {
                "observation": observation_json(self.observation),
                "raw_input": raw,
                "semantic_input": semantic,
                "wire": wire(self._request),
                "provider_called": False,
                "raw_response": None,
                "provider_failure": None,
                "response_status": "ABSENT",
                "engine_status": "NOT_SUBMITTED",
                "arrived": False,
            }
        )
        self.pending = step
        return Record.capture(step).data

    def _submit(self, candidate: JsonObject) -> None:
        if self.pending is None:
            raise ValueError("no active observation")
        action = parse_candidate(candidate, self.observation)
        self.pending["request"] = json_value(action)
        if self.condition is not None:
            if candidate["action_type"] == "WAIT":
                duration = cast(int, obj(candidate["payload"])["duration"])
            else:
                route_id = obj(candidate["payload"])["route_id"]
                routes = obj(obj(self.initial_state["movement"])["routes"])
                route = routes.get(cast(str, route_id))
                duration = cast(int, obj(route)["traversal_cost"]) if isinstance(route, dict) else 0
            if self.kernel.simulation_time + duration > 3 + self.max_ticks:
                self.stop = "TIME_BUDGET_NOT_SUBMITTED"
                return
        submit_step(self.pending, self.observation, action, self.game, self.archive, self.research)
        receipt = self.pending["receipt"]
        if isinstance(receipt, dict):
            self.pending["engine_status"] = receipt["status"]
        if self.pending["failure"] is not None:
            self.stop = "INTEGRITY"
        report = json_value(engine_report(self.research))
        self.pending["arrived"] = arrived(self.pending, obj(report))

    def finish(
        self,
        response: RawModelResponse | None,
        failure: JsonObject | None,
        *,
        initial: bool = False,
    ) -> JsonObject:
        if self.pending is None:
            raise ValueError("no prepared decision")
        step = self.pending
        step["provider_called"] = True
        step["provider_failure"] = failure
        if initial:
            self.prefix_calls += 1
        if response is None:
            if failure is None:
                raise ValueError("missing response/failure")
            step["response_status"] = "INFRASTRUCTURE"
            self.stop = "INFRASTRUCTURE"
        else:
            if failure is not None:
                raise ValueError("response/failure conflict")
            step["raw_response"] = json_value(response)
            meta = response.metadata
            if (
                meta.get("provider") != "fake"
                or meta.get("adapter_version") != CONFIGURATION_VERSION
                or meta.get("model") != MODEL
                or type(meta.get("response_id")) is not str
                or not meta.get("response_id")
                or meta.get("usage") is not None
            ):
                step["response_status"] = "INTEGRITY"
                self.stop = "INTEGRITY"
            elif meta.get("refusal") is True:
                step["response_status"] = "REFUSAL"
                self.stop = "RESPONSE_FAILURE"
            elif meta.get("status") != "completed":
                step["response_status"] = "INCOMPLETE"
                self.stop = "RESPONSE_FAILURE"
            else:
                try:
                    candidate = intent(strict_json(response.text))
                except (TypeError, ValueError, RuntimeError):
                    step["response_status"] = "MALFORMED"
                    self.stop = "RESPONSE_FAILURE"
                else:
                    step["candidate"] = candidate
                    step["response_status"] = "VALID"
                    self._submit(candidate)
        if initial:
            self.prefix, self.pending = step, None
            if (
                self.stop != "INTEGRITY"
                and len(self.archive.traces) == 1
                and step["response_status"] == "VALID"
                and step["engine_status"] == "SUCCEEDED"
                and self.kernel.simulation_time == 2
                and self.position in ("A", "B")
                and obj(step["candidate"])["action_type"] == "MOVE"
                and obj(obj(step["candidate"])["payload"])["route_id"] in ("S-to-A", "S-to-B")
            ):
                self.stop = "PREFIX_READY"
                self._checkpoint()
            elif self.stop == "INITIAL":
                self.stop = "PREFIX_UNSUCCESSFUL"
        else:
            self.steps.append(step)
            self.pending = None
            if len(self.steps) == 1:
                if self.baseline is None or self.start is None:
                    raise ValueError("missing research baseline")
                self.first = first_judgment(step, self.baseline, self.start)
            if self.stop == "RUNNING":
                if step["arrived"] is True:
                    self.stop = "ARRIVED"
                elif self.kernel.simulation_time >= 3 + self.max_ticks:
                    self.stop = "TIME_LIMIT"
                elif len(self.steps) == self.max_decisions:
                    self.stop = "DECISION_LIMIT"
        return self.snapshot()

    @property
    def position(self) -> str:
        return cast(
            str,
            obj(obj(obj(self.kernel.state_snapshot["movement"])["positions"])[ACTOR])[
                "location_id"
            ],
        )

    def _checkpoint(self) -> None:
        self.checkpoint = {
            "tick": self.kernel.simulation_time,
            "position": self.position,
            "state": self.kernel.state_snapshot,
            "state_digest": self.kernel.state_digest,
            "closed_experiences": len(self.archive.traces),
        }

    def reconstruct_prefix(self, candidate: JsonObject, parent: JsonObject) -> JsonObject:
        self.prepare(initial=True)
        if self.pending is None:
            raise ValueError("missing replay observation")
        self.pending.update({"candidate": intent(candidate), "response_status": "REPLAYED"})
        self._submit(candidate)
        if (
            self.pending["engine_status"] != "SUCCEEDED"
            or self.position not in ("A", "B")
            or self.kernel.simulation_time != 2
            or len(self.archive.traces) != 1
        ):
            raise ValueError("prefix replay did not rebuild application experience")
        self.prefix, self.pending = self.pending, None
        self.prefix_replays, self.parent, self.stop = 1, Record.capture(parent).data, "PREFIX_READY"
        self._checkpoint()
        return self.snapshot()

    def install_branch(self, condition: str) -> JsonObject:
        if self.stop != "PREFIX_READY" or self.kernel.simulation_time != 2:
            raise ValueError("intervention must follow common checkpoint")
        self.condition = condition
        for event in interventions(condition):
            self.kernel.schedule(event)
        self.kernel.advance_to(3)
        self.start = self.position
        self.baseline = truth_baseline(self.kernel.state_snapshot, self.position)
        self.stop = "RUNNING"
        return self.snapshot()

    def snapshot(self) -> JsonObject:
        assessment = (
            metrics(self.steps, self.kernel.simulation_time, self.stop, self.first, self.baseline)
            if self.baseline is not None
            else None
        )
        return Record.capture(
            {
                "run_id": self.kernel.manifest.run_id,
                "manifest": json_value(self.kernel.manifest),
                "initial_state": self.initial_state,
                "prefix": self.prefix,
                "parent": self.parent,
                "prefix_calls": self.prefix_calls,
                "prefix_replays": self.prefix_replays,
                "checkpoint": self.checkpoint,
                "condition": self.condition,
                "interventions": json_value(interventions(self.condition))
                if self.condition is not None
                else [],
                "steps": cast(list[JsonValue], self.steps),
                "pending": self.pending,
                "first_judgment": self.first,
                "baseline": self.baseline,
                "metrics": assessment,
                "stop": self.stop,
                "observations": [observation_json(o) for o in self.research.observations],
                "event_traces": [t.to_json() for t in self.archive.traces],
                "action_traces": json_value(self.research.action_traces),
                "knowledge": json_value(self.research.knowledge_history(ACTOR)),
                "engine_report": json_value(engine_report(self.research)),
            }
        ).data

    def close(self) -> None:
        self.kernel.close()
