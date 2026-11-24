"""Describe an inactive policy, run with pre-provisioned authority, or audit stored data."""

import argparse
from pathlib import Path

from journeymap.adapters.probe_continuation import Anchor, ContinuationApproval, inactive_proposal
from journeymap.core.canonical import canonical_json
from journeymap.examples.probe_execution import read, write
from journeymap.experiments import continued_probe


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("describe")
    audit = commands.add_parser("audit")
    audit.add_argument("report", type=Path)
    audit.add_argument("--frozen-source", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--authority", type=Path, required=True)
    run.add_argument("--checkpoint", type=Path, required=True)
    run.add_argument("--approval", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "describe":
        print(canonical_json(inactive_proposal()))
    elif args.command == "audit":
        print(canonical_json(continued_probe.audit_report(read(args.report), args.frozen_source)))
    else:
        anchor = Anchor.parse(read(args.checkpoint))
        approval = ContinuationApproval.parse(read(args.approval))
        # Creation is explicit CLI output admission, never a missing-authority fallback.
        args.output.mkdir(parents=True, exist_ok=False)
        report = continued_probe.execute(args.authority, anchor, approval)
        write(args.output / "report.json", report)
        print(
            canonical_json(
                {
                    "accounting": report["accounting"],
                    "lineage_accounting": report["lineage_accounting"],
                }
            )
        )


if __name__ == "__main__":
    main()
