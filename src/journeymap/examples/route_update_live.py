"""E04 plans/mock/audit default paths. Live and smoke require independent human gates."""

import argparse
from pathlib import Path

from journeymap.core.canonical import canonical_json
from journeymap.experiments.pickup_cue_live_audit import read_json
from journeymap.experiments.pilot_journal import inspect_journal, write_snapshot
from journeymap.experiments.route_update import demo_fixtures
from journeymap.experiments.route_update_counted import execute
from journeymap.experiments.route_update_counted_plan import make_plan
from journeymap.experiments.route_update_limited_smoke import POLICY, authorization_template
from journeymap.experiments.route_update_live import LiveFixture
from journeymap.experiments.route_update_live_audit import audit_directory


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("plan", "mock", "audit", "inspect", "live", "smoke", "review-smoke")
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("--stage", choices=("smoke", "pilot"), default="pilot")
    parser.add_argument("--mode", choices=("live", "recording-mock"), default="live")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--count-only", action="store_true")
    parser.add_argument("--price-review", type=Path)
    parser.add_argument("--service-review", type=Path)
    parser.add_argument(
        "--smoke-count-fee-unknown",
        action="store_true",
        help="Explicit v3 smoke-only 1/1/2 cost uncertainty exception; not approval",
    )
    parser.add_argument(
        "--authorization-template",
        type=Path,
        help="New PENDING_USER_APPROVAL template path (v3 plan only)",
    )
    parser.add_argument(
        "--pilot-count-fee-unknown",
        action="store_true",
        help="Explicit v4 pilot-only 114/114/228 uncertainty exception",
    )
    parser.add_argument("--compatibility", type=Path)
    args = parser.parse_args()
    if args.pilot_count_fee_unknown and (
        args.smoke_count_fee_unknown or args.command not in ("plan", "mock")
    ):
        parser.error("v4 option only creates pilot plan/mock; no policy mixing")
    if args.compatibility and not args.pilot_count_fee_unknown:
        parser.error("compatibility requires explicit v4 pilot option")
    from journeymap.experiments.route_update_limited_pilot import POLICY as PILOT_POLICY
    from journeymap.experiments.route_update_limited_pilot import (
        authorization_template as pilot_template,
    )

    selected_policy = (
        PILOT_POLICY
        if args.pilot_count_fee_unknown
        else POLICY
        if args.smoke_count_fee_unknown
        else None
    )
    if args.smoke_count_fee_unknown and args.command not in ("plan", "mock"):
        parser.error("exception option only creates plan/mock; execution requires frozen plan")
    if args.authorization_template and (
        args.command != "plan" or not (args.smoke_count_fee_unknown or args.pilot_count_fee_unknown)
    ):
        parser.error("authorization template requires explicit v3 plan option")
    if args.command == "plan":
        if args.output is None:
            parser.error("plan requires --output")
        if args.path.exists() or not args.path.parent.is_dir():
            parser.error("plan requires a new file under an existing parent")
        plan = make_plan(
            args.stage,
            args.mode,
            args.output,
            scope="count-only" if args.count_only else "count-and-generate",
            cost_policy=selected_policy,
            price_review=read_json(args.price_review) if args.price_review else None,
            service_review=read_json(args.service_review) if args.service_review else None,
            compatibility=read_json(args.compatibility) if args.compatibility else None,
        )
        if args.authorization_template is not None:
            if args.mode != "live":
                parser.error("pending authorization template is only for live candidate")
            if (
                args.authorization_template.exists()
                or args.path.resolve() == args.authorization_template.resolve()
            ):
                parser.error("template must have a separate new path")
            write_snapshot(
                args.authorization_template,
                pilot_template(plan)
                if args.pilot_count_fee_unknown
                else authorization_template(plan),
            )
        write_snapshot(args.path, plan)
        result = plan
    elif args.command == "mock":
        plan = make_plan(
            args.stage,
            "recording-mock",
            args.path,
            scope="count-only" if args.count_only else "count-and-generate",
            cost_policy=selected_policy,
        )
        demos = demo_fixtures()
        fixtures = tuple(
            LiveFixture(
                demos[i % 2].initial.outputs[0],
                (
                    demos[i % 2].branches[0].outputs,
                    demos[i % 2].branches[1].outputs,
                    demos[i % 2].branches[2].outputs,
                ),
            )
            for i in range(1 if args.stage == "smoke" else 6)
        )
        result = execute(plan, args.path, fixtures=fixtures)
    elif args.command == "review-smoke":
        from journeymap.experiments.route_update_counted_service import review_smoke

        if args.output is None:
            parser.error("review-smoke requires new --output witness path")
        result = review_smoke(args.path)
        write_snapshot(args.output, result)
    elif args.command == "audit":
        result = audit_directory(args.path)
    elif args.command == "inspect":
        journal = inspect_journal(args.path / "attempts.jsonl")
        result = {
            "journal_status": journal.status,
            "durable_rows": len(journal.rows),
            "audit": audit_directory(args.path),
        }
    else:
        if args.plan is None or args.authorization is None:
            parser.error("live/smoke requires frozen --plan and human --authorization")
        plan = read_json(args.plan)
        if plan["mode"] != "live" or plan["stage"] != (
            "smoke" if args.command == "smoke" else "pilot"
        ):
            parser.error("command/stage/mode mismatch")
        result = execute(plan, args.path, authorization=read_json(args.authorization))
    print(canonical_json(result))


if __name__ == "__main__":
    main()
