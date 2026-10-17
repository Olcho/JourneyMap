"""Copy and hash existing F1 evidence only. Never run tests or delete files."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from uuid import uuid4

ROOT = Path(r"C:\Users\USER\JourneyMap-Actual")
SOURCES = [
    Path(r"C:\Users\USER\AppData\Local\Temp\journeymap-checkpoint-probe-c3teu4nr"),
    Path(r"C:\Users\USER\AppData\Local\Temp\journeymap-f1-uninstrumented-itc_8_25"),
]
REVIEW = ROOT / "docs/PHASE4_4_INDEPENDENT_REVIEW.md"
DIAGNOSTIC = ROOT / "scripts/diagnose_checkpoint_view.py"
BASE = ROOT / "artifacts/phase4_4/f1-investigation"
STATUS = "현재 전체 회귀 통과, F1 미재현·원인 미확정, 독립 검토의 기존 수정 필요 판정 유지"
REOPEN = "동일 assertion의 재발 또는 새로운 원인 증거 확보"

def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")

def inventory(root):
    result = {"directories": [], "files": {}, "links": {}}
    def walk(directory):
        for entry in sorted(os.scandir(directory), key=lambda e: e.name):
            path = Path(entry.path)
            name = path.relative_to(root).as_posix()
            if path.is_junction():
                raise RuntimeError("Junction requires explicit preservation handling: " + str(path))
            if entry.is_symlink():
                target = os.readlink(path)
                result["links"][name] = {
                    "target": target,
                    "target_text_sha256": hashlib.sha256(os.fsencode(target)).hexdigest(),
                }
            elif entry.is_dir(follow_symlinks=False):
                result["directories"].append(name)
                walk(path)
            elif entry.is_file(follow_symlinks=False):
                result["files"][name] = {"bytes": entry.stat(follow_symlinks=False).st_size, "sha256": sha(path)}
            else:
                raise RuntimeError("Unsupported file type: " + str(path))
    walk(root)
    return result

def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args])

review_before = REVIEW.read_bytes()
review_hash = hashlib.sha256(review_before).hexdigest()
if review_hash != "5f1ef81d2f4b8bb4031a01b8318d1adefbaaceabd3fb6c0e572ac5e2f904ee18":
    raise RuntimeError("Review changed since inspection; preserve current work")
diagnostic_hash = sha(DIAGNOSTIC)
runtime_diff_before = git("diff", "--binary", "--", "src", "tests")
head = git("rev-parse", "HEAD").decode().strip()
start_status = git("status", "--short").decode().strip()
source_inventories = []
for source in SOURCES:
    if not source.is_dir() or source.is_symlink() or source.is_junction():
        raise RuntimeError("Expected original evidence directory: " + str(source))
    contents = inventory(source)
    source_inventories.append(contents)
    print(json.dumps({"source": str(source), "files": len(contents["files"]),
                      "directories": len(contents["directories"]), "links": len(contents["links"]),
                      "bytes": sum(v["bytes"] for v in contents["files"].values())}), flush=True)

stamp = datetime.now(timezone(timedelta(hours=9))).strftime("%Y%m%d-%H%M%S-KST")
archive = BASE / ("closed-" + stamp + "-" + uuid4().hex[:10])
if archive.exists():
    raise FileExistsError(str(archive))
archive.mkdir(parents=True, exist_ok=False)
manifests = archive / "manifests"
manifests.mkdir(exist_ok=False)
print("ARCHIVE=" + str(archive), flush=True)

comparisons = []
for source, before in zip(SOURCES, source_inventories, strict=True):
    destination = archive / source.name
    write_json(manifests / (source.name + ".source-before.json"), {"root": str(source), "inventory": before})
    # Destination must not exist; copytree creates it and does not merge trees.
    shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2, dirs_exist_ok=False)
    original_after = inventory(source)
    copied = inventory(destination)
    write_json(manifests / (source.name + ".source-after.json"), {"root": str(source), "inventory": original_after})
    write_json(manifests / (source.name + ".destination-after.json"), {"root": str(destination), "inventory": copied})
    if before != original_after or before != copied:
        raise RuntimeError("Evidence copy verification failed; all files retained for inspection: " + source.name)
    comparisons.append({"source": str(source), "destination": str(destination),
                        "files": len(copied["files"]), "directories": len(copied["directories"]),
                        "links": len(copied["links"]), "bytes": sum(v["bytes"] for v in copied["files"].values()),
                        "source_before_equals_source_after": True,
                        "source_before_equals_destination_after": True})

before_name = "PHASE4_4_INDEPENDENT_REVIEW.before-closure.md"
after_name = "PHASE4_4_INDEPENDENT_REVIEW.at-closure.md"
with (archive / before_name).open("xb") as stream:
    stream.write(review_before)
if sha(archive / before_name) != review_hash:
    raise RuntimeError("Preserved review mismatch")
if REVIEW.read_bytes() != review_before or sha(DIAGNOSTIC) != diagnostic_hash:
    raise RuntimeError("Existing work changed concurrently; preserve it")
if git("diff", "--binary", "--", "src", "tests") != runtime_diff_before:
    raise RuntimeError("Runtime or existing tests changed concurrently")

addition = f"""

## 2026-10-17 KST F1 조사 종료 및 증거 보존

**상태: {STATUS}.** 추가 테스트 없이 F1 조사를 종료했다. 통과 결과를 수정 완료 또는 환경 문제의 확정으로 해석하지 않는다. 최초 실패와 이전 검토 기록은 그대로 보존한다.

**F1 재조사 조건: {REOPEN}.** 동일 assertion은 `tests/test_life_checkpoint.py:188`의 조회 전후 checkpoint digest 비교다. 이 두 조건 중 하나가 생기기 전에는 반복 재현 실행을 재개하지 않는다.

보관 위치: `{archive}`

| 원본 폴더 | 서로 구분된 새 보관 폴더 | 검증 |
| --- | --- | --- |
| `{SOURCES[0]}` | `{SOURCES[0].name}` | 복사 전/후 원본 및 복사본의 파일 목록·크기·SHA-256, 디렉터리 목록 일치 |
| `{SOURCES[1]}` | `{SOURCES[1].name}` | 복사 전/후 원본 및 복사본의 파일 목록·크기·SHA-256, 디렉터리 목록 일치 |

숨김/cache/pytest 임시 산출물도 제외하지 않고 복사했다. 원본과 기존 대상 파일을 덮어쓰거나 삭제하지 않았으며 새 보관 루트만 만들었다. 링크가 있으면 역참조하지 않고 링크 대상을 보존·비교한다. 파일별 목록과 SHA-256은 `manifests/*.source-before.json`, `*.source-after.json`, `*.destination-after.json`에, 전체 대조 결과와 문서 hash는 `verification.json`에 보존한다.

- 보관 직전의 현재 검토 문서: `{before_name}`; SHA-256 `{review_hash}`.
- 이 종료 기록을 추가한 검토 문서: `{after_name}`. 작업 문서의 기존 바이트는 보존하고 이 절만 추가했다.
- source·기존 테스트·기존 진단 도구는 변경하지 않았다. 키 조회, 실제 API 호출, 승인 파일 생성, commit/push/merge 및 추가 테스트를 실행하지 않았다.

**독립 검토의 기존 수정 필요 판정은 유지되며, 이 조사 종료와 증거 보존은 유료 실행 승인이 아니다.**
""".encode("utf-8")
with REVIEW.open("ab") as stream:
    stream.write(addition)
review_after = REVIEW.read_bytes()
if review_after != review_before + addition:
    raise RuntimeError("Review append verification failed")
with (archive / after_name).open("xb") as stream:
    stream.write(review_after)
after_hash = hashlib.sha256(review_after).hexdigest()
if sha(archive / after_name) != after_hash:
    raise RuntimeError("Final archived review mismatch")
if sha(DIAGNOSTIC) != diagnostic_hash or git("diff", "--binary", "--", "src", "tests") != runtime_diff_before:
    raise RuntimeError("Unexpected source/test/diagnostic change")

verification = {"closed_kst": datetime.now(timezone(timedelta(hours=9))).isoformat(),
    "status": STATUS, "reopen_condition": REOPEN, "archive": str(archive), "head": head,
    "git_status_before": start_status, "git_status_after": git("status", "--short").decode().strip(),
    "comparisons": comparisons,
    "review_before_sha256": review_hash, "review_after_sha256": after_hash,
    "review_original_bytes_preserved": review_after.startswith(review_before),
    "diagnostic_unchanged_sha256": diagnostic_hash, "runtime_and_existing_tests_diff_unchanged": True,
    "tests_executed_during_archiving": 0,
    "archive_control_file_hashes": {p.relative_to(archive).as_posix(): sha(p) for p in sorted(manifests.iterdir())}
      | {before_name: sha(archive / before_name), after_name: sha(archive / after_name)}}
write_json(archive / "verification.json", verification)
print(json.dumps(verification, ensure_ascii=False, indent=2), flush=True)
