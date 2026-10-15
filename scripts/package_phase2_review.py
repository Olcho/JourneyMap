"""Build a self-contained review ZIP and reconstruct HEAD + patch + new files."""

import hashlib
import io
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "phase2"
ZIP_PATH = OUT / "JourneyMap_Phase2_review_2026-10-14.zip"


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
    before = json.loads((OUT / "phase2-start-files.json").read_text(encoding="utf-8"))
    head = git("rev-parse", "HEAD").decode().strip()
    patch = git("diff", "--binary", "HEAD")
    changes = {
        name: {"before": before.get(name), "after": info(source[name]) if name in source else None}
        for name in sorted(set(before) | set(source))
        if before.get(name) != (info(source[name]) if name in source else None)
    }
    (OUT / "phase2-changes.json").write_text(json.dumps(changes, indent=2), encoding="utf-8")
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
    payload = {f"source/{n}": data for n, data in source.items()}
    payload["HEAD_TO_FINAL.patch"] = patch
    payload["PHASE2_CHANGES.json"] = json.dumps(changes, indent=2).encode()
    for name in (
        "phase2-start-source.zip",
        "phase2-start-files.json",
        "phase2-start-status.txt",
        "phase2-start-tracked.patch",
        "reconstruction.json",
        "verification.json",
        "protection-summary.json",
        "pytest-final.log",
        "full-final.xml",
        "pytest-targeted.log",
        "targeted-final.xml",
        "ruff-check.log",
        "ruff-format.log",
        "mypy.log",
        "diff-check.log",
        "phase1-start.xml",
        "phase1-start-scenes.json",
        "phase1-preservation.json",
        "changed-files.json",
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
    payload["evidence/prototype-7-day-summary.json"] = (
        OUT / "normal-initial" / "summary.json"
    ).read_bytes()
    payload["README_REVIEW.md"] = f"""# JourneyMap Phase 2 review

Base HEAD: {head}; branch codex/phase1-concurrent-runtime.
Phase 2 continues the uncommitted Phase 1 state (23 changed files), not HEAD alone.
No commit/push/merge, LLM/API calls or key reads were performed.

`source/` is the complete final source/test/config/document tree ({len(source)} files).
`HEAD_TO_FINAL.patch` contains tracked Phase 1 + Phase 2 changes. New files are in source/.
`evidence/phase2-start-source.zip` preserves all 155 pre-Phase-2 files,
including untracked Phase 1 files.
`PHASE2_CHANGES.json` identifies Phase-2-only before/after byte hashes.
`evidence/reconstruction.json` confirms reconstruction from HEAD archive,
tracked patch and new files.
Git checkout LF/CRLF differences were restored explicitly before byte comparison.

Extract, enter source/, set PYTHONPATH to its src directory.
Use Python 3.12 with pytest/Ruff/mypy:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m journeymap.examples.life --verify-replay
python -m pytest -q --basetemp=../review-test-temp
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

Normal and shortage outputs and JUnit/static evidence are under evidence/.
No virtualenv, cache, .git internals, private credentials, or E03/E04 raw originals are included.
MANIFEST.json lists size/SHA-256 of every payload file except itself (no self-hash claim).
Save/restore and UI are not implemented.
Read source/docs/PHASE2_LIFE_LOOP.md and PHASE2_REPORT_2026-10-14.md.
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
