"""Deletion, accounting and legal open/closed/late snapshot audit regressions."""

import copy
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from test_paid_life import Clock, FakeHTTPS, FakeResponse, compose
from test_paid_life import offline as offline

from journeymap.adapters.life_http import LifeHTTPProvider
from journeymap.adapters.life_llm import scripted_responses
from journeymap.adapters.life_responses import synthetic_response
from journeymap.experiments.paid_life import PaidLifeSession
from journeymap.experiments.paid_replay import replay_paid_report

ROOT = Path(__file__).resolve().parents[1]


def make_report(path: Path, kind: str) -> dict[str, Any]:
    clock, gate = Clock(), threading.Event()
    doc = json.loads(synthetic_response('{"action_type":"WAIT","payload":{"duration":1}}'))
    if kind == "usage":
        doc["usage"] = None
    if kind == "identity":
        doc["model"] = "wrong-model"
    if kind == "tier":
        doc["service_tier"] = "priority"
    if kind == "token-limit":
        doc["usage"]["input_tokens"] = 100001
        doc["usage"]["total_tokens"] = 100101
    raw = json.dumps(doc)
    if kind in {"refusal", "incomplete"}:
        raw = synthetic_response(
            "",
            refusal=kind == "refusal",
            status="incomplete" if kind == "incomplete" else "completed",
        )
    responses = (
        [FakeResponse(synthetic_response(r.text)) for r in scripted_responses()]
        if kind == "normal"
        else [
            FakeResponse(
                raw,
                status=500 if kind == "http" else 200,
                gate=gate if kind in {"uncertain", "late", "timeout"} else None,
            )
        ]
    )
    p = compose(path, FakeHTTPS(list[FakeResponse | Exception](responses)), clock)
    if kind == "not-sent":

        def missing() -> str:
            raise ValueError("synthetic missing credential")

        p._credential = missing
    s = PaidLifeSession(p, clock=clock)
    try:
        if kind in {"zero", "open-zero"}:
            if kind == "zero":
                s.finish()
        elif kind in {"uncertain", "late", "timeout"}:
            s.progress(1, 0)
            assert responses[0].entered.wait(2)
            if kind in {"uncertain", "late"}:
                s.finish()
            else:
                clock.value = 220.001
                s.decider.poll()
            if kind != "uncertain":
                clock.value = 221
                gate.set()
                assert s.decider._signal.wait(2)
                s.decider.poll()
                s.finish()
        elif kind == "uncollected":
            s.progress(1, 0)
            assert s.decider._signal.wait(2)
            s.finish()
        elif kind == "ready":
            s.progress(1, 0)
            assert s.decider._signal.wait(2)
            s.decider.poll()
            s.finish()
        else:
            try:
                s.run_until(24 if kind in {"normal", "token-limit"} else 1)
            except ValueError:
                assert s.decider.state.status == "PAUSED_ERROR"
            s.finish()
        report = s.export_report()
        with patch.object(LifeHTTPProvider, "generate", side_effect=AssertionError("no recall")):
            assert s.replay_recorded()["audit_verified"]
        return report
    finally:
        gate.set()
        if s.decider.in_flight:
            assert s.decider._signal.wait(2)
            s.decider.poll()
        s.close()


def mutate(report: dict[str, Any], case: str) -> None:
    c = report["controller"]
    if case == "tail":
        c["journal"].pop()
    elif case == "last-call":
        c["calls"].pop()
    elif case == "all-calls":
        c["calls"] = []
    elif case == "zero-accounting":
        for key in ("attempts", "input_tokens", "output_tokens", "cost_nano_usd"):
            c["usage"][key] = 0
    elif case == "both-empty":
        c["calls"] = c["journal"] = []
    elif case == "extra-call":
        c["calls"].append(copy.deepcopy(c["calls"][-1]))
    elif case == "duplicate-row":
        c["journal"].append(copy.deepcopy(c["journal"][-2]))
    elif case == "no-receipt":
        c["journal"] = [
            r
            for r in c["journal"]
            if not (r["stage"] == "ACTOR_RECEIPT" and r["data"]["sequence"] == 1)
        ]
    elif case == "no-usage":
        c["journal"] = [
            r
            for r in c["journal"]
            if not (r["stage"] == "USAGE_CHECKED" and r["data"]["sequence"] == 1)
        ]
    elif case == "no-completion":
        c["calls"][0]["final_result"] = None
        c["journal"] = [
            r
            for r in c["journal"]
            if not (r["stage"] == "ACTOR_COMPLETION" and r["data"]["sequence"] == 1)
        ]


def reseal(report: dict[str, Any]) -> None:
    import hashlib

    from journeymap.core.canonical import canonical_json

    c, previous = report["controller"], "0" * 64
    for ordinal, row in enumerate(c["journal"], 1):
        row["ordinal"], row["previous"] = ordinal, previous
        previous = hashlib.sha256(canonical_json(row).encode()).hexdigest()
    c["audit_snapshot"].update(
        entry_count=len(c["journal"]), tail_digest=previous, call_count=len(c["calls"])
    )


CHILD = """import os,socket,sys,json
from journeymap.examples.life_paid import main
from journeymap.adapters.life_http import LifeHTTPProvider
def deny(*a,**k): raise AssertionError("Provider/key/socket/DNS forbidden")
original=os.environ.get
os.environ.get=lambda k,d=None: deny() if k=="OPENAI_API_KEY" else original(k,d)
socket.socket=deny;socket.getaddrinfo=deny;LifeHTTPProvider.generate=deny
sys.argv=["life_paid","--replay",sys.argv[1]]
main()
"""


def child_replay(report: dict[str, Any], path: Path) -> subprocess.CompletedProcess[str]:
    path.write_text(json.dumps(report), encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-c", CHILD, str(path)],
        cwd=ROOT,
        env=dict(os.environ, PYTHONPATH=str(ROOT / "src")),
        text=True,
        capture_output=True,
    )


@pytest.mark.parametrize(
    "case", ["tail", "last-call", "all-calls", "zero-accounting", "both-empty"]
)
def test_review_counterexamples_fail_in_memory_and_cli(tmp_path: Path, case: str) -> None:
    report = make_report(tmp_path / "registry", "normal")
    mutate(report, case)
    with pytest.raises(ValueError):
        replay_paid_report(report)
    child = child_replay(report, tmp_path / "bad.json")
    assert child.returncode != 0 and '"journal_checked": true' not in child.stdout


@pytest.mark.parametrize(
    "case",
    [
        "last-call",
        "both-empty",
        "no-receipt",
        "no-usage",
        "no-completion",
        "extra-call",
        "duplicate-row",
    ],
)
def test_semantics_reject_resealed_missing_extra_duplicate_records(
    tmp_path: Path, case: str
) -> None:
    report = make_report(tmp_path / "registry", "normal")
    mutate(report, case)
    reseal(report)
    with pytest.raises(ValueError):
        replay_paid_report(report)


@pytest.mark.parametrize(
    "kind",
    [
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
    ],
)
def test_valid_snapshot_semantics_and_fresh_cli(tmp_path: Path, kind: str) -> None:
    report = make_report(tmp_path / "registry", kind)
    with patch.object(LifeHTTPProvider, "generate", side_effect=AssertionError("no Provider")):
        result = replay_paid_report(report)
    assert result["audit_verified"] and result["journal_checked"]
    assert result["snapshot_state"] == report["controller"]["audit_snapshot"]["run_status"]
    child = child_replay(report, tmp_path / "valid.json")
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == result
    if kind in {"identity", "tier", "usage", "http", "uncertain", "late"}:
        assert not result["outcome_certain"]


def test_uncertain_cost_cannot_be_relabelled_known(tmp_path: Path) -> None:
    report = make_report(tmp_path / "registry", "identity")
    report["controller"]["usage"]["cost_state"] = "KNOWN_RATE_CARD"
    report["controller"]["usage"]["reserved_cost"] = 0
    with pytest.raises(ValueError, match="accounting summary"):
        replay_paid_report(report)
