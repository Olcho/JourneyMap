# JourneyMap Actual: 노트북에서 이어서 개발하기

2026-10-16 KST. 이 브랜치는 Phase 4.3.2 독립 검토 소스의 백업이다.
생활 시뮬레이션 본체의 현재 상태는 [Notion Actual](https://app.notion.com/p/3fae2656dc268172852deda41e77764b)과
[인수인계](docs/ACTUAL_HANDOFF_2026-10-16.md)를 따른다. README와 과거 보고서는 당시 기록을 보존한다.

## 백업 범위

- 저장소: `Olcho/JourneyMap`, 브랜치: `backup/journeymap-actual-20261016`.
- 부모: `97a5b6a24079b1f3bf3afb45bac85d30c1468336`. main에 병합하지 않은 독립 백업이다.
- 검토 ZIP에서 가져온 소스, 테스트, 문서, 설정 224개 파일과 이 인수인계 자료를 포함한다.
- Git 저장 시 CRLF를 LF로 정규화한다. 원본별 byte hash와 정규화 hash는 manifest에 함께 기록한다.
- 데스크톱 작업 트리, 다른 E03/E04 미커밋 작업 전체, 가상환경, 실제 실행 ZIP,
  실제 승인 파일, API 키, registry DB는 이 백업의 범위가 아니다.
- 기존 실행 ID `86212ada-c33b-4813-94a9-2fdf4a32774d`는 사용 완료 상태다.
  노트북에 registry가 없더라도 이전 승인을 다시 사용하면 안 된다.

## Windows 노트북 시작

Git과 Python 3.12가 설치된 PowerShell에서 새 폴더로 복제한다.
기존 `JourneyMap-Actual` 폴더가 있다면 다른 이름을 사용한다.
가상환경 활성화 없이 아래 Python 경로를 직접 사용한다.

```powershell
git clone --branch backup/journeymap-actual-20261016 https://github.com/Olcho/JourneyMap.git JourneyMap-Actual
Set-Location JourneyMap-Actual
git status --short
git log -1 --oneline
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e '.[dev]'
& .\.venv\Scripts\python.exe scripts/verify_actual_backup.py
& .\.venv\Scripts\python.exe -m journeymap.examples.life --verify-replay
& .\.venv\Scripts\python.exe -m journeymap.examples.life_ui
```

UI 주소는 `http://127.0.0.1:8765`다. 처음에는 정지 상태이며 Ctrl+C로 서버를 종료한다.
위 예제는 전원 규칙 기반이고 API 키가 필요하지 않다. pip 설치에는 인터넷이 필요하다.
이 환경 구성은 개발 도구의 허용 버전 범위를 설치하며, 완전히 고정된 의존성 lockfile은 아니다.

macOS/Linux에서는 `python3.12 -m venv .venv` 후 `.venv/bin/python`을 사용한다.

## 코드 수정 전 확인

```powershell
& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m ruff format --check .
& .\.venv\Scripts\python.exe -m mypy
git diff --check
git switch -c codex/actual-output-budget-8192
```

백업 manifest 검사는 동결된 백업이 동일한지 확인하는 용도다. 이후 의도적으로 파일을 수정하면
불일치가 정상이며, 검사를 통과시키려고 원래 manifest를 덮어쓰지 않는다.

## Codex에 전달할 시작 문구

> 이 저장소의 AGENTS.md, ACTUAL_START_HERE.md, docs/ACTUAL_HANDOFF_2026-10-16.md를 읽고
> JourneyMap Actual 개발을 이어가세요. 현재 브랜치와 작업 트리 상태부터 확인하세요.
> 첫 실제 실행은 high/4096에서 5번째 응답이 INCOMPLETE가 되어 tick 9에 멈췄습니다.
> 다음 후보는 같은 high에서 요청당 출력 8192이며 아직 구현되지 않았습니다.
> 우선 변경 계획과 오프라인 검증 범위를 제시하세요. 과거 4096 기록의 replay를 보존하세요.
> 실제 키 조회, 승인 파일 생성, 유료 실행은 별도 명시적 승인 없이는 하지 마세요.

개발용 권고는 **Sol / High**다. 실제 실험 모델 `gpt-5.4-mini-2026-03-17 / high`와 구분한다.

## 기기 간 이어받기

작업을 마칠 때 의도한 소스와 문서만 검토하여 새 개발 브랜치에 커밋하고 push한다.
다른 기기에서는 먼저 미커밋 작업을 확인한 뒤 fetch하고 해당 브랜치를 별도 worktree로 연다.
데스크톱의 기존 `.worktrees/phase1` 위에 reset하거나 덮어쓰지 않는다.

```powershell
git fetch origin
git worktree add ..\JourneyMap-Actual-next -b codex/actual-output-budget-8192-desktop origin/codex/actual-output-budget-8192
```

위 마지막 명령은 노트북 개발 브랜치를 push한 뒤에만 사용한다. 각 worktree의 가상환경은
해당 기기에서 새로 만든다. 실제 실행 원본은 공개 GitHub에 올리지 않고 별도 보존한다.
과거 실제 기록 replay에는 원래 report와 동결된 소스가 필요하며 API 키는 필요하지 않다.
