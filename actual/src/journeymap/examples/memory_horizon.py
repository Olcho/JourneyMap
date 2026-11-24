"""Offline fixtures only. No credentials, network transport, or live mode."""

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from journeymap.adapters.event_memory import RecencyEventMemory
from journeymap.adapters.llm import LLMController
from journeymap.adapters.memory import NoMemory
from journeymap.adapters.memory_horizon_prompt import PROFILE
from journeymap.adapters.provider import ProviderIdentity, ProviderRequest, RawModelResponse
from journeymap.core.canonical import JsonObject
from journeymap.experiments.alderwick import export_trial
from journeymap.experiments.memory_horizon import run_trial
from journeymap.experiments.memory_horizon_audit import audit_export, replay_export


@dataclass(slots=True)
class HorizonFakeProvider:
    """Stateful harness script for offline mechanics, NOT behavioral evidence.

    A fresh internal cursor executes the same deterministic plan in all four
    conditions. Only current location and exits are read from the prompt. No
    Event Memory, archive, condition/k or clock is consulted. The cursor advances
    on generated moves, not receipts: this fixture assumes successful delivery
    of its valid plan and is not a retry/recovery or navigation model.
    """

    destinations: tuple[str, ...] = ("inn", "bakery", "well")
    _cursor: int = field(default=0, init=False, repr=False)

    @property
    def identity(self) -> ProviderIdentity:
        return ProviderIdentity("fake", "memory-horizon-cursor-fixture-2")

    def generate(self, request: ProviderRequest) -> RawModelResponse:
        data = json.loads(request.prompt.split("\nINPUT_JSON\n", 1)[1])
        observation = data["observation"]
        sections = {
            section["contributor_id"]: section["content"]
            for section in observation["content"]["sections"]
        }
        location = sections["position"]["position"]["location_id"]
        slot = self._cursor // 2
        destination = (
            "village-square"
            if location != "village-square"
            else self.destinations[slot]
            if slot < len(self.destinations)
            else None
        )
        exits = sections["local"]["local"]["exits"]
        route = next((e for e in exits if e["destination"] == destination), None)
        action: JsonObject = (
            {"action_type": "MOVE", "payload": {"route_id": route["route_id"]}}
            if route is not None
            else {"action_type": "WAIT", "payload": {"duration": 1}}
        )
        if route is not None:
            self._cursor += 1
        return RawModelResponse(
            json.dumps(action),
            {
                "provider": "fake",
                "adapter_version": self.identity.version,
                "model": request.model,
            },
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory", choices=("no-memory", "recency-k1", "recency-k2", "recency-k3"))
    parser.add_argument(
        "--fixture", choices=("complete", "repeat", "incomplete"), default="complete"
    )
    parser.add_argument("--trial-id")
    parser.add_argument("--output", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--audit", type=Path)
    mode.add_argument("--replay", type=Path)
    args = parser.parse_args()
    if args.audit:
        audit = audit_export(args.audit)
        print(json.dumps(audit, indent=2))
        raise SystemExit(0 if audit["status"] == "INCLUDED" else 1)
    if args.replay:
        report = replay_export(args.replay)
        print(f"Replay equality: True; tick={report.final_simulation_time}")
        return
    if not args.memory or not args.trial_id or args.output is None:
        parser.error("--memory, --trial-id and --output are required for an offline fixture")
    plans = {
        "complete": ("inn", "bakery", "well"),
        "repeat": ("inn", "bakery", "well", "inn"),
        "incomplete": ("inn", "bakery"),
    }
    memory = NoMemory() if args.memory == "no-memory" else RecencyEventMemory(int(args.memory[-1]))
    trial = run_trial(
        LLMController(
            HorizonFakeProvider(plans[args.fixture]),
            model="memory-horizon-fixture-2",
            event_memory=memory,
            prompt_profile=PROFILE,
        ),
        trial_id=args.trial_id,
    )
    export_trial(trial, args.output)
    report = replay_export(args.output)
    audit = audit_export(args.output)
    print(
        json.dumps(
            {
                "audit": audit,
                "end_tick": report.final_simulation_time,
                "metrics": cast(JsonObject, trial.data["metrics"]),
            },
            indent=2,
        )
    )
    raise SystemExit(0 if audit["status"] == "INCLUDED" else 1)


if __name__ == "__main__":
    main()
