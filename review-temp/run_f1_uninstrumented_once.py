"""One ordinary pytest child process; metadata/log collection only in the parent."""
import getpass
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

ROOT = Path(r"C:\Users\USER\JourneyMap-Actual")
EVIDENCE = Path(tempfile.mkdtemp(prefix="journeymap-f1-uninstrumented-"))

def write(name, value):
    with (EVIDENCE / name).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")

def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()

def source():
    package = ROOT / "src/journeymap"
    combined = hashlib.sha256()
    files = {}
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package).as_posix()
        content = path.read_bytes()
        normalized = content.replace(b"\r\n", b"\n")
        combined.update(relative.encode())
        combined.update(normalized)
        files[relative] = {"raw_sha256": hashlib.sha256(content).hexdigest(), "normalized_sha256": hashlib.sha256(normalized).hexdigest()}
    return {"source_identity": combined.hexdigest(), "files": files}

def preserved():
    paths = [ROOT / "docs/PHASE4_4_INDEPENDENT_REVIEW.md", ROOT / "scripts/diagnose_checkpoint_view.py"]
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}

command = [str(ROOT / ".venv/Scripts/python.exe"), "-m", "pytest",
           "--junitxml=" + str(EVIDENCE / "junit.xml"),
           "-o", "cache_dir=" + str(EVIDENCE / "pytest-cache"),
           "--basetemp=" + str(EVIDENCE / "pytest-temp")]
start = {
    "started_utc": datetime.now(timezone.utc).isoformat(),
    "account": getpass.getuser(),
    "whoami": subprocess.check_output(["whoami"], text=True).strip(),
    "python": sys.version, "executable": sys.executable, "platform": platform.platform(),
    "versions": {p: importlib.metadata.version(p) for p in ("pytest", "ruff", "mypy", "pluggy")},
    "cwd": str(ROOT), "branch": git("branch", "--show-current"), "head": git("rev-parse", "HEAD"),
    "git_status_before": git("status", "--short"),
    "command": command, "command_line": subprocess.list2cmdline(command),
    "diagnostic_plugin_attached": False,
    "selected_noncredential_environment": {k: os.environ.get(k) for k in ("PYTHONHASHSEED", "PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "PYTHONPATH")},
    "preserved_file_hashes": preserved(),
}
write("metadata-before.json", start)
write("source-before.json", source())
(EVIDENCE / "command.txt").write_text("cwd: " + str(ROOT) + "\n" + start["command_line"] + "\n", encoding="utf-8")
print("EVIDENCE=" + str(EVIDENCE), flush=True)
print("ACCOUNT=" + start["whoami"], flush=True)
print("COMMAND=" + start["command_line"], flush=True)
started = time.monotonic()
with (EVIDENCE / "stdout.txt").open("xb") as stdout, (EVIDENCE / "stderr.txt").open("xb") as stderr:
    result = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr)
write("source-after.json", source())
summary = {"pytest_exit_code": result.returncode, "wall_seconds": time.monotonic() - started,
           "finished_utc": datetime.now(timezone.utc).isoformat(), "head_after": git("rev-parse", "HEAD"),
           "git_status_after": git("status", "--short"), "preserved_file_hashes_after": preserved()}
if (EVIDENCE / "junit.xml").exists():
    xml = ET.parse(EVIDENCE / "junit.xml").getroot()
    summary["junit_suites"] = [dict(s.attrib) for s in xml.iter("testsuite")]
    summary["f1_cases"] = [{"attributes": dict(c.attrib), "failure": [f.text for f in c.findall("failure")]} for c in xml.iter("testcase") if c.get("name") == "test_speed_and_view_polling_preserve_entire_stream"]
write("summary.json", summary)
write("file-hashes.json", {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in EVIDENCE.iterdir() if p.is_file()})
print(json.dumps(summary, ensure_ascii=False), flush=True)
print("EVIDENCE=" + str(EVIDENCE), flush=True)
raise SystemExit(result.returncode)
