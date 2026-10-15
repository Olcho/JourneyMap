"""Verify the frozen review source without API access or third-party packages."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/ACTUAL_SOURCE_MANIFEST_2026-10-16.json"


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    mismatches = []
    for record in manifest["files"]:
        path = ROOT / record["path"]
        if not path.is_file():
            mismatches.append(record["path"] + ": missing")
            continue
        digest = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        if digest != record["sha256_lf"]:
            mismatches.append(record["path"] + ": changed")
    package = ROOT / "src/journeymap"
    source = hashlib.sha256()
    for path in sorted(package.rglob("*.py")):
        source.update(path.relative_to(package).as_posix().encode())
        source.update(path.read_bytes().replace(b"\r\n", b"\n"))
    if source.hexdigest() != manifest["source_identity"]:
        mismatches.append("package source identity differs")
    if mismatches:
        raise SystemExit("Frozen backup differs:\n" + "\n".join(mismatches))
    print(f"PASS: {len(manifest['files'])} reviewed files match after LF normalization")
    print(f"source_identity={source.hexdigest()}")
    print("Offline verification only; no key, approval, registry, or API access.")


if __name__ == "__main__":
    main()
