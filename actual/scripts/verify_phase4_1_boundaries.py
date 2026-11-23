"""Offline before/after evidence, mixed regression and unchanged map JavaScript."""

import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from journeymap.adapters.life_llm import LifeLLMConfig, OfflineLifeProvider
from journeymap.adapters.provider import RawModelResponse
from journeymap.examples.life_mixed import main as mixed_main
from journeymap.experiments.checkpoint import checkpoint_digest, load_checkpoint, save_checkpoint
from journeymap.experiments.life import LifeSession
from journeymap.experiments.live import LiveLife
from journeymap.experiments.mixed_life import MixedLifeSession
from journeymap.scenarios.life import LifeConfig

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/phase4_1"


class Clock:
    def __init__(self) -> None:
        self.value = 100.0
        self.lock = threading.Lock()

    def __call__(self) -> float:
        with self.lock:
            return self.value

    def set(self, value: float) -> None:
        with self.lock:
            assert value >= self.value
            self.value = value


def write(name: str, value: object) -> None:
    path = OUT / name
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2)


def boundary_evidence() -> None:
    text = "[" * 1500 + "0" + "]" * 1500
    session = MixedLifeSession(
        LifeConfig(horizon=24), LifeLLMConfig(responses=(RawModelResponse(text),))
    )
    try:
        try:
            session.run_until(24)
        except ValueError as error:
            assert str(error) == "PAUSED_ERROR"
        call = session.decider.state.calls[-1]
        assert call.raw and call.raw.data["text"] == text
        assert call.error == "JSON_DEPTH_EXCEEDED"
        save_checkpoint(session, OUT / "final/f1-error.json")
        restored = load_checkpoint(OUT / "final/f1-error.json")
        assert isinstance(restored, MixedLifeSession)
        try:
            with patch.object(
                OfflineLifeProvider, "generate", side_effect=AssertionError("no recall")
            ):
                replay = restored.replay_recorded()
            write(
                "f1-after.json",
                {
                    "status": session.decider.state.status,
                    "call_status": call.status,
                    "error": call.error,
                    "raw_bytes": len(text.encode()),
                    "raw_preserved": True,
                    "ready_missing": session.decider.state.ready is None,
                    "observation_missing": session.decider.state.observation is None,
                    "tick": session.kernel.simulation_time,
                    "attempts": len(session.application.attempts),
                    "requests": session.decider.state.requests_started,
                    "replay": replay,
                    "restored_equal": checkpoint_digest(restored) == checkpoint_digest(session),
                },
            )
        finally:
            restored.close()
    finally:
        session.close()
    cases = []
    for completed in (104.999, 105.0, 105.001):
        clock = Clock()
        session = MixedLifeSession(
            LifeConfig(horizon=24), LifeLLMConfig(hold_responses=True), decision_clock=clock
        )
        try:
            session.progress(1, 0)
            clock.set(completed)
            session.decider.release_response()
            assert session.decider._signal.wait(1)
            clock.set(110)
            session.decider.poll()
            status = session.decider.state.status
            assert status == ("READY" if completed <= 105 else "PAUSED_ERROR")
            session.progress(1, 0)
            call = session.decider.state.calls[0]
            cases.append(
                {
                    "started_at": session.decider._started_at,
                    "completed_at": session.decider._completed_at,
                    "polled_at": 110,
                    "deadline": 105,
                    "status_after_poll": status,
                    "error": call.error,
                    "discarded_late": call.discarded_late,
                    "tick": session.kernel.simulation_time,
                    "requests": session.decider.state.requests_started,
                    "raw_preserved": call.raw is not None,
                }
            )
            if completed > 105:
                save_checkpoint(session, OUT / "final/f2-timeout.json")
                assert session.replay_recorded()["provider_recalled"] is False
        finally:
            session.close()
    write("f2-after.json", cases)


def mixed_regression() -> None:
    # Use the public CLI path for actual export, candidate/receipt recording and replay.
    with patch.object(
        sys,
        "argv",
        [
            "life_mixed",
            "--output",
            str(OUT / "final/mixed"),
            "--save",
            str(OUT / "final/mixed-final.json"),
        ],
    ):
        mixed_main()
    comparisons = []
    for path in sorted((OUT / "final/mixed").iterdir()):
        baseline = ROOT / "artifacts/phase4/final/mixed" / path.name
        if path.suffix == ".jsonl":
            left = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
            right = [json.loads(x) for x in baseline.read_text(encoding="utf-8").splitlines()]
        else:
            left = json.loads(path.read_text(encoding="utf-8"))
            right = json.loads(baseline.read_text(encoding="utf-8"))
            if path.name == "provider.json":
                left.pop("checkpoint_digest")
                right.pop("checkpoint_digest")  # Source compatibility hash intentionally changed.
        assert left == right, path.name
        comparisons.append(path.name)
    expected = json.loads((OUT / "final/mixed-final.json").read_text(encoding="utf-8"))["sha256"]
    resumed = []
    for tick, kind in ((2, "MOVE"), (5, "WORK"), (13, "REST")):
        session = MixedLifeSession(LifeConfig(horizon=24))
        source, destination = (
            OUT / f"final/mixed-{kind.lower()}.json",
            OUT / f"final/resumed-{kind.lower()}.json",
        )
        try:
            session.run_until(tick)
            assert any(
                f.request.actor_id == "ada" and f.request.action_type == kind
                for f in session.kernel.in_flight
            )
            save_checkpoint(session, source)
        finally:
            session.close()
        child = subprocess.run(
            [
                sys.executable,
                "-m",
                "journeymap.examples.life_mixed",
                "--resume",
                str(source),
                "--save",
                str(destination),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        report = json.loads(child.stdout)
        assert report["checkpoint_digest"] == expected
        resumed.append({"saved_at": tick, "kind": kind, "child": report, "equal": True})
    write(
        "final/mixed-verification.json",
        {
            "unchanged_phase4_files": comparisons,
            "excluded": "provider.checkpoint_digest (changed source hash)",
            "fresh_process": resumed,
        },
    )


def map_regression() -> None:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("Node is required only for this explicit map JS QA helper")
    session = LifeSession(LifeConfig(closure_tick=2))
    try:
        live = LiveLife(session, OUT / "unused.json")
        opened = live.view()
        session.run_until(2)
        one_closed = live.view()
        both_closed = json.loads(json.dumps(one_closed))
        both_closed["movement"]["routes"]["shop-square"]["passable"] = (
            False  # Display-only QA copy.
        )
        data = [
            {"name": name, "state": state}
            for name, state in (
                ("both-open", opened),
                ("one-closed", one_closed),
                ("both-closed", both_closed),
            )
        ]
        write("map-inputs-utf8.json", data)
    finally:
        session.close()
    js = """
const fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(process.argv[2],'utf8');
const start=html.indexOf('function drawMap(s)'),end=html.indexOf('\\nrefresh();',start);
if(start<0||end<start)throw Error('renderer not found');
const entries=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const output=entries.map(entry=>{
 let map={innerHTML:''};
 let context={data:entry.state,$:()=>map,esc:x=>String(x??''),
  names:{ada:'Ada',bert:'Bert',cora:'Cora',dara:'Dara',employer:'Employer'},
  places:{homes:'Homes',square:'Square',detour:'Detour',workshop:'Workshop'}};
 vm.runInNewContext(html.slice(start,end)+'\\ndrawMap(data);',context,{timeout:1000});
 return {name:entry.name,svg:map.innerHTML};
});
process.stdout.write(JSON.stringify(output));
"""
    script = OUT / "map-qa-utf8.cjs"
    script.write_text(js, encoding="utf-8")
    result = subprocess.run(
        [
            node,
            str(script),
            str(ROOT / "src/journeymap/examples/life_ui.html"),
            str(OUT / "map-inputs-utf8.json"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    checks = []
    for index, row in enumerate(json.loads(result.stdout)):
        root = ElementTree.fromstring("<svg>" + row["svg"] + "</svg>")
        arrows = {e.attrib["data-route"]: e.attrib for e in root.findall("line")}
        assert len(arrows) == 8
        forward, reverse = (("true", "true"), ("false", "true"), ("false", "false"))[index]
        assert arrows["square-shop"]["data-passable"] == forward
        assert arrows["shop-square"]["data-passable"] == reverse
        for arrow in arrows.values():
            expected = "open" if arrow["data-passable"] == "true" else "closed"
            assert arrow["marker-end"] == f"url(#{expected}-arrow)"
        (OUT / f"map-{row['name']}.svg").write_text(
            "<svg>" + row["svg"] + "</svg>", encoding="utf-8"
        )
        checks.append(
            {"case": row["name"], "arrows": 8, "square-shop": forward, "shop-square": reverse}
        )
    write("map-verification.json", checks)


def main() -> None:
    (OUT / "final").mkdir(exist_ok=True)
    boundary_evidence()
    mixed_regression()
    map_regression()
    print("F1/F2, mixed records/restores and actual map JS verified", flush=True)


if __name__ == "__main__":
    main()
