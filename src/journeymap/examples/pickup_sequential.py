"""Separate sequential diagnostic CLI; defaults offline, no automatic API execution."""

import argparse
import json
from pathlib import Path

from journeymap.experiments.pickup_cue_live_audit import read_json
from journeymap.experiments.pickup_sequential import execute
from journeymap.experiments.pickup_sequential_audit import audit_directory
from journeymap.experiments.pickup_sequential_plan import make_plan
from journeymap.experiments.pilot_journal import write_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    plan = sub.add_parser("plan")
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--mode", choices=("recording-mock", "live"), default="recording-mock")
    plan.add_argument("--config", type=Path)
    plan.add_argument("--reviewed", action="store_true")
    plan.add_argument("--allocation-count", type=int, default=40)
    plan.add_argument("--call-cap", type=int)
    plan.add_argument("--engine-seed", type=int, default=42)
    plan.add_argument("--allocation-seed", type=int, default=6)
    for mode in ("mock", "live"):
        run = sub.add_parser(mode)
        run.add_argument("--plan", type=Path, required=True)
        run.add_argument("--output", type=Path, required=True)
        if mode == "live":
            run.add_argument("--enable-live", action="store_true")
            run.add_argument("--approved-plan-sha256")
    for name in ("audit", "inspect"):
        audit = sub.add_parser(name)
        audit.add_argument("directory", type=Path)
        audit.add_argument("--expected-plan-sha256")
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return 0
    try:
        if args.command == "plan":
            result = make_plan(
                config=read_json(args.config) if args.config else None,
                mode=args.mode,
                reviewed=args.reviewed,
                allocation_count=args.allocation_count,
                call_cap=args.call_cap,
                engine_seed=args.engine_seed,
                allocation_seed=args.allocation_seed,
            )
            write_snapshot(args.output, result)
        elif args.command in ("mock", "live"):
            result = execute(
                read_json(args.plan),
                args.output,
                live=args.command == "live",
                enable_live=getattr(args, "enable_live", False),
                approved_plan_sha256=getattr(args, "approved_plan_sha256", None),
            )
        else:
            result = audit_directory(args.directory, expected_plan_sha256=args.expected_plan_sha256)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        print('{"status":"REFUSED","reason":"PREFLIGHT_OR_RECORDING_ERROR"}')
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result.get("status", "INCLUDED") == "INCLUDED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
