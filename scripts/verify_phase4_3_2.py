"""Guarded offline before/after audit evidence and fresh-process CLI outcomes."""

import copy
import json
import os
import runpy
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from journeymap.adapters.life_admission import proposal
from journeymap.adapters.life_http import LifeHTTPProvider
from journeymap.experiments.paid_replay import replay_paid_report

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/phase4_3_2/final"


def write(name: str, data: object) -> None:
    with (OUT / name).open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=True, indent=2)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "normal.json").exists():
        raise FileExistsError("preserve existing evidence")
    sys.path.insert(0, str(ROOT / "tests"))
    fixtures = runpy.run_path(str(ROOT / "tests/test_paid_audit.py"))
    response_fixtures = runpy.run_path(str(ROOT / "tests/test_paid_audit_response.py"))
    get = os.environ.get

    def deny(*args: object, **kwargs: object) -> None:
        raise AssertionError("real key/network forbidden")

    def guarded(key: str, default: object = None) -> object:
        return deny() if key == "OPENAI_API_KEY" else get(key, default)

    summary = []
    with (
        patch("socket.socket", deny),
        patch("socket.getaddrinfo", deny),
        patch.object(os.environ, "get", guarded),
    ):
        for kind in (
            "normal",
            "zero",
            "open-zero",
            "not-sent",
            "http",
            "usage",
            "identity",
            "tier",
            "refusal",
            "incomplete",
            "ready",
            "uncertain",
            "late",
            "timeout",
            "token-limit",
            "uncollected",
        ):
            report = fixtures["make_report"](OUT / ("mock-" + kind), kind)
            write(kind + ".json", report)
            with patch.object(LifeHTTPProvider, "generate", deny):
                result = replay_paid_report(report)
            child = fixtures["child_replay"](report, OUT / ("cli-" + kind + ".json"))
            assert child.returncode == 0, child.stderr
            assert json.loads(child.stdout) == result
            summary.append({"case": kind, "audit": result, "fresh_cli_exit": child.returncode})
        normal = json.loads((OUT / "normal.json").read_text(encoding="utf-8"))
        baseline = json.loads(
            (ROOT / "artifacts/phase4_3/final/normal.json").read_text(encoding="utf-8")
        )
        assert normal["life"] == baseline["life"]
        assert normal["execution"] == baseline["execution"]
        cases = []
        for case in ("tail", "last-call", "all-calls", "zero-accounting", "both-empty"):
            report = copy.deepcopy(normal)
            fixtures["mutate"](report, case)
            with patch.object(LifeHTTPProvider, "generate", deny):
                try:
                    replay_paid_report(report)
                except ValueError as error:
                    reason = str(error)
                else:
                    raise AssertionError("deleted audit passed")
            child = fixtures["child_replay"](report, OUT / ("bad-" + case + ".json"))
            assert child.returncode != 0
            cases.append(
                {
                    "case": case,
                    "rejected": True,
                    "reason": reason,
                    "fresh_cli_exit": child.returncode,
                }
            )
        write("counterexamples-after.json", cases)
        response_cases = []
        for case in ("timeout", "late", "ready-pending", "ready-error"):
            kind = "ready" if case.startswith("ready-") else case
            original = json.loads((OUT / (kind + ".json")).read_text(encoding="utf-8"))
            report = copy.deepcopy(original)
            response_fixtures["remove_response"](report, case)
            for field in ("journal", "audit_snapshot", "usage"):
                assert report["controller"][field] == original["controller"][field]
            with patch.object(LifeHTTPProvider, "generate", deny):
                try:
                    replay_paid_report(report)
                except ValueError as error:
                    reason = str(error)
                else:
                    raise AssertionError("missing response passed")
            child = fixtures["child_replay"](report, OUT / ("bad-" + case + ".json"))
            assert child.returncode != 0
            response_cases.append(
                {
                    "case": case,
                    "reason": reason,
                    "fresh_cli_exit": child.returncode,
                    "journal_snapshot_accounting_unchanged": True,
                }
            )
        write("response-counterexamples-after.json", response_cases)
        write(
            "verification.json",
            {
                "legal_snapshots": summary,
                "normal_life_and_execution_unchanged": True,
                "real_api_calls": 0,
                "real_key_reads": 0,
            },
        )
        write("execution-proposal.json", proposal(str(uuid4())))
    print(
        "five deletion/accounting counterexamples rejected; "
        "four response contradictions rejected; 16 legal snapshots verified in fresh CLI"
    )


if __name__ == "__main__":
    main()
