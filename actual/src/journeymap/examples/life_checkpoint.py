"""Offline checkpoint example, also usable as a fresh-process resume worker."""

import argparse
import json
from pathlib import Path

from journeymap.experiments.checkpoint import checkpoint_digest, load_checkpoint, save_checkpoint
from journeymap.experiments.life import LifeSession
from journeymap.scenarios.life import LifeConfig


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--until", type=int, default=168)
    parser.add_argument("--save", type=Path, required=True)
    parser.add_argument("--closure", action="store_true")
    args = parser.parse_args()
    session = (
        load_checkpoint(args.resume)
        if args.resume
        else LifeSession(LifeConfig(closure_tick=2 if args.closure else None))
    )
    try:
        session.run_until(args.until)
        save_checkpoint(session, args.save)
        print(
            json.dumps(
                {
                    "tick": session.kernel.simulation_time,
                    "digest": checkpoint_digest(session),
                    "pending": [f.request.action_type for f in session.kernel.in_flight],
                    "events": len(session.kernel.events),
                }
            )
        )
    finally:
        session.kernel.close()


if __name__ == "__main__":
    main()
