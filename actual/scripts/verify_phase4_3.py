"""Offline evidence for the actual paid composition using test fake connections."""

import json
import os
import runpy
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from journeymap.adapters.life_admission import proposal
from journeymap.adapters.life_http import LifeHTTPProvider, PaidFailure
from journeymap.adapters.life_llm import scripted_responses
from journeymap.adapters.life_responses import synthetic_response
from journeymap.experiments.paid_life import PaidLifeSession

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/phase4_3/final"


def write(name: str, value: object) -> None:
    with (OUT / name).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / "normal.json").exists():
        raise FileExistsError("preserve existing evidence")
    fixtures = runpy.run_path(str(ROOT / "tests/test_paid_life.py"))
    fake_response, fake_https, compose = (
        fixtures[k] for k in ("FakeResponse", "FakeHTTPS", "compose")
    )
    original_get = os.environ.get

    def deny(*args: object, **kwargs: object) -> None:
        raise AssertionError("real keys/network forbidden")

    def guarded_get(key: str, default: object = None) -> object:
        return deny() if key == "OPENAI_API_KEY" else original_get(key, default)

    with (
        patch("socket.socket", deny),
        patch("socket.getaddrinfo", deny),
        patch.object(os.environ, "get", guarded_get),
    ):
        fake = fake_https([fake_response(synthetic_response(r.text)) for r in scripted_responses()])
        provider = compose(OUT / "mock-normal", fake)
        session = PaidLifeSession(provider)
        try:
            session.run_until(24)
            with patch.object(LifeHTTPProvider, "generate", deny):
                replay = session.replay_recorded()
            session.finish()
            write(
                "normal.json",
                {
                    "evidence_kind": "synthetic-approval-key-and-fake-HTTPS-only",
                    "real_api_calls": 0,
                    "real_key_reads": 0,
                    "life": session.report(),
                    "controller": session.controller_report(),
                    "replay": replay,
                    "events": len(session.kernel.events),
                    "pending": len(session.kernel.in_flight),
                    "tick": session.kernel.simulation_time,
                    "inputs": [asdict(i) for i in session.inputs],
                    "execution": session.kernel.execution_snapshot,
                    "events_data": [asdict(e) for e in session.kernel.events],
                    "results": [asdict(r) for r in session.kernel.action_results],
                },
            )
        finally:
            session.close()
        errors = []
        for field, value in (("model", "wrong-model"), ("service_tier", "priority")):
            document = json.loads(synthetic_response("{}")) | {field: value}
            p = compose(OUT / ("mock-" + field), fake_https([fake_response(json.dumps(document))]))
            try:
                try:
                    p.generate(fixtures["current_request"]())
                except PaidFailure as failure:
                    errors.append(
                        {
                            "reason": failure.reason,
                            "accounting": p.accounting(),
                            "journal": p.journal.records(),
                        }
                    )
                assert p.budget.cost_nano_usd == 0 and p.budget.reserved_cost == 318432000
            finally:
                p.journal.close()
        write("pricing-uncertainty.json", errors)
        # Reuse the tested deterministic clock/gate path for a concrete journal.
        import threading

        gate, clock = threading.Event(), fixtures["Clock"]()
        response = fake_response(
            synthetic_response('{"action_type":"WAIT","payload":{"duration":1}}'), gate=gate
        )
        p = compose(OUT / "mock-late", fake_https([response]), clock)
        session = PaidLifeSession(p, clock=clock)
        try:
            session.progress(1, 0)
            assert response.entered.wait(2)
            clock.value = 220.001
            session.decider.poll()
            held = p.budget.reserved_cost
            clock.value = 221
            gate.set()
            assert session.decider._signal.wait(2)
            session.decider.poll()
            with patch.object(LifeHTTPProvider, "generate", deny):
                replay = session.replay_recorded()
            session.finish()
            write(
                "late-recovery.json",
                {
                    "reservation_at_timeout": held,
                    "tick": session.kernel.simulation_time,
                    "attempts": len(session.application.attempts),
                    "controller": session.controller_report(),
                    "replay": replay,
                },
            )
        finally:
            gate.set()
            session.close()
        write("execution-proposal.json", proposal(str(uuid4())))
    print("fake HTTPS normal 12/24/171/3, mismatch pricing and late usage recovery verified")


if __name__ == "__main__":
    main()
