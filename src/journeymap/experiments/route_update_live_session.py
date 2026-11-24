"""Separate live response validation; shared trusted application submission and metrics."""

from typing import cast

from journeymap.adapters.llm import Record, strict_json
from journeymap.adapters.provider import RawModelResponse
from journeymap.adapters.route_update import intent, obj
from journeymap.adapters.route_update_live import (
    ADAPTER_VERSION,
    CONFIG_VERSION,
    MOCK_VERSION,
    wire,
)
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.experiments.alderwick import json_value
from journeymap.experiments.route_update_metrics import first_judgment
from journeymap.experiments.route_update_session import Session


class LiveSession(Session):
    def __init__(self, run_id: str, config: JsonObject, mode: str) -> None:
        if mode not in ("live", "recording-mock"):
            raise ValueError("E04 session mode")
        self.config, self.mode = Record.capture(config).data, mode
        super().__init__(run_id)

    def prepare(self, *, initial: bool = False) -> JsonObject:
        # Common observation/archive lifecycle opens exactly once; fake wire is replaced
        # before any durable record or transport. Never call the fake-only finish path.
        super().prepare(initial=initial)
        if self.pending is None:
            raise ValueError("E04 preparation missing")
        self.pending["wire"] = wire(obj(self.pending["semantic_input"]), self.config)
        return Record.capture(self.pending).data

    def finish_live(
        self,
        response: RawModelResponse | None,
        failure: JsonObject | None,
        *,
        initial: bool = False,
        global_stop: str | None = None,
    ) -> JsonObject:
        if self.pending is None:
            raise ValueError("E04 live decision not prepared")
        step = self.pending
        step["provider_called"] = True
        step["provider_failure"] = failure
        if initial:
            self.prefix_calls += 1
        if response is None:
            if failure is None:
                raise ValueError("E04 missing response/failure")
            step["response_status"], self.stop = "INFRASTRUCTURE", "INFRASTRUCTURE"
        else:
            if failure is not None:
                raise ValueError("E04 response/failure conflict")
            step["raw_response"] = json_value(response)
            meta = response.metadata
            if (
                meta.get("provider") != ("openai" if self.mode == "live" else "e04-inert-http-mock")
                or meta.get("adapter_version")
                != (ADAPTER_VERSION if self.mode == "live" else MOCK_VERSION)
                or meta.get("configuration_version") != CONFIG_VERSION
                or meta.get("model") not in cast(list[JsonValue], self.config["returned_models"])
                or type(meta.get("response_id")) is not str
                or not meta.get("response_id")
            ):
                step["response_status"], self.stop = "INTEGRITY", "INTEGRITY"
            elif meta.get("status") == "incomplete":
                step["response_status"], self.stop = "INCOMPLETE", "RESPONSE_FAILURE"
                details = meta.get("incomplete_details")
                if isinstance(details, dict) and details.get("reason") == "max_output_tokens":
                    self.stop = "OUTPUT_BUDGET_DIAGNOSTIC_STOP"
            elif meta.get("status") != "completed":
                step["response_status"], self.stop = "INTEGRITY", "INTEGRITY"
            elif meta.get("refusal") is True:
                step["response_status"], self.stop = "REFUSAL", "RESPONSE_FAILURE"
            else:
                try:
                    candidate = intent(strict_json(response.text))
                except (TypeError, ValueError, RuntimeError):
                    step["response_status"], self.stop = "MALFORMED", "RESPONSE_FAILURE"
                else:
                    if global_stop is None:
                        step["candidate"], step["response_status"] = candidate, "VALID"
                        self._submit(candidate)
                    else:
                        step["response_status"] = "COST_UNVERIFIED"
        if global_stop is not None:
            self.stop = global_stop
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
