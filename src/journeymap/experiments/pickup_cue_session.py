"""Trusted one-case runtime, also used without a Provider for independent reconstruction."""

from typing import cast

from journeymap.adapters.event_memory import EventTraceArchive
from journeymap.adapters.llm import observation_json, parse_candidate
from journeymap.adapters.pickup_cue_live import (
    ADAPTER_VERSION,
    MOCK_VERSION,
    provider_request,
    wire_record,
)
from journeymap.adapters.provider import RawModelResponse
from journeymap.bootstrap import create_pickup_cue_application, create_pickup_cue_kernel
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.experiments.alderwick import json_value
from journeymap.experiments.memory_horizon_audit import obj, require
from journeymap.experiments.pickup_cue import (
    engine_report,
    evaluate,
    evidence,
    parse_response,
    step_record,
    submit_step,
)
from journeymap.scenarios.alderwick.pickup_cue import pickup_schedule, prefix_intents


class PickupSession:
    def __init__(self, allocation: JsonObject, seed: int, config: JsonObject) -> None:
        self.config = config
        self.case: JsonObject = {
            "case_id": allocation["allocation_id"],
            "target": allocation["target"],
            "d": allocation["d"],
            "condition": allocation["condition"],
            "seed": seed,
        }
        self.kernel = create_pickup_cue_kernel(
            run_id=f"{self.case['case_id']}:run",
            target=cast(str, self.case["target"]),
            seed=seed,
        )
        self.kernel.boot()
        self.app, self.research = create_pickup_cue_application(self.kernel)
        self.game, self.archive = self.app.game_for("stranger"), EventTraceArchive()
        self.prefix: list[JsonValue] = []
        self.finished = False
        self.data: JsonObject = {
            "case": self.case,
            "engine_manifest": json_value(self.kernel.manifest),
            "initial_state": self.kernel.state_snapshot,
            "initial_knowledge": [],
            "schedule": json_value(pickup_schedule()),
            "prefix": self.prefix,
            "prefix_status": "PREPARING",
        }

    def prepare(self) -> JsonObject:
        for intent in prefix_intents(cast(int, self.case["d"])):
            step = step_record("preparation")
            self.prefix.append(step)
            observation = self.game.observe()
            step["observation"] = observation_json(observation)
            self.archive.begin(observation)
            submit_step(
                step,
                observation,
                parse_candidate(intent, observation),
                self.game,
                self.archive,
                self.research,
            )
            require(
                step["failure"] is None and obj(step["receipt"])["status"] == "SUCCEEDED",
                "preparation failure",
            )
        require(
            self.kernel.simulation_time == 7 and len(self.archive.traces) == 5, "preparation bounds"
        )
        self.data["prefix_status"] = "READY"
        self.data["checkpoint"] = {
            "state": self.kernel.state_snapshot,
            "digest": self.kernel.state_digest,
            "tick": 7,
        }
        self.observation = self.game.observe()
        inputs = evidence(self.observation, self.archive, cast(str, self.case["condition"]))
        self.request = provider_request(obj(inputs["model_visible_input"]), self.config)
        inputs["provider_request"] = json_value(self.request)
        self.decision = step_record("evaluation")
        self.decision.update(
            {
                "observation": observation_json(self.observation),
                "input": inputs,
                "raw_response": None,
                "provider_called": False,
                "candidate": None,
                "parser_outcome": "NOT_RUN",
                "provider_failure": None,
            }
        )
        self.data["decision"] = self.decision
        self.data["wire"] = wire_record(self.request, self.config)
        return self.snapshot()

    def snapshot(self) -> JsonObject:
        self.data.update(
            {
                "observations": [observation_json(o) for o in self.research.observations],
                "action_traces": json_value(self.research.action_traces),
                "action_requests": json_value(
                    tuple(t.request for t in self.research.action_traces if t.engine_submitted)
                ),
                "event_traces": [t.to_json() for t in self.archive.traces],
                "knowledge": json_value(self.research.knowledge_history("stranger")),
                "engine_report": json_value(engine_report(self.research)),
            }
        )
        return self.data

    def finish(
        self,
        response: RawModelResponse | None,
        failure: JsonObject | None,
        *,
        mock: bool,
    ) -> JsonObject:
        require(not self.finished, "one evaluation opportunity")
        self.finished = True
        self.decision["provider_called"] = True
        self.decision["provider_failure"] = failure
        category = "BEHAVIORAL"
        if response is None:
            require(failure is not None, "missing response provenance")
            self.decision["failure"] = "TRANSPORT_ERROR"
            category = "INFRASTRUCTURE"
        else:
            require(failure is None, "response and provider failure are exclusive")
            self.decision["raw_response"] = json_value(response)
            metadata = response.metadata
            if (
                metadata.get("provider") != ("fake" if mock else "openai")
                or metadata.get("adapter_version") != (MOCK_VERSION if mock else ADAPTER_VERSION)
                or metadata.get("model")
                not in cast(list[JsonValue], self.config["returned_models"])
                or type(metadata.get("response_id")) is not str
                or not metadata.get("response_id")
                or metadata.get("status") not in ("completed", "incomplete")
            ):
                self.decision["failure"] = "RESPONSE_IDENTITY_MISMATCH"
                category = "INTEGRITY_EXCLUSION"
            else:
                parsed, action = parse_response(response, self.observation)
                self.decision.update(parsed)
                if action is not None:
                    submit_step(
                        self.decision,
                        self.observation,
                        action,
                        self.game,
                        self.archive,
                        self.research,
                    )
                if self.decision["failure"] in ("ENGINE_ERROR", "RECORDING_ERROR"):
                    category = "INTEGRITY_EXCLUSION"
        self.snapshot()
        assessment = evaluate(self.data)
        # Preserve the existing task predicate; add admission as a separate research axis.
        assessment["behavioral_denominator"] = category == "BEHAVIORAL"
        if category != "BEHAVIORAL":
            assessment["task_success"] = None
        self.data["evaluation"] = assessment
        self.data["category"] = category
        return self.data

    def close(self) -> None:
        self.kernel.close()
