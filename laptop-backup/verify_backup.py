"""Verify backup ZIPs and every original file without running project code."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile

root = Path(__file__).resolve().parent
labels = sys.argv[1:] or [p.name.removesuffix("-manifest.json") for p in root.glob("*-manifest.json")]
for label in labels:
    manifest = json.loads((root / (label + "-manifest.json")).read_text(encoding="utf-8"))
    path = root / manifest["archive"]["file"]
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["archive"]["sha256"]:
        raise SystemExit("Archive hash mismatch: " + path.name)
    with zipfile.ZipFile(path) as archive:
        expected = {r["path"]: r for r in manifest["files"]}
        if len(archive.infolist()) != len(expected) or set(archive.namelist()) != set(expected):
            raise SystemExit("Archive member list mismatch")
        for name, record in expected.items():
            member = PurePosixPath(name)
            if member.is_absolute() or ".." in member.parts or "\\" in name:
                raise SystemExit("Invalid archive member path")
            data = archive.read(name)
            if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
                raise SystemExit("Original file hash mismatch: " + name)
    print(label + ": verified " + str(len(expected)) + " original files")
