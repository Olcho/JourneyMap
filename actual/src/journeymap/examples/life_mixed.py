"""Explicit offline Ada Provider example; no real model, keys or network."""

import argparse
import json
from pathlib import Path

from journeymap.adapters.life_llm import LifeLLMConfig
from journeymap.experiments.checkpoint import checkpoint_digest, load_checkpoint, save_checkpoint
from journeymap.experiments.life import LifeRun, export_life, json_data
from journeymap.experiments.mixed_life import MixedLifeSession
from journeymap.scenarios.life import LifeConfig


def main() -> None:
    from dataclasses import asdict

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--until", type=int, default=24)
    parser.add_argument("--limit", type=int, default=32)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--save", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    session = (
        load_checkpoint(args.resume)
        if args.resume
        else MixedLifeSession(LifeConfig(horizon=24), LifeLLMConfig(request_limit=args.limit))
    )
    if not isinstance(session, MixedLifeSession):
        session.close()
        raise ValueError("expected an offline mixed checkpoint")
    try:
        try:
            session.run_until(args.until)
        except ValueError:
            if session.decider.state.status not in {"PAUSED_ERROR", "LIMIT_REACHED"}:
                raise
        report = session.controller_report()
        report["replay"] = session.replay_recorded()
        report["checkpoint_digest"] = checkpoint_digest(session)
        report["tick"] = session.kernel.simulation_time
        if args.save:
            save_checkpoint(session, args.save)
        if args.output:
            run = LifeRun(
                session.report(),
                session.decisions,
                tuple(session.inputs),
                tuple(json_data(asdict(e)) for e in session.kernel.events),
                tuple(json_data(asdict(r)) for r in session.kernel.action_results),
                tuple(json_data(asdict(a)) for a in session.application.attempts),
                session.kernel.execution_snapshot,
            )
            export_life(run, args.output)
            (args.output / "provider.json").write_text(
                json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8"
            )
        print(
            json.dumps(
                {
                    k: report[k]
                    for k in ("tick", "status", "requests_started", "replay", "checkpoint_digest")
                },
                ensure_ascii=False,
            )
        )
    finally:
        session.close()


if __name__ == "__main__":
    main()
