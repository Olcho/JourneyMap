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
OUT = ROOT / "artifacts" / "phase4_3_1"
ZIP_PATH = OUT / "JourneyMap_Phase4_3_1_review_2026-10-15.zip"


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
    with zipfile.ZipFile(OUT / "phase4_3_1-start-source.zip") as start_zip:
        before = {name: info(start_zip.read(name)) for name in start_zip.namelist()}
    head = git("rev-parse", "HEAD").decode().strip()
    patch = git("diff", "--binary", "HEAD")
    changes = {
        name: {"before": before.get(name), "after": info(source[name]) if name in source else None}
        for name in sorted(set(before) | set(source))
        if before.get(name) != (info(source[name]) if name in source else None)
    }
    (OUT / "phase4_3_1-changes.json").write_text(json.dumps(changes, indent=2), encoding="utf-8")
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
    code = """import json,os,socket,sys
from pathlib import Path
import journeymap
from journeymap.examples.life_paid import main
from journeymap.adapters.life_http import LifeHTTPProvider
assert Path(journeymap.__file__).resolve().is_relative_to(Path(sys.argv[2]).resolve())
def deny(*args,**kwargs): raise AssertionError("no Provider/key/network in replay")
original=os.environ.get
os.environ.get=lambda k,d=None: deny() if k=="OPENAI_API_KEY" else original(k,d)
socket.socket=deny; socket.getaddrinfo=deny; LifeHTTPProvider.generate=deny
sys.argv=["life_paid","--replay",sys.argv[1]]
main()
"""
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(OUT / "final/normal.json"),
            str(reconstructed / "src"),
        ],
        cwd=reconstructed,
        env=dict(os.environ, PYTHONPATH=str(reconstructed / "src")),
        capture_output=True,
        text=True,
        check=True,
    )
    actual = json.loads(child.stdout)
    if not (
        actual["provider_recalled"] is False
        and actual["engine_equal"]
        and actual["audit_verified"]
        and actual["snapshot_state"] == "FINISHED"
        and actual["calls_checked"] == 12
    ):
        raise AssertionError("reconstructed-source audit mismatch")
    rejected = subprocess.run(
        [
            sys.executable,
            "-c",
            code,
            str(OUT / "final/bad-both-empty.json"),
            str(reconstructed / "src"),
        ],
        cwd=reconstructed,
        env=dict(os.environ, PYTHONPATH=str(reconstructed / "src")),
        capture_output=True,
        text=True,
    )
    if rejected.returncode == 0 or '"journal_checked": true' in rejected.stdout:
        raise AssertionError("reconstructed source accepted deleted audit")
    (OUT / "reconstruction-check.py").write_text(code, encoding="utf-8")
    (OUT / "reconstruction-replay.json").write_text(
        json.dumps(
            {
                "source_tree": str(reconstructed),
                "Provider_key_socket_DNS_blocked": True,
                "normal": actual,
                "deleted_calls_journal_exit": rejected.returncode,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    payload = {f"source/{n}": data for n, data in source.items()}
    payload["HEAD_TO_FINAL.patch"] = patch
    payload["PHASE4_3_1_CHANGES.json"] = json.dumps(changes, indent=2).encode()
    for name in (
        "phase4_3_1-start-source.zip",
        "start-manifest.json",
        "start-status.txt",
        "independent-review.md",
        "reconstruction.json",
        "reconstruction-replay.json",
        "reconstruction-check.py",
        "preservation.json",
        "pytest-final.log",
        "full-final.xml",
        "targeted-final.log",
        "targeted-final.xml",
        "ruff-check.log",
        "ruff-format.log",
        "mypy.log",
        "diff-check.log",
        "verification-final.log",
        "counterexamples-before-guarded.json",
        "before-check.py",
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
    payload["README_REVIEW.md"] = f"""# JourneyMap Phase4.3.1 audit completeness review

Base HEAD: {head}; branch codex/phase1-concurrent-runtime.
Continues uncommitted Phase1-4.3 (213 starting files), not HEAD alone.
source/ contains {len(source)} full source/test/config/doc files including HTML.
HEAD_TO_FINAL.patch plus new files reconstructs the tree; reconstruction.json verifies bytes.
PHASE4_3_1_CHANGES.json identifies this stage against phase4_3_1-start-source.zip.

Use Python3.12, set PYTHONPATH to source/src, work from source/:

    python -m journeymap.examples.life_paid
    python -m journeymap.examples.life_paid --replay ../evidence/final/normal.json
    python -m journeymap.examples.life_paid --replay ../evidence/final/bad-both-empty.json
    python -m pytest -q --basetemp=../review-test-temp
    python -m ruff check .
    python -m ruff format --check .
    python -m mypy

The bad report command MUST fail. All examples use synthetic approval/key/fake HTTPS.
No real key/API/paid execution or valid approval file was used or created.
Default CLI only prints an unauthorized proposal. DO NOT execute the paid command
without independent re-review and separate explicit human approval.
Read source/docs/PHASE4_3_1_AUDIT.md and PHASE4_3_1_REPORT_2026-10-15.md.
Report schema life-paid-report-2 requires a declared snapshot. Memory/file replay share
complete lifecycle, bidirectional Ada/call/journal/engine coverage and recomputed accounting.
Valid OPEN/UNCERTAIN snapshots are not promoted to cleanly finished outcomes.
The five original deletions/accounting changes fail; 15 legitimate lifecycle cases pass.
Reconstruction checks normal success and deleted audit rejection
with Provider/key/socket/DNS blocked.
This is not invoice authentication, a signature or protection from complete operator rewriting.
No recovery/migration/live checkpoint or additional domain/UI work was added.
Helper scripts need dev pytest; old-artifact comparison needs original workspace baseline.
MANIFEST.json lists payload size/SHA-256 except itself; no signature claim.
No registry DB, credentials, valid approval, venv/cache/Git or old research raw originals.
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
