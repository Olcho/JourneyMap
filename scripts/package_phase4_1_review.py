"""Build a self-contained review ZIP and reconstruct HEAD + patch + new files."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "phase4_1"
ZIP_PATH = OUT / "JourneyMap_Phase4_1_review_2026-10-15.zip"


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def source_path(name: str) -> bool:
    path = Path(name)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and (
            path.parts[0] in {"src", "tests", "docs", ".github", "scripts"}
            or name in {"README.md", "AGENTS.md", "pyproject.toml", ".gitignore"}
        )
        and path.suffix not in {".pyc", ".key", ".pem"}
    )


def info(data: bytes) -> dict[str, int | str]:
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def main() -> None:
    if ZIP_PATH.exists():
        raise FileExistsError("review ZIP already exists; preserve it and choose a new version")
    names = git("ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0")
    source = {
        n: (ROOT / n).read_bytes()
        for n in sorted(set(names))
        if n and source_path(n) and (ROOT / n).is_file()
    }
    with zipfile.ZipFile(OUT / "phase4_1-start-source.zip") as start_zip:
        before = {name: info(start_zip.read(name)) for name in start_zip.namelist()}
    head = git("rev-parse", "HEAD").decode().strip()
    patch = git("diff", "--binary", "HEAD")
    changes = {
        name: {"before": before.get(name), "after": info(source[name]) if name in source else None}
        for name in sorted(set(before) | set(source))
        if before.get(name) != (info(source[name]) if name in source else None)
    }
    (OUT / "phase4_1-changes.json").write_text(json.dumps(changes, indent=2), encoding="utf-8")
    (OUT / "head-to-final.patch").write_bytes(patch)
    reconstructed = Path(tempfile.mkdtemp(prefix="reconstructed-", dir=OUT))
    if not reconstructed.resolve().is_relative_to(OUT.resolve()):
        raise ValueError("reconstruction outside approved output directory")
    archive = git("archive", "--format=zip", "HEAD")
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        for member in z.infolist():
            target = reconstructed / member.filename
            if not target.resolve().is_relative_to(reconstructed.resolve()):
                raise ValueError("invalid archived path")
        z.extractall(reconstructed)
    prefix = reconstructed.relative_to(ROOT).as_posix()
    for extra in (["--check"], []):
        subprocess.run(
            ["git", "apply", *extra, "--unsafe-paths", f"--directory={prefix}"],
            cwd=ROOT,
            input=patch,
            check=True,
            capture_output=True,
        )
    tracked = set(git("ls-tree", "-r", "--name-only", "-z", "HEAD").decode().split("\0"))
    restored_line_endings = []
    for name, expected in source.items():
        path = reconstructed / name
        if name not in tracked:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)
            continue
        actual = path.read_bytes()
        if b"\r\n" in expected and expected.count(b"\n") == expected.count(b"\r\n"):
            actual = actual.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        elif b"\r\n" not in expected:
            actual = actual.replace(b"\r\n", b"\n")
        if actual != path.read_bytes():
            restored_line_endings.append(name)
            path.write_bytes(actual)
    mismatches = [n for n, data in source.items() if (reconstructed / n).read_bytes() != data]
    actual_names = {
        p.relative_to(reconstructed).as_posix()
        for p in reconstructed.rglob("*")
        if p.is_file() and source_path(p.relative_to(reconstructed).as_posix())
    }
    if mismatches or actual_names != set(source):
        raise AssertionError(
            {"mismatches": mismatches, "path_difference": sorted(actual_names ^ set(source))}
        )
    reconstruction = {
        "base_head": head,
        "source_files": len(source),
        "byte_identical": True,
        "mismatches": [],
        "source_tree": str(reconstructed),
        "method": (
            "git archive HEAD + git apply tracked diff + new sources + declared LF/CRLF restoration"
        ),
        "line_endings_restored": restored_line_endings,
    }
    (OUT / "reconstruction.json").write_text(json.dumps(reconstruction, indent=2), encoding="utf-8")
    child = subprocess.run(
        [
            sys.executable,
            "-m",
            "journeymap.examples.life_mixed",
            "--resume",
            str(OUT / "final/mixed-work.json"),
            "--save",
            str(OUT / "reconstructed-mixed-final.json"),
        ],
        cwd=reconstructed,
        env=dict(os.environ, PYTHONPATH=str(reconstructed / "src")),
        capture_output=True,
        text=True,
        check=True,
    )
    child_report = json.loads(child.stdout)
    expected_digest = json.loads((OUT / "final/mixed-final.json").read_text(encoding="utf-8"))[
        "sha256"
    ]
    if child_report["checkpoint_digest"] != expected_digest:
        raise AssertionError("reconstructed-source resume differs from continuous run")
    (OUT / "reconstruction-resume.json").write_text(
        json.dumps(
            {
                "source_tree": str(reconstructed),
                "PYTHONPATH": str(reconstructed / "src"),
                "checkpoint": "final/mixed-work.json",
                "equal": True,
                "child": child_report,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    payload = {f"source/{n}": data for n, data in source.items()}
    payload["HEAD_TO_FINAL.patch"] = patch
    payload["PHASE4_1_CHANGES.json"] = json.dumps(changes, indent=2).encode()
    for name in (
        "phase4_1-start-source.zip",
        "start-manifest.json",
        "start-status.txt",
        "independent-review.md",
        "reconstruction.json",
        "reconstruction-resume.json",
        "reconstructed-mixed-final.json",
        "protection-summary.json",
        "pytest-final.log",
        "full-final.xml",
        "errors-final.log",
        "errors-final.xml",
        "ruff-check.log",
        "ruff-format.log",
        "mypy.log",
        "diff-check.log",
        "scenarios.log",
        "boundaries.log",
        "verification-summary.json",
        "f1-before.json",
        "f1-after.json",
        "f2-before.json",
        "f2-before-repro.py",
        "f2-after.json",
        "map-verification.json",
        "map-inputs-utf8.json",
        "map-qa-utf8.cjs",
        "map-both-open.svg",
        "map-one-closed.svg",
        "map-both-closed.svg",
        "ui-verification.json",
        "ui-error-restored.jpg",
        "ui-error-checkpoint.json",
    ):
        path = OUT / name
        if not path.is_file():
            raise FileNotFoundError(path)
        payload[f"evidence/{name}"] = path.read_bytes()
    for path in sorted((OUT / "final").rglob("*")):
        if path.is_file() and path.suffix in {".json", ".jsonl", ".log"}:
            payload["evidence/final/" + path.relative_to(OUT / "final").as_posix()] = (
                path.read_bytes()
            )
    payload["README_REVIEW.md"] = f"""# JourneyMap Phase 4.1 error-boundary review

Base HEAD: {head}; branch codex/phase1-concurrent-runtime.
Phase 4.1 continues the uncommitted Phase 1+2+3+4 state (191 source files), not HEAD alone.
source/ contains the complete final source/test/config/doc tree ({len(source)} files).
It includes HTML. HEAD_TO_FINAL.patch and new files reconstruct the complete source.
reconstruction.json verifies bytes.
PHASE4_1_CHANGES.json identifies Phase-4.1-only changes against phase4_1-start-source.zip.

Set PYTHONPATH to source/src and use Python 3.12. Run:

    python -m journeymap.examples.life_ui
    python -m journeymap.examples.life_mixed --until 5 --save work.json
    python -m journeymap.examples.life_mixed --resume work.json --save resumed.json
    python -m pytest -q --basetemp=../review-test-temp
    python -m ruff check .
    python -m ruff format --check .
    python -m mypy

Open http://127.0.0.1:8765 after launching the local UI. It starts paused.
Default is all RULE. Add --controller offline for Ada literal response fixture mode.
This validates offline wiring, not actual LLM behavior.
The checkpoint is direct data hydration, not replay.
Source compatibility is checked after LF normalization.
Six scenario outputs, fresh-process checkpoints, JUnit and UI evidence are under evidence/.
MANIFEST.json lists size/SHA-256 of every payload except itself; no self-hash or signature claim.
No venv/cache/Git internals/API credentials/E03/E04 raw originals are included.
No LLM/API calls or commit/push/merge.
Local loopback UI traffic is distinct from external API activity.
Read source/docs/PHASE4_1_ERROR_BOUNDARIES.md and PHASE4_1_REPORT_2026-10-15.md.
F1/F2 are corrected; exactly-deadline completion is timely, later completion is discarded.
verification-summary.json indexes successful evidence. boundaries.log preserves an initial
Windows stdout decoding failure in the QA helper; map-verification.json/SVG are the
successful rerun with explicit UTF-8. The full pytest suite passed independently.
Helper scripts comparing old phase2/phase4 artifacts require those baseline artifacts
from the original workspace; this review package contains their comparison results.
""".encode()
    manifest = {name: info(data) for name, data in sorted(payload.items())}
    with zipfile.ZipFile(ZIP_PATH, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in sorted(payload.items()):
            z.writestr(name, data)
        z.writestr("MANIFEST.json", json.dumps(manifest, indent=2))
    with zipfile.ZipFile(ZIP_PATH) as z:
        assert set(z.namelist()) == set(manifest) | {"MANIFEST.json"}
        for name, expected in manifest.items():
            assert info(z.read(name)) == expected
    digest = hashlib.sha256(ZIP_PATH.read_bytes()).hexdigest()
    ZIP_PATH.with_suffix(".zip.sha256").write_text(
        digest + "  " + ZIP_PATH.name + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "zip": str(ZIP_PATH),
                "bytes": ZIP_PATH.stat().st_size,
                "payload_files": len(manifest),
                "sha256": digest,
                "reconstruction": reconstruction,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
