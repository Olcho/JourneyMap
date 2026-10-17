"""Run the existing pytest suite and retain F1 snapshots without changing its assertions.

Usage: python scripts/diagnose_checkpoint_view.py [pytest arguments]
The evidence directory is always new. No credentials or network calls are needed.
"""

import hashlib
import json
import platform
import sys
import tempfile
from collections import deque
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

TARGET = "test_speed_and_view_polling_preserve_entire_stream"


def differences(before: Any, after: Any, path: str = "$") -> Generator[str, None, None]:
    """Locate differences without dropping ordering or type information."""
    if type(before) is not type(after):
        yield path
    elif isinstance(before, dict):
        for key in sorted(before.keys() | after.keys()):
            child = f"{path}[{key!r}]"
            if key not in before or key not in after:
                yield child
            else:
                yield from differences(before[key], after[key], child)
    elif isinstance(before, list):
        if len(before) != len(after):
            yield f"{path}.length"
        for index, (left, right) in enumerate(zip(before, after, strict=False)):
            yield from differences(left, right, f"{path}[{index}]")
    elif before != after:
        yield path


def write_json(path: Path, data: Any) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, allow_nan=False, indent=2)
        stream.write("\n")


class Capture:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.summary: dict[str, Any] = {
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "pytest": pytest.__version__,
            "cwd": str(Path.cwd()),
            "target": TARGET,
            "target_outcome": "NOT_RUN",
        }
        self.samples: deque[dict[str, Any]] = deque(maxlen=2)
        self.count = 0

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item: pytest.Item) -> Generator[None, Any, None]:
        if item.name != TARGET or item.path.name != "test_life_checkpoint.py":
            yield
            return
        import journeymap.experiments.checkpoint as checkpoint

        original = checkpoint.snapshot
        self.summary["checkpoint_module"] = str(Path(checkpoint.__file__).resolve())
        self.summary["nodeid"] = item.nodeid

        def capture(session: Any) -> dict[str, Any]:
            # Call exactly once; return the same detached payload to the real digest.
            # Do not resnapshot, replace checkpoint_digest, or suppress an assertion.
            payload = original(session)
            self.count += 1
            self.samples.append(
                {"call": self.count, "tick": session.kernel.simulation_time, "payload": payload}
            )
            return payload

        checkpoint.snapshot = capture
        try:
            yield
        finally:
            checkpoint.snapshot = original

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.nodeid.split("::")[-1] != TARGET or report.when != "call":
            return
        self.summary["target_outcome"] = report.outcome
        self.summary["snapshot_calls"] = self.count
        if not report.failed:
            return
        import journeymap.experiments.checkpoint as checkpoint

        for index, sample in enumerate(self.samples):
            write_json(self.directory / f"snapshot-{index}.json", sample)
        if len(self.samples) == 2:
            before, after = self.samples
            self.summary["changed_paths"] = list(differences(before["payload"], after["payload"]))
            self.summary["digests"] = [checkpoint._digest(s["payload"]) for s in self.samples]
        (self.directory / "failure.txt").write_text(str(report.longrepr), encoding="utf-8")
        # Read after the failure. This inventory is evidence at that later instant,
        # not a claim about files present at either original snapshot boundary.
        package = Path(checkpoint.__file__).resolve().parents[1]
        inventory: dict[str, Any] = {}
        try:
            for path in sorted(package.rglob("*.py")):
                content = path.read_bytes().replace(b"\r\n", b"\n")
                inventory[path.relative_to(package).as_posix()] = hashlib.sha256(
                    content
                ).hexdigest()
        except OSError as error:
            inventory["inventory_error"] = str(error)
        write_json(self.directory / "source-inventory-after-failure.json", inventory)


def main() -> int:
    directory = Path(tempfile.mkdtemp(prefix="journeymap-checkpoint-probe-"))
    print(f"Checkpoint diagnostic evidence: {directory}", flush=True)
    plugin = Capture(directory)
    # pytest retains its normal collection, fixtures, assertions and exit code.
    arguments = sys.argv[1:] or ["-q"]
    result = pytest.main(arguments, plugins=[plugin])
    plugin.summary["pytest_exit_code"] = int(result)
    write_json(directory / "summary.json", plugin.summary)
    print(f"Checkpoint diagnostic evidence: {directory}", flush=True)
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
