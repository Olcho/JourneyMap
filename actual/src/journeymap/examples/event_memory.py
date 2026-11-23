"""Phase 1 offline correctness fixture. This entry point has no live mode."""

import argparse
import json
from pathlib import Path

from journeymap.adapters.event_memory import (
    EVENT_MEMORY_PROTOCOL_VERSION,
    RECENCY_WINDOW_PROTOCOL_VERSION,
    RecencyEventMemory,
)
from journeymap.adapters.llm import LLMController
from journeymap.adapters.memory import NoMemory
from journeymap.examples.alderwick_llm import ProtocolFakeProvider
from journeymap.experiments.alderwick import audit_export, export_trial, replay_export, run_trial


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--memory", choices=("no-memory", "recency-k1", "recency-k2", "recency-k3"), required=True
    )
    parser.add_argument("--trial-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    memory = (
        NoMemory() if args.memory == "no-memory" else RecencyEventMemory(k=int(args.memory[-1]))
    )
    trial = run_trial(
        LLMController(ProtocolFakeProvider(), model="protocol-fixture-1", event_memory=memory),
        trial_id=args.trial_id,
        protocol_version=(
            RECENCY_WINDOW_PROTOCOL_VERSION
            if args.memory in ("recency-k2", "recency-k3")
            else EVENT_MEMORY_PROTOCOL_VERSION
        ),
    )
    export_trial(trial, args.output)
    report = replay_export(args.output)
    audit = audit_export(args.output)
    print(json.dumps({"audit": audit, "end_tick": report.final_simulation_time}, indent=2))
    print(f"Export: {args.output.resolve()}")
    raise SystemExit(0 if audit["status"] == "INCLUDED" else 1)


if __name__ == "__main__":
    main()
