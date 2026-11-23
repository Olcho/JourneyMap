"""Experiment 04 offline fake demo, independent audit and inspect; no live option."""

import argparse
import json
from pathlib import Path

from journeymap.experiments.route_update import demo_fixtures, execute
from journeymap.experiments.route_update_audit import audit_directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    demo = sub.add_parser("fake")
    demo.add_argument("--output", type=Path, required=True)
    for command in ("audit", "inspect"):
        audit = sub.add_parser(command)
        audit.add_argument("directory", type=Path)
        audit.add_argument("--expected-plan-sha256")
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return 0
    try:
        result = (
            execute(demo_fixtures(), args.output)
            if args.command == "fake"
            else audit_directory(args.directory, expected_plan_sha256=args.expected_plan_sha256)
        )
    except (OSError, TypeError, ValueError, RuntimeError):
        print('{"status":"REFUSED","reason":"OFFLINE_PREFLIGHT_OR_RECORDING"}')
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "INCLUDED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
