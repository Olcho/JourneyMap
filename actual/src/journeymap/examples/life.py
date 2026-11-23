"""Offline finite life CLI; no provider, API key, or interactive input."""

import argparse
from dataclasses import replace
from pathlib import Path

from journeymap.experiments.life import export_life, readable_summary, run_life
from journeymap.scenarios.life import LifeConfig


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case",
        choices=("normal", "no-wages", "no-materials", "stockout", "no-money", "closure"),
        default="normal",
    )
    parser.add_argument("--horizon", type=int, default=168)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-replay", action="store_true")
    args = parser.parse_args()
    config = LifeConfig(horizon=args.horizon)
    changes = {
        "normal": {},
        "no-wages": {"employer_money": 0},
        "no-materials": {"flour": 0},
        "stockout": {"bread": 0},
        "no-money": {"worker_money": 0},
        "closure": {"closure_tick": 2},
    }
    config = replace(config, **changes[args.case])
    run = run_life(config, verify_replay=args.verify_replay)
    print(readable_summary(run))
    if args.output:
        export_life(run, args.output)


if __name__ == "__main__":
    main()
