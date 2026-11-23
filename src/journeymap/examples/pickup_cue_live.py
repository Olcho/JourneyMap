"""Pickup diagnostic preparation CLI. No arguments only prints help; never calls an API."""

import argparse
import json
from pathlib import Path

from journeymap.experiments.pickup_cue_live import execute
from journeymap.experiments.pickup_cue_live_audit import audit_directory, read_json
from journeymap.experiments.pickup_cue_live_plan import MODES, make_plan, mock_config
from journeymap.experiments.pilot_journal import write_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    plan = commands.add_parser("plan", help="write an unresolved draft or reviewed configuration")
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--mode", choices=MODES, default="recording-mock")
    config = plan.add_mutually_exclusive_group()
    config.add_argument("--config", type=Path)
    config.add_argument("--mock-config", action="store_true")
    plan.add_argument("--engine-seed", type=int, default=42)
    plan.add_argument("--allocation-seed", type=int, default=0)
    plan.add_argument("--allocation-count", type=int, default=40)
    plan.add_argument("--call-cap", type=int, default=40)
    for mode in ("mock", "live"):
        run = commands.add_parser(mode)
        run.add_argument("--plan", type=Path, required=True)
        run.add_argument("--output", type=Path, required=True)
        if mode == "live":
            run.add_argument("--enable-live", action="store_true")
            run.add_argument("--approved-plan-sha256")
    for name in ("audit", "inspect"):
        audit = commands.add_parser(name, help="read-only Provider-free complete/prefix audit")
        audit.add_argument("directory", type=Path)
        audit.add_argument("--expected-plan-sha256")
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return 0
    try:
        if args.command == "plan":
            configuration = (
                mock_config()
                if args.mock_config
                else read_json(args.config)
                if args.config
                else None
            )
            result = make_plan(
                config=configuration,
                mode=args.mode,
                engine_seed=args.engine_seed,
                allocation_seed=args.allocation_seed,
                allocation_count=args.allocation_count,
                call_cap=args.call_cap,
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
        # No raw exceptions/configuration/credentials in CLI diagnostics.
        print('{"status":"REFUSED","reason":"PREFLIGHT_OR_RECORDING_ERROR"}')
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result.get("status", "INCLUDED") == "INCLUDED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
