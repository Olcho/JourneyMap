"""Normal rule-session digest reference, not a replay of either failed snapshot."""
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

from journeymap.adapters.life_admission import source_identity
from journeymap.core.canonical import canonical_json
from journeymap.experiments.checkpoint import checkpoint_digest, snapshot
from journeymap.experiments.life import LifeSession
from journeymap.scenarios.life import LifeConfig

directory = Path(sys.argv[1]) / "normal-tick16-reference"
directory.mkdir(exist_ok=False)
before = "b29a617885e779681f138447952f8a5a11482e4a43970602d6d1c030dfdc1a7f"
after = "e257e1e1526dceceade3ab42f6206d80f25557f01946471b6af5c5a5682c5401"

def write(name, value):
    with (directory / name).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")

def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()

records = []
session = LifeSession(LifeConfig())
try:
    for tick in range(0, 18):
        if tick:
            session.run_until(tick)
        value = snapshot(session)
        computed = digest(value)
        assert computed == checkpoint_digest(session)
        record = {"tick": tick, "digest": computed, "matches_original_before": computed == before,
                  "matches_original_after": computed == after, "source_sha256": value["compatibility"]["source_sha256"],
                  "world_digest": session.kernel.state_digest,
                  "events": len(session.kernel.events), "inputs": len(session.inputs),
                  "observations": len(session.observations), "attempts": len(session.application.attempts),
                  "in_flight": [f.request.actor_id + ":" + f.request.action_type for f in session.kernel.in_flight],
                  "top_level_hashes": {k: digest(v) for k, v in value.items()}}
        records.append(record)
        if 15 <= tick <= 17:
            write(f"tick-{tick}-payload.json", value)
            print(json.dumps(record), flush=True)
finally:
    session.close()
write("normal-reference.json", {"platform": platform.platform(), "python": sys.version,
      "executable": sys.executable, "source_identity": source_identity(),
      "sequence": "LifeSession(LifeConfig()); run_until(1), ..., run_until(17), no UI view",
      "reported_linux_hash": after, "linux_execution_independently_repeated": False,
      "ticks": records})
# Availability check only; never install WSL or query online distributions.
wsl = subprocess.run(["wsl.exe", "--list"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
write("linux-availability.json", {"command": ["wsl.exe", "--list"], "returncode": wsl.returncode,
      "stdout": wsl.stdout.decode("utf-16-le", errors="replace"),
      "stderr": wsl.stderr.decode("utf-16-le", errors="replace")})
print("NORMAL_REFERENCE=" + str(directory), flush=True)
