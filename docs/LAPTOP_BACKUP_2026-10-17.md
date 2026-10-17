# 노트북 초기화 전 백업 — 2026-10-17 KST

원격 저장소: https://github.com/Olcho/JourneyMap

이 백업은 공개 저장소에 저장된다. 실제 API 키, 비밀번호, 인증 파일은 백업에 포함하지 않는다.
탐지된 키/secret 형태의 문자열은 모두 기존 테스트의 합성 fixture로 검토했다.
프로젝트 밖의 사용자 인증 설정과 API 키는 초기화 후 별도로 다시 설정한다.

## 조사 결과

| 원본 폴더 | 원래 브랜치 / HEAD | 상태 및 보존 위치 |
| --- | --- | --- |
| `C:\Users\USER\Documents\JourneyMap` | `main`, `4c4c00601cccc7c56daf3deb67621d4ba8d02d76` | 등록 worktree 1개, 로컬 브랜치 5개. 기존 tracked 파일 변경 없음. `review-temp/`와 ignored `trials/` 등을 research 압축본에 보존. |
| `C:\Users\USER\JourneyMap-Actual` | `codex/actual-output-budget-8192`, `739f08d34325106cf2eaaacc3fadcdecd0858ddd` | 등록 worktree 1개, 로컬 브랜치 1개. 미추적 문서 2개/진단 스크립트 1개와 ignored F1 조사 artifacts를 actual 압축본에 보존. 세 미추적 파일은 백업 커밋에도 일반 파일로 추가. |
| `C:\Users\USER\Documents\JourneyMap-broken-20260911` | 유효한 HEAD를 읽을 수 없음 | HEAD 파일이 NUL 바이트이며 Git이 저장소로 인식하지 않음. 수리 없이 남은 파일 108개를 broken 압축본에 보존. |

두 정상 저장소에는 stash가 없고 추가 등록 worktree나 중첩 Git 저장소는 없다.
알려진 Codex worktree 경로에도 별도 checkout은 없었다.
fetch 후 기존 로컬 브랜치의 원격 미포함 커밋은 0개다.
연구 폴더의 main은 원격 main보다 6개 뒤이며 m7 브랜치는 원격보다 1개 뒤다.
다른 연구 로컬 브랜치 3개와 actual 브랜치는 동일한 원격 브랜치에 보존돼 있다.
원격 main은 조사 시 `97a5b6a24079b1f3bf3afb45bac85d30c1468336`이다.
로컬 브랜치와 원격 기존 브랜치를 병합하거나 이동하지 않는다.

연구 저장소의 미참조 merge commit `8860122fe830f9a40de5941b3cc129add302e316`은
이미 원격에 있는 수정 merge `49cc9f5`와 tree가 동일하다. 고유 파일 내용은 없으며
원본 object/reflog는 로컬 전용 Git 메타데이터 ZIP으로 보존했다.
손상 저장소의 과거 커밋 완전성은 확인할 수 없으므로 복구 완료라고 주장하지 않는다.

## GitHub 백업 브랜치와 데이터

- `codex/laptop-backup-research-20261017`: 원래 연구 main에서 분기. `laptop-backup/research-files.zip`은 603개 파일(12,321,968 bytes), `broken-files.zip`은 108개 파일(651,384 bytes)을 원본 byte 그대로 보존한다. 검토 문서와 진단 스크립트는 일반 파일로도 보존한다.
- `codex/laptop-backup-actual-20261017`: 원래 actual HEAD에서 분기. `laptop-backup/actual-files.zip`은 1,150개 파일(124,092,544 bytes)을 보존한다.
- 각 `*-manifest.json`은 원본 상대 경로/크기/SHA-256과 ZIP SHA-256을 포함한다.
- ignored Horizon/Recency trial JSON/JSONL, F1 조사 기록/체크포인트/SQLite DB도 ZIP에 포함한다. DB는 합성 테스트 registry를 포함한 원본 자료이며 기존 기록을 지우거나 재생성하지 않았다.
- `.venv`, Python bytecode, pytest/mypy/Ruff cache와 egg-info는 재생성 대상으로 제외했다. 빈 임시 디렉터리는 Git 보존 대상이 아니다.

백업 생성은 별도 index와 검증된 복사본을 사용한다. 원본 작업 브랜치/HEAD/index와
미커밋 파일을 그대로 유지하므로 원본 `git status`에 기존 미추적 파일이 계속 보이는 것은 정상이다.
백업 브랜치의 커밋과 ZIP에 그 내용이 별도로 보존된다.
추가 실제 API 호출, 연구 실행, 기존 테스트/불변식 변경은 수행하지 않았다.
이 작업의 검증은 파일/ZIP/commit/원격 무결성이다. 과거 F1 연구 gate의 판정을 변경하지 않는다.

## 초기화 후 복구

Python 3.12와 Git을 설치하고, **새 빈 경로에** 각각 clone한다. 데스크톱의 기존 checkout 위에 복원하지 않는다.

```powershell
git clone --branch codex/laptop-backup-research-20261017 --single-branch https://github.com/Olcho/JourneyMap.git JourneyMap-Research
git clone --branch codex/laptop-backup-actual-20261017 --single-branch https://github.com/Olcho/JourneyMap.git JourneyMap-Actual

cd JourneyMap-Research
py -3.12 laptop-backup/verify_backup.py
Expand-Archive -LiteralPath laptop-backup/research-files.zip -DestinationPath . -Force
Expand-Archive -LiteralPath laptop-backup/broken-files.zip -DestinationPath ../JourneyMap-broken-files
cd ../JourneyMap-Actual
py -3.12 laptop-backup/verify_backup.py
Expand-Archive -LiteralPath laptop-backup/actual-files.zip -DestinationPath . -Force
```

`-Force`는 위에서 새로 clone한 백업 checkout에 원래 파일 byte와 ignored 데이터를 복원하기 위한 것이다.
ZIP에는 `.git`이 없으므로 clone의 정상 Git 메타데이터는 유지된다. broken 파일 폴더를 정상 Git 저장소로 간주하지 않는다.
raw 데이터/replay의 source compatibility는 소스 byte와 경로에 영향을 받을 수 있으므로 manifest와
동결 소스를 함께 보존한다. 민감 승인·기존 실행 registry를 새 실행 승인으로 재사용하지 않는다.

각 정상 checkout에서 새 가상환경을 만든다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
```

개발 재개 전 해당 README/AGENTS/TEST_STRATEGY를 확인하고 필요한 오프라인 gate를 실행한다.
백업 브랜치는 보존하고 새 개발 브랜치로 작업하는 편이 좋다. 다른 원격 브랜치도 필요하면
`git config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'` 후 `git fetch origin`으로 가져온다.
이 fetch refspec의 `+`는 로컬 remote-tracking ref 동기화이며 원격에 Push하는 명령이 아니다.

## GitHub에 올리지 않는 별도 파일

로컬 `C:\Users\USER\Documents\JourneyMap\trials\laptop-backup-20261017\`에
`PRIVATE-git-metadata.zip`을 보관했다. 정상 두 저장소의 원본 reflog/objects/config와
손상 저장소의 `.git`을 포함하며 공개 저장소에는 올리지 않는다.
이 로컬 폴더의 Git bundle과 검증 기록도 별도 보관용이다.
필요한 경우 **초기화 전에 이 로컬 백업 폴더를 외장 저장장치 등 노트북 밖으로 복사**한다.
현재 폴더에만 둔 파일은 노트북 초기화 시 사라진다.
개인 API 키/인증정보는 공개 GitHub 대신 사용 중인 비밀관리 수단으로 보관하고 초기화 후 재설정한다.

GitHub Push 결과와 백업 commit SHA는 최종 원격 검증 기록/최종 답변에서 확인한다.
