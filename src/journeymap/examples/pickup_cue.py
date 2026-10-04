"""Offline pickup diagnostic; no live toggle, credentials, or transport option."""

import argparse
import json
from pathlib import Path

from journeymap.adapters.pickup_cue_prompt import Fixture
from journeymap.experiments.pickup_cue import CONDITIONS, export_case, run_case, run_matrix
from journeymap.experiments.pickup_cue_audit import (
    audit_export,
    audit_matrix,
    read_export,
    replay_record,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--matrix", action="store_true")
    mode.add_argument("--audit", type=Path)
    mode.add_argument("--audit-matrix", type=Path)
    mode.add_argument("--replay", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case-id")
    parser.add_argument("--target", choices=("inn", "bakery"))
    parser.add_argument("--distance", type=int, choices=(1, 2, 3, 4))
    parser.add_argument("--memory", choices=CONDITIONS)
    parser.add_argument(
        "--fixture",
        choices=(
            "notice",
            "wait",
            "inn",
            "bakery",
            "unknown-route",
            "malformed",
            "refusal",
            "incomplete",
            "provider-error",
            "transport-error",
        ),
        default="notice",
    )
    args = parser.parse_args()
    if args.replay:
        report = replay_record(read_export(args.replay))
        print(f"Replay equality: True; tick={report.final_simulation_time}")
        return
    if args.audit or args.audit_matrix:
        result = audit_export(args.audit) if args.audit else audit_matrix(args.audit_matrix)
    else:
        if args.output is None:
            parser.error("--output must be a new directory")
        fixture = Fixture()
        # Fixed choices are data, never an injectable Provider implementation.
        if args.fixture in ("wait", "inn", "bakery", "unknown-route", "malformed"):
            text = (
                "not json"
                if args.fixture == "malformed"
                else '{"action_type":"WAIT","payload":{"duration":1}}'
                if args.fixture == "wait"
                else json.dumps(
                    {
                        "action_type": "MOVE",
                        "payload": {
                            "route_id": "missing"
                            if args.fixture == "unknown-route"
                            else f"village-square-to-{args.fixture}"
                        },
                    }
                )
            )
            fixture = Fixture("fixed", text)
        else:
            fixture = Fixture(args.fixture)
        if args.matrix:
            result = run_matrix(args.output, fixture=fixture)
        else:
            if not args.case_id or not args.target or not args.distance or not args.memory:
                parser.error("--case-id, --target, --distance and --memory required")
            record = run_case(
                case_id=args.case_id,
                target=args.target,
                d=args.distance,
                condition=args.memory,
                fixture=fixture,
            )
            export_case(record, args.output)
            result = audit_export(args.output)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "INCLUDED" else 1)


if __name__ == "__main__":
    main()
