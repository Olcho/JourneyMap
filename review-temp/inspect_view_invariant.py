"""Observe snapshot differences without changing any reviewed file."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import zipfile

WORK = Path(__file__).parent
ROOT = Path(r"C:\Users\USER\JourneyMap-Actual")
baseline = "--baseline" in sys.argv
if baseline:
    ROOT = WORK / "frozen-base"
    if not ROOT.exists():
        raw = subprocess.check_output(["git", "-C", r"C:\Users\USER\JourneyMap-Actual", "archive", "--format=zip", "b7717c2112c076b6f194d26ca0507d13b5b8a4d1"])
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for info in archive.infolist():
                dest = (ROOT / info.filename).resolve()
                assert dest.is_relative_to(ROOT.resolve())
            archive.extractall(ROOT)
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

import pytest
from journeymap.core.canonical import canonical_json
from journeymap.experiments.checkpoint import snapshot

def changes(a, b, path="$"):
    if type(a) is not type(b):
        return [(path, a, b)]
    if isinstance(a, dict):
        result = []
        for k in sorted(a.keys() | b.keys()):
            result.extend(changes(a.get(k), b.get(k), f"{path}.{k}"))
        return result
    if isinstance(a, list) and len(a) == len(b):
        return [c for i, (x, y) in enumerate(zip(a, b)) for c in changes(x, y, f"{path}[{i}]")]
    return [] if a == b else [(path, a, b)]

class Probe:
    def pytest_runtest_setup(self, item):
        if item.name != "test_speed_and_view_polling_preserve_entire_stream":
            return
        pending = {}
        def inspect(session):
            value = snapshot(session)
            digest = hashlib.sha256(canonical_json(value).encode()).hexdigest()
            key = id(session)
            if key not in pending:
                pending[key] = value
            else:
                before = pending.pop(key)
                diffs = changes(before, value)
                if diffs:
                    result = {"baseline": baseline, "tick": session.kernel.simulation_time, "differences": diffs}
                    (WORK / ("baseline-view-diff.json" if baseline else "current-view-diff.json")).write_text(json.dumps(result, indent=2), encoding="utf-8")
                    print("SNAPSHOT_DIFFERENCE", json.dumps(result)[:5000], flush=True)
            return digest
        item.module.checkpoint_digest = inspect

print("INSPECTED_SOURCE", ROOT, flush=True)
raise SystemExit(pytest.main([
    str(ROOT / "tests/test_life_checkpoint.py") + "::test_speed_and_view_polling_preserve_entire_stream",
    "-q", "-s", "--tb=short", "-o", "cache_dir=" + str(WORK / "pytest-cache"),
    "--basetemp=" + str(WORK / ("baseline-view-data" if baseline else "current-view-data")),
], plugins=[Probe()]))
