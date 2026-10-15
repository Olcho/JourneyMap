"""Data-only paid report replay; no Provider, credential or network construction."""

import hashlib
from dataclasses import asdict
from typing import Any, cast

from journeymap.adapters.life_admission import protocol_identity, source_identity
from journeymap.adapters.life_http import paid_body, paid_request, parsed_response
from journeymap.adapters.life_llm import LifeLLMConfig, raw_record
from journeymap.adapters.life_responses import LifeExecutionPlan
from journeymap.adapters.llm import Record, strict_json
from journeymap.adapters.provider import ProviderRequest
from journeymap.application.concurrent import ConcurrentReceipt
from journeymap.application.concurrent_replay import LiveInput, replay_live_inputs
from journeymap.bootstrap_life import create_life_application, create_life_kernel
from journeymap.core.canonical import JsonObject, canonical_json
from journeymap.core.handlers import ActionRequest
from journeymap.core.scheduler import ScheduledEventSpec
from journeymap.experiments.life import json_data
from journeymap.experiments.life_decisions import LifeCall, LifeDecisionService
from journeymap.experiments.paid_audit import audit_controller
from journeymap.scenarios.life import LifeConfig


def replay_paid_report(report: JsonObject) -> JsonObject:
    try:
        return _replay_paid_report(report)
    except (KeyError, IndexError, TypeError, AttributeError, RecursionError):
        raise ValueError("paid audit malformed report") from None


def _replay_paid_report(report: JsonObject) -> JsonObject:
    data = cast(dict[str, Any], report)
    if data.get("report_schema") != "life-paid-report-2":
        raise ValueError("paid audit report schema required")
    controller, plan = data["controller"], LifeExecutionPlan()
    bound = controller["binding"]
    if (bound["source_digest"], bound["protocol_digest"], bound["plan_digest"]) != (
        source_identity(),
        protocol_identity(),
        plan.digest,
    ):
        raise ValueError("paid replay source/protocol/plan mismatch")
    previous = "0" * 64
    rows = controller["journal"]
    for ordinal, row in enumerate(rows, 1):
        if (
            type(row["ordinal"]) is not int
            or row["ordinal"] != ordinal
            or row["previous"] != previous
            or row["run_id"] != bound["run_id"]
        ):
            raise ValueError("paid replay journal mismatch")
        previous = hashlib.sha256(canonical_json(row).encode()).hexdigest()
    inputs = []
    for original in data["inputs"]:
        entry = original.copy()
        if entry["schedule"] is not None:
            entry["schedule"] = ScheduledEventSpec(**entry["schedule"])
        if entry["request"] is not None:
            entry["request"] = ActionRequest(**entry["request"])
        if entry["receipt"] is not None:
            entry["receipt"] = ConcurrentReceipt(**entry["receipt"])
        inputs.append(LiveInput(**entry))
    kernel = create_life_kernel(LifeConfig(seed=plan.seed, horizon=plan.horizon))
    app = create_life_application(kernel)
    replay = replay_live_inputs(lambda: (kernel, app), tuple(inputs))
    if (
        replay.final_execution != data["execution"]
        or json_data({"v": [asdict(e) for e in kernel.events]})["v"] != data["events"]
        or json_data({"v": [asdict(r) for r in kernel.action_results]})["v"] != data["results"]
    ):
        raise ValueError("paid replay engine mismatch")
    observed = {o.observation_id: o for o in replay.observations}
    received = {r["data"]["sequence"]: r["data"] for r in rows if r["stage"] == "RESPONSE_RECEIVED"}
    attempted = {r["data"]["sequence"]: r["data"] for r in rows if r["stage"] == "ATTEMPTED"}
    receipts = {r["data"]["sequence"]: r["data"] for r in rows if r["stage"] == "ACTOR_RECEIPT"}
    calls: list[LifeCall] = []
    for original in controller["calls"]:
        value = original.copy()
        value["request"] = ProviderRequest(**value["request"])
        for field, cls in (
            ("raw", Record),
            ("action", ActionRequest),
            ("receipt", ConcurrentReceipt),
            ("final_result", ConcurrentReceipt),
        ):
            if value[field] is not None:
                value[field] = cls(**value[field])
        call = LifeCall(**value)
        calls.append(call)
        observation = observed[call.observation_id]
        if call.request != paid_request(observation, plan) or call.body != paid_body(call.request):
            raise ValueError("paid replay request mismatch")
        if call.raw is not None:
            document = strict_json(received.get(call.sequence, {}).get("raw", "null"))
            if (
                attempted.get(call.sequence, {}).get("body") != call.body
                or not isinstance(document, dict)
                or raw_record(parsed_response(document)) != call.raw
            ):
                raise ValueError("paid journal raw mismatch")
        if call.error == "TIMEOUT" and call.discarded_late:
            if any(
                x is not None
                for x in (call.action, call.candidate, call.receipt, call.final_result)
            ):
                raise ValueError("paid replay late action")
        elif call.raw is not None and call.status != "READY":
            probe = LifeDecisionService(LifeLLMConfig(max_output_tokens=4096, timeout_seconds=120))
            probe.state.calls = [call]
            probe.state.ready, probe.state.observation, probe.state.status = (
                call.raw,
                observation,
                "READY",
            )
            action = probe.resolve()
            if (
                action != call.action
                or probe.state.calls[0].candidate != call.candidate
                or probe.state.calls[0].error != call.error
            ):
                raise ValueError("paid replay classification mismatch")
        if call.action is not None:
            expected = json_data(
                {
                    "sequence": call.sequence,
                    "action": asdict(call.action),
                    "receipt": asdict(call.receipt) if call.receipt else None,
                }
            )
            if receipts.get(call.sequence) != expected or not any(
                i.request == call.action and i.receipt == call.receipt for i in inputs
            ):
                raise ValueError("paid replay receipt mismatch")
            if (
                call.final_result is not None
                and app.game_for(call.actor_id).result(call.action.action_request_id)
                != call.final_result
            ):
                raise ValueError("paid replay completion mismatch")
    audit = audit_controller(controller, calls, list(replay.observations), inputs, app)
    return {
        "provider_recalled": False,
        "engine_equal": True,
        "journal_checked": True,
        "calls_checked": len(controller["calls"]),
    } | audit
