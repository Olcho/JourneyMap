"""Tick-3 code policy evidence; never fed back to either network input."""

from typing import cast

from journeymap.adapters.llm import Record
from journeymap.adapters.route_update import obj
from journeymap.adapters.route_update_policy import first_policy
from journeymap.core.canonical import JsonObject, JsonValue
from journeymap.experiments.route_update_live import branch_summary as legacy_summary
from journeymap.experiments.route_update_live_session import LiveSession


class CountedSession(LiveSession):
    def __init__(self, run_id: str, config: JsonObject, mode: str) -> None:
        self.first_move_policies: JsonObject | None = None
        super().__init__(run_id, config, mode)

    def prepare(self, *, initial: bool = False) -> JsonObject:
        prepared = super().prepare(initial=initial)
        if not initial and not self.steps:
            inputs = obj(prepared["semantic_input"])
            self.first_move_policies = {
                p: first_policy(inputs, p) for p in ("OLD-MAP", "RECEIVED-UPDATE")
            }
        return prepared

    def snapshot(self) -> JsonObject:
        value = super().snapshot()
        value["first_move_policies"] = self.first_move_policies
        return Record.capture(value).data


def summary(
    prefixes: list[JsonObject],
    branches: list[JsonObject],
    plan: JsonObject,
    prepared_baselines: JsonObject,
) -> JsonObject:
    value = legacy_summary(prefixes, branches, plan)
    policies: list[JsonValue] = []
    for run_id, result in prepared_baselines.items():
        branch = next((b for b in branches if b["run_id"] == run_id), None)
        policies.append(
            {
                "run_id": run_id,
                "first_move_policies": result,
                "policy_computed": True,
                "scope": "first-evaluation-move-only",
                "model_called": any(
                    s["provider_called"] is True for s in cast(list[JsonObject], branch["steps"])
                )
                if branch
                else False,
                "model_first_judgment": branch["first_judgment"] if branch else None,
            }
        )
    value["code_baselines"] = policies
    value["service_confirmation"] = "NOT_ESTABLISHED_BY_MOCK_OR_UNSIGNED_AUDIT"
    return value
