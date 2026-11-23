"""Versioned software identity outside deterministic engine state."""

import platform
import subprocess
import sys
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

from journeymap.core.canonical import JsonObject

SOURCE_IDENTITY_VERSION = "utf8-lf-path-lengths-1"


def logical_source_sha256(sources: Mapping[str, bytes]) -> str:
    """UTF-8 POSIX paths, byte ordering, length framing; only EOL normalization.

    Preserve all other characters, whitespace, BOM and final-newline presence.
    Invalid UTF-8 fails rather than silently replacing actual source content.
    """
    digest = sha256(SOURCE_IDENTITY_VERSION.encode("ascii") + b"\0")
    for name in sorted(sources, key=lambda item: item.encode("utf-8")):
        path = name.encode("utf-8")
        content = sources[name].decode("utf-8").replace("\r\n", "\n")
        payload = content.encode("utf-8")
        for value in (path, payload):
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
    return digest.hexdigest()


def runtime_identity() -> JsonObject:
    return {
        "implementation": platform.python_implementation(),
        "version": list(sys.version_info[:3]),
    }


def code_identity(root: Path | None = None) -> JsonObject:
    root = root if root is not None else Path(__file__).resolve().parents[3]

    def git(*args: str) -> str | None:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=root,
                check=True,
                capture_output=True,
                encoding="utf-8",
                timeout=5,
            ).stdout.strip()
        except (OSError, UnicodeError, subprocess.SubprocessError):
            return None

    status = git("status", "--porcelain")
    sources: dict[str, bytes] = {}
    legacy_digest = sha256()
    for path in sorted((root / "src" / "journeymap").rglob("*.py")):
        name, payload = path.relative_to(root).as_posix(), path.read_bytes()
        sources[name] = payload
        # Preserve the original raw-byte source_sha256 semantics and scope.
        legacy_digest.update(name.encode("utf-8"))
        legacy_digest.update(b"\0")
        legacy_digest.update(payload)
    sources["pyproject.toml"] = (root / "pyproject.toml").read_bytes()
    return {
        "git_commit": git("rev-parse", "HEAD"),
        "working_tree_dirty": None if status is None else bool(status),
        "source_sha256": legacy_digest.hexdigest(),
        "source_identity_version": SOURCE_IDENTITY_VERSION,
        "source_scope": "src/journeymap/**/*.py+pyproject.toml",
        "git_source_tree": git("rev-parse", "HEAD:src/journeymap"),
        "working_source_sha256": logical_source_sha256(sources),
    }
