"""Independent offline review probes; not part of the reviewed checkout."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from dataclasses import asdict, replace
from unittest.mock import patch

import pytest

ROOT = Path(r"C:\Users\USER\JourneyMap-Actual")
sys.path.insert(0, str(ROOT / "tests"))
from test_paid_life import Clock, FakeHTTPS, FakeResponse, compose, current_request
from test_paid_audit import reseal
from journeymap.adapters.life_admission import source_identity, protocol_identity
from journeymap.adapters.life_http import LifeHTTPProvider, parsed_response
from journeymap.adapters.life_llm import LifeLLMConfig, life_body, life_response_format, raw_record
from journeymap.adapters.life_responses import Budget, LifeExecutionPlan, PreflightStop, Usage, synthetic_response
from journeymap.adapters.llm import parameters_v1
from journeymap.adapters.openai_provider import request_body
from journeymap.examples.life_preflight import run_preflight
from journeymap.experiments.paid_life import PaidLifeSession
from journeymap.experiments.paid_replay import replay_paid_report

BASE = "b7717c2112c076b6f194d26ca0507d13b5b8a4d1"
WAIT = '{"action_type":"WAIT","payload":{"duration":1}}'

@pytest.fixture(autouse=True)
def blocked_external(monkeypatch):
    original = os.environ.get
    def deny(*a, **kw):
        raise AssertionError("Network or real credential access forbidden")
    def guarded(key, default=None):
        return deny() if key == "OPENAI_API_KEY" else original(key, default)
    monkeypatch.setattr(os.environ, "get", guarded)
    monkeypatch.setattr("socket.socket", deny)
    monkeypatch.setattr("socket.getaddrinfo", deny)

def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])

def test_compare_real_base_plan_ast_and_all_unchanged_runtime_files():
    old = ast.parse(git("show", f"{BASE}:src/journeymap/adapters/life_responses.py").decode())
    cls = next(n for n in old.body if isinstance(n, ast.ClassDef) and n.name == "LifeExecutionPlan")
    defaults = {
        n.target.id: (ast.literal_eval(n.value) if not isinstance(n.value, ast.Name) else "gpt-5.4-mini-2026-03-17")
        for n in cls.body if isinstance(n, ast.AnnAssign)
    }
    current = asdict(LifeExecutionPlan())
    differences = {k: (defaults[k], current[k]) for k in current if defaults[k] != current[k]}
    assert differences == {"max_output_tokens": (4096, 8192)}
    changed = set(git("diff", "--name-only", BASE, "HEAD", "--", "src").decode().splitlines())
    assert changed == {
        "src/journeymap/adapters/life_admission.py", "src/journeymap/adapters/life_http.py",
        "src/journeymap/adapters/life_llm.py", "src/journeymap/adapters/life_responses.py",
        "src/journeymap/experiments/paid_replay.py",
    }
    digest = hashlib.sha256()
    paths = git("ls-tree", "-r", "--name-only", BASE, "--", "src/journeymap").decode().splitlines()
    for path in sorted(p for p in paths if p.endswith(".py")):
        digest.update(path.removeprefix("src/journeymap/").encode())
        digest.update(git("show", f"{BASE}:{path}").replace(b"\r\n", b"\n"))
    assert digest.hexdigest() == "b0cd3e00bc45b27bc7e8ffe63bc3481e4ff23d8eb8c61e48ea46fb02f66d5a06"
    assert source_identity() == "f29e9631f80b19d832e83ce7969318bae061a88b543424ed7254499aca91f973"
    assert protocol_identity() == "9dfd86a62f3c45570b86ad016b7a14a5fbbba71257cf8b76efe99b88b8eef970"

@pytest.mark.parametrize("cap", [16, 1024, 4095, 4096, 4097, 8191, 8192])
def test_old_and_new_wire_boundary_matrix(cap):
    request = replace(current_request(), parameters={"max_output_tokens": cap, "reasoning": {"effort": "high"}})
    assert life_body(request)["max_output_tokens"] == cap
    LifeLLMConfig(max_output_tokens=cap)
    if cap <= 4096:
        assert life_body(request) == request_body(request, format_override=life_response_format())
    else:
        with pytest.raises(ValueError):
            parameters_v1(request.parameters)
        with pytest.raises(ValueError):
            request_body(request)

@pytest.mark.parametrize("cap", [0, 15, 8193, 16384, True, False, 8192.0, "8192", None])
def test_invalid_wire_matrix(cap):
    request = replace(current_request(), parameters={"max_output_tokens": cap, "reasoning": {"effort": "high"}})
    with pytest.raises(ValueError):
        life_body(request)
    with pytest.raises(ValueError):
        LifeLLMConfig(max_output_tokens=cap)

@pytest.mark.parametrize("field,exact,reason", [
    ("input_tokens", 100000, "INPUT_TOKEN_LIMIT"),
    ("output_tokens", 122880, "OUTPUT_TOKEN_LIMIT"),
    ("cost_nano_usd", 663136000, "COST_LIMIT"),
    ("attempts", 31, "REQUEST_LIMIT"),
])
def test_exact_and_one_over_budget(field, exact, reason):
    for delta in (0, 1):
        b = Budget(LifeExecutionPlan(), **{field: exact + delta})
        if delta:
            with pytest.raises(PreflightStop, match=reason):
                b.reserve({})
            assert b.reserved_cost == 0
        else:
            b.reserve({})
            assert b.reserved_cost == 336864000

def report_with_usage(tmp_path, output=8192, status="completed", late=False):
    doc = json.loads(synthetic_response(WAIT if status == "completed" else "", status=status))
    doc["usage"].update(output_tokens=output, total_tokens=1000 + output)
    doc["usage"]["output_tokens_details"]["reasoning_tokens"] = output - 1
    gate, clock = threading.Event(), Clock()
    response = FakeResponse(json.dumps(doc), gate=gate if late else None)
    fake = FakeHTTPS([response])
    provider = compose(tmp_path, fake, clock)
    session = PaidLifeSession(provider, clock=clock)
    try:
        if late:
            session.progress(1, 0)
            assert response.entered.wait(2)
            clock.value = 220.001
            session.decider.poll()
            assert provider.budget.reserved_cost == 336864000
            assert session.kernel.simulation_time == 0
            clock.value = 221
            gate.set()
            assert session.decider._signal.wait(2)
            session.decider.poll()
            assert session.decider.state.calls[0].error == "TIMEOUT"
            assert not session.application.attempts
        else:
            try:
                session.run_until(1)
            except ValueError:
                assert status == "incomplete" or output > 8192
        session.finish()
        report = session.export_report()
        assert len(fake.sent) == 1
        with patch.object(LifeHTTPProvider, "generate", side_effect=AssertionError("recall")):
            assert replay_paid_report(report)["audit_verified"] is True
        return report
    finally:
        gate.set()
        session.close()

@pytest.mark.parametrize("output,status", [(8192, "completed"), (8192, "incomplete"), (8193, "completed")])
def test_late_large_usage_accounting(tmp_path, output, status):
    report = report_with_usage(tmp_path, output, status, late=True)
    usage = report["controller"]["usage"]
    if output == 8192:
        assert usage["cost_nano_usd"] == 37479000
        assert usage["reserved_cost"] == 0
        assert usage["output_tokens"] == 8192
    else:
        assert usage["cost_nano_usd"] == 0
        assert usage["reserved_cost"] == 336864000

@pytest.mark.parametrize("mutation", ["double-charge", "old-reservation", "missing-raw", "fractional-output", "over-output"])
def test_resealed_adversarial_records_rejected(tmp_path, mutation):
    report = report_with_usage(tmp_path)
    bad = copy.deepcopy(report)
    c = bad["controller"]
    if mutation == "double-charge":
        extra = 8191 * 4500
        c["usage"]["cost_nano_usd"] += extra
        for row in c["journal"]:
            if row["stage"] == "USAGE_PRICED":
                row["data"]["accounting"]["cost_nano_usd"] += extra
    elif mutation == "old-reservation":
        for row in c["journal"]:
            if row["stage"] == "PREPARED_NOT_SENT":
                row["data"]["accounting"].update(reserved_output=4096, reserved_cost=318432000)
    elif mutation == "missing-raw":
        c["calls"][0]["raw"] = None
    else:
        row = next(r for r in c["journal"] if r["stage"] == "RESPONSE_RECEIVED")
        doc = json.loads(row["data"]["raw"])
        doc["usage"].update(output_tokens=8192.0 if mutation == "fractional-output" else 8193,
                            total_tokens=9192.0 if mutation == "fractional-output" else 9193)
        raw = json.dumps(doc)
        row["data"]["raw"] = raw
        envelope = next(r for r in c["journal"] if r["stage"] == "RESPONSE_ENVELOPE_RECEIVED")
        envelope["data"].update(bytes=len(raw.encode()), sha256=hashlib.sha256(raw.encode()).hexdigest())
        c["calls"][0]["raw"] = asdict(raw_record(parsed_response(doc)))
        if mutation == "over-output":
            checked = next(r for r in c["journal"] if r["stage"] == "USAGE_CHECKED")
            checked["data"]["usage"] = asdict(Usage.parse(doc["usage"]))
    reseal(bad)
    with patch.object(LifeHTTPProvider, "generate", side_effect=AssertionError("recall")):
        expected = {"fractional-output": "INVALID_USAGE", "over-output": "reservation exceeded"}.get(mutation)
        with pytest.raises(ValueError, match=expected):
            replay_paid_report(bad)

def test_guarded_preflight_and_save_evidence():
    result = run_preflight()
    assert (result["requests"], result["tick"], result["events"], result["pending_actions"]) == (12, 24, 171, 3)
    assert {r["body"]["max_output_tokens"] for r in result["wire_records"]} == {8192}
    (Path(__file__).parent / "independent-preflight.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("PREFLIGHT", {k: result[k] for k in ("requests", "tick", "events", "pending_actions", "replay")})
