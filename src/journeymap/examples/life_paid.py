"""Paid life CLI: default is proposal only. --execute requires human approval."""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from journeymap.adapters.life_admission import REGISTRY, load_approval, proposal
from journeymap.adapters.life_http import admit_provider
from journeymap.experiments.life import json_data
from journeymap.experiments.paid_life import PaidLifeSession
from journeymap.experiments.paid_replay import replay_paid_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--replay", type=Path)
    args = parser.parse_args()
    run_id = args.run_id or str(uuid4())
    if args.replay:
        if args.execute or args.approval:
            parser.error("replay cannot execute or consume approval")
        report = json_data(json.loads(args.replay.read_text(encoding="utf-8")))
        print(json.dumps(replay_paid_report(report)))
        return
    if not args.execute:
        print(json.dumps(proposal(run_id), ensure_ascii=True, indent=2))
        return
    if args.approval is None or args.run_id is None:
        parser.error("--execute requires --run-id and an independently authorized --approval file")
    provider = admit_provider(run_id, load_approval(args.approval), REGISTRY)
    session = PaidLifeSession(provider)
    try:
        try:
            session.run_until(provider.plan.horizon)
        except ValueError:
            if session.decider.state.status not in {"PAUSED_ERROR", "LIMIT_REACHED"}:
                raise
        # Timeout is not cancellation: recover any available late usage while
        # keeping the virtual world frozen. Otherwise preserve uncertain close.
        session.drain()
        session.finish()
        try:
            replay = session.replay_recorded()
        except ValueError:
            replay = {"verified": False, "reason": "INCOMPLETE_JOURNAL_OR_RESPONSE"}
        report = session.export_report() | {"replay": replay}
        destination = REGISTRY.parent / (run_id + ".json")
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=True, indent=2)
        print(
            json.dumps(
                {
                    "report": str(destination),
                    "tick": session.kernel.simulation_time,
                    "status": session.decider.state.status,
                }
            )
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
