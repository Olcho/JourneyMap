"""Explicit --live opt-in; default is an offline mixed-action protocol fixture."""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from journeymap.adapters.llm import DEFAULT_MODEL, LLMController
from journeymap.adapters.openai_provider import OpenAIProvider
from journeymap.adapters.provider import Provider, ProviderRequest, RawModelResponse
from journeymap.core.canonical import JsonObject
from journeymap.experiments.alderwick import TrialPolicy, export_trial, replay_export, run_trial


class ProtocolFakeProvider:
    """A test fixture, not a model or evidence of LLM behaviour."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: ProviderRequest) -> RawModelResponse:
        data = json.loads(request.prompt.split("\nINPUT_JSON\n", 1)[1])
        plan: tuple[tuple[str, JsonObject], ...] = (
            ("MOVE", {"route_id": "west-gate-to-village-square"}),
            (
                "ASK",
                {"target_actor_id": "hugh", "subject_ref": "east-bridge", "predicate": "condition"},
            ),
            ("MOVE", {"route_id": "village-square-to-bakery"}),
            ("BUY", {"offer_id": "edwin-bread", "quantity": 2}),
            ("CONSUME", {"item_id": "bread", "quantity": 1}),
            ("MOVE", {"route_id": "bakery-to-village-square"}),
            (
                "ASK",
                {"target_actor_id": "hugh", "subject_ref": "east-bridge", "predicate": "condition"},
            ),
            (
                "REQUEST",
                {
                    "target_actor_id": "thomas",
                    "request_kind": "travel_advice",
                    "request_payload_json": "{}",
                },
            ),
        )
        if self.calls < len(plan):
            action, payload = plan[self.calls]
        elif self.calls == len(plan):
            records = [
                record
                for section in data["observation"]["content"]["sections"]
                if section["module_id"] == "knowledge"
                for record in section["content"]["records"]
                if record["subject_ref"] == "east-bridge" and record["predicate"] == "condition"
            ]
            action, payload = (
                "INFORM",
                {
                    "target_actor_id": "thomas",
                    "claim_record_id": records[-1]["knowledge_record_id"],
                },
            )
        else:
            action, payload = "REST", {"duration": 1}
        self.calls += 1
        return RawModelResponse(
            json.dumps({"action_type": action, "payload": payload}),
            {"provider": "fake", "model": "protocol-fixture-1"},
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Opt in to paid OpenAI API calls")
    parser.add_argument("--replay", type=Path, help="Replay an export without any Provider calls")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--trial-id")
    parser.add_argument("--model", default=os.environ.get("JOURNEYMAP_MODEL", DEFAULT_MODEL))
    parser.add_argument(
        "--reasoning-effort",
        default="medium",
        choices=("none", "low", "medium", "high", "xhigh", "max"),
    )
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--max-decisions", type=int, default=64)
    parser.add_argument("--max-provider-calls", type=int, default=64)
    parser.add_argument("--wall-timeout", type=float, default=300)
    parser.add_argument("--provider-timeout", type=float, default=30)
    args = parser.parse_args()
    if args.replay:
        report = replay_export(args.replay)
        print(
            f"Replay equality: True; tick={report.final_simulation_time}; "
            f"digest={report.final_state_digest}"
        )
        return
    if args.live and not os.environ.get("OPENAI_API_KEY"):
        parser.error("--live requires OPENAI_API_KEY in the environment")
    provider: Provider = (
        OpenAIProvider(timeout_seconds=args.provider_timeout)
        if args.live
        else ProtocolFakeProvider()
    )
    model = args.model if args.live else "protocol-fixture-1"
    trial_id = args.trial_id or datetime.now(UTC).strftime("m8-%Y%m%dT%H%M%S%fZ")
    output = args.output or Path("trials") / trial_id
    trial = run_trial(
        LLMController(
            provider,
            model=model,
            parameters={
                "reasoning": {"effort": args.reasoning_effort},
                "max_output_tokens": args.max_output_tokens,
            },
        ),
        trial_id=trial_id,
        policy=TrialPolicy(
            args.max_decisions, args.max_provider_calls, wall_timeout_seconds=args.wall_timeout
        ),
        provider_name="openai" if args.live else "fake",
        provider_version=OpenAIProvider.version if args.live else "fixture-1",
    )
    export_trial(trial, output)
    replay_export(output)
    print(json.dumps({key: trial.data[key] for key in ("manifest", "metrics")}, indent=2))
    print(f"Export: {output.resolve()}")
    manifest = cast(JsonObject, trial.data["manifest"])
    if not manifest["replay_equal"]:
        raise SystemExit("Replay proof failed")


if __name__ == "__main__":
    main()
