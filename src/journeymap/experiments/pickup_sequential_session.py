"""Trusted sequential orchestration. Provider never receives these world capabilities."""

from typing import cast

from journeymap.adapters.event_memory import EventMemoryPolicy, RecencyEventMemory
from journeymap.adapters.llm import event_memory_input, observation_json
from journeymap.adapters.memory import NoMemory
from journeymap.adapters.pickup_cue_live import ADAPTER_VERSION
from journeymap.adapters.pickup_cue_prompt import diagnostic_input
from journeymap.adapters.pickup_sequential import (
    MOCK_VERSION,
    FullHistoryMemory,
    cue_flags,
    provider_request,
    wire_record,
)
from journeymap.adapters.provider import RawModelResponse
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.experiments.alderwick import json_value
from journeymap.experiments.memory_horizon_audit import obj, require
from journeymap.experiments.pickup_cue import (
    engine_report,
    parse_response,
    step_record,
    submit_step,
)
from journeymap.experiments.pickup_cue_session import PickupSession
from journeymap.experiments.pickup_sequential_metrics import arrival, metrics
from journeymap.experiments.pickup_sequential_plan import CONDITIONS


class SequentialSession:
    def __init__(self, allocation: JsonObject, seed: int, config: JsonObject) -> None:
        require(allocation["condition"] in CONDITIONS, "memory condition")
        self.condition = cast(str, allocation["condition"])
        legacy = {
            **allocation,
            "condition": "full-prefix" if self.condition == "full-history" else self.condition,
        }
        self.base = PickupSession(legacy, seed, config)
        self.config = config
        self.steps: list[JsonValue] = []
        self.pending = False
        self.stop = "PREPARING"

    def prepare(self) -> JsonObject:
        try:
            data = self.base.prepare()
        finally:
            self.base.case["condition"] = self.condition
        self.initial = {k: v for k, v in data.items() if k not in ("decision", "wire")}
        self.stop = "RUNNING"
        return self.snapshot()

    def prepare_step(self) -> JsonObject:
        require(
            self.stop == "RUNNING" and not self.pending and len(self.steps) < 6, "decision horizon"
        )
        self.observation = self.base.observation if not self.steps else self.base.game.observe()
        policy: EventMemoryPolicy = (
            FullHistoryMemory()
            if self.condition == "full-history"
            else NoMemory()
            if self.condition == "no-event-memory"
            else RecencyEventMemory(int(self.condition[-1]))
        )
        raw, provenance = event_memory_input(self.base.archive.begin(self.observation), policy)
        inputs = diagnostic_input(raw)
        flags = cue_flags(inputs)
        require(
            flags["cue_target"] in (None, self.base.case["target"]), "fixed target cue consistency"
        )
        self.request = provider_request(inputs, self.config)
        self.step = step_record("sequential-evaluation")
        self.step.update(
            {
                "step_number": len(self.steps) + 1,
                "observation": observation_json(self.observation),
                "input": {
                    **provenance,
                    "model_visible_input": inputs,
                    "provider_request": json_value(self.request),
                },
                "wire": wire_record(self.request, self.config),
                "cue": flags,
                "raw_response": None,
                "provider_failure": None,
                "provider_called": False,
                "candidate": None,
                "parser_outcome": "NOT_RUN",
                "category": "PENDING",
                "engine_status": "NOT_SUBMITTED",
                "arrived": False,
            }
        )
        self.pending = True
        return self.step

    def finish_step(
        self, response: RawModelResponse | None, failure: JsonObject | None, *, mock: bool
    ) -> JsonObject:
        require(self.pending, "one finish per opportunity")
        self.pending = False
        step = self.step
        step["provider_called"] = True
        step["provider_failure"] = failure
        category = "CONTINUE"
        if response is None:
            require(failure is not None, "missing failure")
            category, step["failure"] = "INFRASTRUCTURE", "TRANSPORT_ERROR"
        else:
            require(failure is None, "response/failure conflict")
            step["raw_response"] = json_value(response)
            meta = response.metadata
            if (
                meta.get("provider") != ("fake" if mock else "openai")
                or meta.get("adapter_version") != (MOCK_VERSION if mock else ADAPTER_VERSION)
                or meta.get("model") not in cast(list[JsonValue], self.config["returned_models"])
                or type(meta.get("response_id")) is not str
                or not meta.get("response_id")
                or meta.get("status") not in ("completed", "incomplete")
            ):
                category, step["failure"] = "INTEGRITY", "RESPONSE_IDENTITY_MISMATCH"
            else:
                parsed, action = parse_response(response, self.observation)
                step.update(parsed)
                if action is None:
                    category = "RESPONSE_FAILURE"
                else:
                    submit_step(
                        step,
                        self.observation,
                        action,
                        self.base.game,
                        self.base.archive,
                        self.base.research,
                    )
                    if step["failure"] in ("ENGINE_ERROR", "RECORDING_ERROR"):
                        category = "INTEGRITY"
        report = json_value(engine_report(self.base.research))
        report = obj(report)
        receipt = step["receipt"]
        if isinstance(receipt, dict) and step["engine_submitted"] is True:
            step["engine_status"] = receipt["status"]
        step["arrived"] = category == "CONTINUE" and arrival(
            step, report, cast(str, self.base.case["target"])
        )
        if step["arrived"]:
            category = "SUCCESS"
        step["category"] = category
        self.steps.append(step)
        if category != "CONTINUE":
            self.stop = category
        elif len(self.steps) == 6:
            self.stop = "DECISION_LIMIT"
        return self.snapshot()

    def snapshot(self) -> JsonObject:
        data = self.base.snapshot()
        initial = getattr(
            self, "initial", {k: v for k, v in data.items() if k not in ("decision", "wire")}
        )
        result: JsonObject = {
            **initial,
            "case": self.base.case,
            "steps": self.steps,
            "pending_step": self.step if self.pending else None,
            **{
                k: data[k]
                for k in (
                    "observations",
                    "action_traces",
                    "action_requests",
                    "event_traces",
                    "knowledge",
                    "engine_report",
                )
            },
        }
        result["metrics"] = metrics(result, self.stop)
        return result

    def close(self) -> None:
        self.base.close()
