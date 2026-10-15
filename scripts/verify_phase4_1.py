"""Reproducible offline Phase 4.1 evidence; preserve every prior output directory."""

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from journeymap.experiments.checkpoint import checkpoint_digest, load_checkpoint, save_checkpoint
from journeymap.experiments.life import LifeSession, export_life, run_life
from journeymap.scenarios.life import LifeConfig

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/phase4_1/final"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=False)
    comparisons = {}
    for case, changes in {
        "normal": {},
        "stockout": {"bread": 0},
        "no-money": {"worker_money": 0},
        "no-materials": {"flour": 0},
        "no-wages": {"employer_money": 0},
        "closure": {"closure_tick": 2},
    }.items():
        run = run_life(replace(LifeConfig(), **changes), verify_replay=True)
        export_life(run, OUT / case)
        baseline = ROOT / "artifacts/phase2/final" / case
        checked = []
        for path in sorted((OUT / case).iterdir()):
            if path.suffix == ".jsonl":
                left = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                right = [
                    json.loads(line)
                    for line in (baseline / path.name).read_text(encoding="utf-8").splitlines()
                ]
            else:
                left = json.loads(path.read_text(encoding="utf-8"))
                right = json.loads((baseline / path.name).read_text(encoding="utf-8"))
                if path.name == "summary.json":
                    for key in ("elapsed_seconds", "simulation_elapsed_seconds"):
                        left.pop(key)
                        right.pop(key)
            if left != right:
                raise AssertionError(f"Phase 2 regression: {case}/{path.name}")
            checked.append(path.name)
        comparisons[case] = {"equal": checked, "replay": run.summary["reproduction"]}
        print(case, "Phase 2 structural equality and both replays passed", flush=True)
    continuous = LifeSession(LifeConfig())
    try:
        continuous.run_until(168)
        expected = checkpoint_digest(continuous)
        resumes = []
        for tick, kind in ((2, "MOVE"), (8, "WORK"), (30, "REST")):
            session = LifeSession(LifeConfig())
            source = OUT / f"checkpoint-{kind.lower()}.json"
            destination = OUT / f"resumed-{kind.lower()}.json"
            try:
                session.run_until(tick)
                assert kind in {f.request.action_type for f in session.kernel.in_flight}
                save_checkpoint(session, source)
            finally:
                session.close()
            child = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "journeymap.examples.life_checkpoint",
                    "--resume",
                    str(source),
                    "--until",
                    "168",
                    "--save",
                    str(destination),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            restored = load_checkpoint(destination)
            try:
                assert checkpoint_digest(restored) == expected
                assert restored.inputs == continuous.inputs
                assert restored.decisions == continuous.decisions
                assert restored.report() == continuous.report()
                resumes.append(
                    {
                        "saved_at": tick,
                        "pending_kind": kind,
                        "child": json.loads(child.stdout),
                        "full_checkpoint_equal": True,
                        "inputs_decisions_report_equal": True,
                    }
                )
            finally:
                restored.close()
            print(kind, "fresh-process continuation passed", flush=True)
        (OUT / "verification.json").write_text(
            json.dumps(
                {
                    "phase2_regression": comparisons,
                    "fresh_process": resumes,
                    "wallclock_fields_excluded": ["elapsed_seconds", "simulation_elapsed_seconds"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    finally:
        continuous.close()


if __name__ == "__main__":
    main()
