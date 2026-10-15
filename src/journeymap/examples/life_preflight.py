"""Offline Responses connection preparation. Never selects live/key/network code."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from journeymap.adapters.life_llm import LifeLLMConfig, scripted_responses
from journeymap.adapters.life_responses import (
    LifeExecutionPlan,
    RecordingResponsesTransport,
    preflight_provider,
    synthetic_response,
)
from journeymap.experiments.life import json_data
from journeymap.experiments.mixed_life import MixedLifeSession
from journeymap.scenarios.life import LifeConfig


def run_preflight() -> dict[str, object]:
    plan = LifeExecutionPlan()
    transport = RecordingResponsesTransport(
        tuple((200, synthetic_response(r.text)) for r in scripted_responses())
    )
    provider = preflight_provider(transport)
    session = MixedLifeSession(
        LifeConfig(seed=plan.seed, horizon=plan.horizon),
        LifeLLMConfig(
            max_output_tokens=plan.max_output_tokens, timeout_seconds=plan.timeout_seconds
        ),
        provider=provider,
    )
    try:
        session.run_until(plan.horizon)
        replay = session.replay_recorded()
        return {
            "evidence_kind": "offline-protocol-correctness-only",
            "real_api_calls": 0,
            "real_key_reads": 0,
            "live_execution_available": False,
            "plan": asdict(plan),
            "plan_digest": plan.digest,
            "tick": session.kernel.simulation_time,
            "events": len(session.kernel.events),
            "pending_actions": len(session.kernel.in_flight),
            "requests": provider.budget.attempts,
            "replay": replay,
            "life_report": session.report(),
            "accounting_synthetic": asdict(provider.budget),
            "wire_records": [record.data for record in provider.records],
            "controller_records": session.controller_report(),
        }
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("choose a new evidence output path")
    result = json_data(run_preflight())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=True)
    print(json.dumps({k: result[k] for k in ("tick", "events", "requests", "plan_digest")}))


if __name__ == "__main__":
    main()
