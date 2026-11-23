"""Inert wire-to-engine checks and an unapproved pilot draft. No live mode."""

import argparse
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from journeymap.adapters.llm import parse_candidate, strict_json
from journeymap.adapters.route_update import intent
from journeymap.experiments.pickup_cue import step_record, submit_step
from journeymap_memoryreplica.paired import (
    digest,
    encode,
    move,
    observe,
    prepare,
    reconstruction_record,
    request,
)
from journeymap_memoryreplica.probe import source_fingerprint, validate_imports
from journeymap_memoryreplica.projection import latest_facts
from journeymap_memoryreplica.responses import (
    EFFORT,
    MAX_OUTPUT_TOKENS,
    MODEL,
    RATES,
    Frame,
    InertTransport,
    exchange,
)


def mock_frame(text: str, variant: str = "normal", index: int = 0) -> Frame:
    if variant not in {
        "normal",
        "incomplete",
        "refusal",
        "partial_frame",
        "missing_usage",
        "bad_json",
    }:
        raise ValueError("unknown mock variant")
    document: dict[str, Any] = {
        "id": f"synthetic-response-{index}",
        "model": MODEL,
        "status": "completed",
        "service_tier": "default",
        "error": None,
        "incomplete_details": None,
        "usage": {
            "input_tokens": 1000,
            "input_tokens_details": {"cached_tokens": 200},
            "output_tokens": 100,
            "output_tokens_details": {"reasoning_tokens": 80},
            "total_tokens": 1100,
        },
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
    }
    if variant == "incomplete":
        document["status"] = "incomplete"
        document["incomplete_details"] = {"reason": "max_output_tokens"}
        document["output"] = []
        document["usage"].update(
            output_tokens=8192, total_tokens=9192, output_tokens_details={"reasoning_tokens": 8192}
        )
    if variant == "refusal":
        document["output"][0]["content"] = [{"type": "refusal", "refusal": "synthetic refusal"}]
    if variant == "missing_usage":
        document["usage"] = None
    if variant == "bad_json":
        document["output"][0]["content"][0]["text"] = "{not-json"
    return Frame(encode(document).encode(), complete=variant != "partial_frame")


def run_mock_pair(
    policy: str, reconstruction_variant: str = "normal", first_action_variant: str = "normal"
) -> dict[str, Any]:
    session = prepare("unchanged", "memoryreplica-response-mock-" + policy)
    packets = []
    steps = []
    reconstruction: dict[str, Any] = {"status": "NOT_RUN"}
    stop = "NOT_RUN"
    try:
        observation, _, context = observe(session, policy)
        baseline = cast(dict[str, Any], session.kernel.state_snapshot)
        start = session.kernel.simulation_time
        common_digest = digest(context)
        reconstruction_request = request(context, "reconstruction", 6, start + 24, "B")
        first_action_request = request(context, "action", 6, start + 24, "B")
        packet = exchange(
            reconstruction_request,
            InertTransport(
                mock_frame(encode({"facts": latest_facts(context)}), reconstruction_variant)
            ),
        )
        packets.append(packet)
        outcome = packet["outcome"]
        if outcome["status"] == "TEXT_READY":
            reconstruction = reconstruction_record(
                outcome["text"], context, baseline["movement"]["routes"]
            )
        else:
            reconstruction = {"status": outcome["status"], "recall": None}
        # Known model-terminal outcomes do not remove the independent action arm.
        if outcome["status"] not in {"TEXT_READY", "INCOMPLETE", "REFUSAL"}:
            stop = "RECONSTRUCTION_OPERATIONAL_STOP"
        else:
            for index, route in enumerate(("A-to-S", "S-to-B")):
                if index:
                    observation, _, context = observe(session, policy)
                action_request = (
                    first_action_request
                    if index == 0
                    else request(context, "action", 6 - index, start + 24, "B")
                )
                packet = exchange(
                    action_request,
                    InertTransport(
                        mock_frame(
                            move(route), first_action_variant if index == 0 else "normal", index + 1
                        )
                    ),
                )
                packets.append(packet)
                outcome = packet["outcome"]
                if outcome["status"] != "TEXT_READY":
                    stop = "ACTION_" + outcome["status"]
                    break
                try:
                    candidate = intent(strict_json(outcome["text"]))
                    action = parse_candidate(candidate, observation)
                except (ValueError, TypeError, UnicodeError, RecursionError):
                    stop = "ACTION_INVALID_OUTPUT"
                    break
                step = step_record("responses-inert-probe")
                submit_step(
                    step, observation, action, session.game, session.archive, session.research
                )
                steps.append(step)
                if step["failure"] is not None:
                    stop = "ENGINE_OR_RECORDING_ERROR"
                    break
                if session.position == "B":
                    stop = "ARRIVED"
                    break
        return {
            "memory_policy": policy,
            "evidence_kind": "SYNTHETIC_RESPONSES_AND_SCRIPTED_ACTIONS",
            "external_api_calls": 0,
            "common_context_digest": common_digest,
            "reconstruction": reconstruction,
            "packets": packets,
            "engine_steps": steps,
            "stop": stop,
            "final_position": session.position,
            "elapsed_ticks": session.kernel.simulation_time - start,
        }
    finally:
        session.close()


def draft_plan() -> dict[str, Any]:
    orders = [("none", "recent", "full"), ("recent", "full", "none"), ("full", "none", "recent")]
    cases = [
        {"repeat": i + 1, "order": j + 1, "memory_policy": p}
        for i, order in enumerate(orders)
        for j, p in enumerate(order)
    ]
    per_call = (
        Decimal(400000) * Decimal(RATES["input"])
        + Decimal(MAX_OUTPUT_TOKENS) * Decimal(RATES["output"])
    ) / Decimal(1_000_000)
    return {
        "state": "DRAFT_NOT_EXECUTABLE",
        "authorized": False,
        "model": MODEL,
        "reasoning_effort": EFFORT,
        "service_tier": "default",
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "scenario": "stable-A13-goal-B",
        "cases": cases,
        "reconstruction_cap": 9,
        "action_cap": 54,
        "request_cap": 63,
        "max_decisions_per_case": 6,
        "max_ticks_per_case": 24,
        "retries": 0,
        "source_pairing": "same prepared experience; two stateless requests; no response sharing",
        "rates_per_million_usd": RATES,
        "input_bound_basis": (
            "conservative 400000-token model context ceiling, not measured input size"
        ),
        "per_call_conservative_rate_ceiling_usd": str(per_call),
        "batch_conservative_rate_ceiling_usd": str(63 * per_call),
        "output_only_ceiling_usd": str(
            Decimal(63 * MAX_OUTPUT_TOKENS) * Decimal(RATES["output"]) / Decimal(1_000_000)
        ),
        "proposed_new_code_budget_usd": "22.00",
        "cost_caveat": (
            "conservative standard text-rate calculation; not predicted spend or billing guarantee"
        ),
        "readiness": {
            "schema_service_acceptance": "UNTESTED",
            "account_access": "UNTESTED",
            "http_and_durable_journal_binding": "NOT_CONNECTED",
            "desktop_integration": "PENDING",
            "human_execution_approval": "ABSENT",
        },
    }


def collect() -> dict[str, Any]:
    return {
        "schema": "memoryreplica-responses-preparation-1",
        "external_api_calls": 0,
        "normal_pairs": [run_mock_pair(p) for p in ("none", "recent", "full")],
        "failure_probes": [
            run_mock_pair("full", first_action_variant=v)
            for v in ("incomplete", "refusal", "partial_frame", "missing_usage", "bad_json")
        ],
        "draft_plan": draft_plan(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    validate_imports(args.source_root)
    base = (args.source_root / "src").resolve()
    from journeymap_memoryreplica import responses

    if not Path(__file__).resolve().is_relative_to(base) or not Path(
        responses.__file__
    ).resolve().is_relative_to(base):
        raise ValueError("response module import/source mismatch")
    before = source_fingerprint(args.source_root)
    result = collect()
    if before != source_fingerprint(args.source_root):
        raise ValueError("source changed")
    result["current_source_hashes"] = before
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        encode(
            {
                "normal_pairs": 3,
                "failure_probes": 5,
                "source_files": len(before),
                "external_api_calls": 0,
            }
        )
    )


if __name__ == "__main__":
    main()
