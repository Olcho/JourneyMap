# Phase 4.4 데스크톱 개발 폴더 반영

2026-10-19 KST. 기존 `C:\Users\<user>\Documents\JourneyMap\.worktrees\phase1`에
검토된 8,192 변경을 선택 반영했다. 새로운 worktree나 전체 폴더 교체는 사용하지 않았다.

실제 HEAD는 `97a5b6a24079b1f3bf3afb45bac85d30c1468336`, 브랜치는
`codex/phase1-concurrent-runtime`이다. 변경을 가져온 기준은
`b7717c2112c076b6f194d26ca0507d13b5b8a4d1` →
`739f08d34325106cf2eaaacc3fadcdecd0858ddd`이며 현재 HEAD를 바꾼 것이 아니다.

## 반영 전 확인

AGENTS.md와 실제 경로·HEAD·브랜치·미커밋 상태를 확인했다. 2026-10-17 백업의 phase1
224개 파일과 현재 파일이 모두 바이트 동일했고 추가 파일도 없었다. 현재 src/tests 170개는
GitHub 이전 기준의 Git blob과 LF 정규화 후 모두 같았다. 백업 이후 충돌하는 작업은 없었다.
새로운 전체 백업을 생성하지 않고 기존 백업과 동결 커밋을 보존했다.

[Notion Actual](https://app.notion.com/p/3fae2656dc268172852deda41e77764b)과
[Chat Handoff](https://app.notion.com/p/3e1e2656dc2681188985fb16942331a0)를 읽었다.
두 페이지의 마지막 편집 기록은 2026-10-16이며 과거 구현·검증 보고로 취급한다.
현재 사용자 지시 및 2026-10-17 인수인계의 F1 종료 판정을 대체하지 않는다.
GitHub 직접 다운로드는 관리 환경의 DNS 제한으로 실패했으며 연결된 GitHub 읽기 도구로
커밋 비교·전체 Git tree·필요 파일을 확인했다. 파일은 Git blob SHA로 검증한 뒤 반영했다.

## 변경 범위

- 수정: `life_admission.py`, `life_http.py`, `life_llm.py`, `life_responses.py`,
  `paid_replay.py`, `test_life_responses.py`, `test_paid_life.py`.
- 추가: `tests/test_life_output_budget.py`.
- 계약 문서: `PHASE4_4_OUTPUT_BUDGET.md` 및 원격 역사 보고서를 추가하고
  README, API, ROADMAP, TEST_STRATEGY의 최신 안내를 반영했다. 기존 문서 본문은 보존했다.
- README의 노트북 안내 링크는 이 데스크톱 반영 기록으로 바꿨다. 원격 역사 보고서에는
  이번 검증과 구별하는 출처 안내 및 원격 노트북 안내 링크만 추가했다.

생활 전용 요청당 출력은 8,192, paid/preflight protocol은 v2다. 기존 연구 요청의 4,096
상한, 모델/high/세계 정책, 누적 한도·재시도 금지 및 과거 binding 의미는 유지한다.
상세 계약은 [출력 예산 계약](PHASE4_4_OUTPUT_BUDGET.md)을 따른다.

## 직접 수행한 검증

Python 3.12.10 및 명시적 phase1/src PYTHONPATH를 사용했다. 실제 import 경로도
`C:\Users\<user>\Documents\JourneyMap\.worktrees\phase1\src\journeymap`임을 확인했다.

| 검사 | 결과 |
| --- | --- |
| output_budget/Responses/paid/audit/response 감사 5개 파일 | **129 passed, 69.37초**, 실패·오류·skip 0 |
| Ruff lint | All checks passed! |
| Ruff format --check | 224 files already formatted |
| strict mypy | Success: no issues found in 170 source files |
| git diff --check | exit0 |
| 기존 백업 대비 15개 변경·추가 파일의 개별 diff --check | whitespace 오류 없음 |
| 현재 src/tests 전체 | 171개 모두 검토 커밋의 Git blob과 LF 정규화 후 일치 |
| 지정 변경 밖의 기존 백업 파일 | 213개 바이트 동일 |
| 원본 registry·실제 report·live-execution 기존 파일 | 18개 크기·SHA-256·mtime 동일 |

최종 package source identity는 다음 요청값과 **정확히 일치**한다.

`f29e9631f80b19d832e83ce7969318bae061a88b543424ed7254499aca91f973`

실제 HEAD와 브랜치는 위 시작 상태 그대로다. 현재 코드·테스트가 739f08d와 같다는 뜻이며,
현재 Git HEAD가 그 커밋으로 바뀌었다는 뜻은 아니다. 문서의 데스크톱 출처 안내는 별도다.
실행한 테스트 명령은 다음과 같다. 전체 pytest와 checkpoint F1 재현 검사는 실행하지 않았다.

```powershell
Set-Location C:\Users\<user>\Documents\JourneyMap\.worktrees\phase1
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
& C:\Users\<user>\Documents\JourneyMap\.venv\Scripts\python.exe -m pytest -q tests/test_life_output_budget.py tests/test_life_responses.py tests/test_paid_life.py tests/test_paid_audit.py tests/test_paid_audit_response.py --basetemp=C:/Users/<user>/Documents/JourneyMap/artifacts/actual-update-20261019/pytest-temp --junitxml=C:/Users/<user>/Documents/JourneyMap/artifacts/actual-update-20261019/pytest.xml
& C:\Users\<user>\Documents\JourneyMap\.venv\Scripts\python.exe -m ruff check .
& C:\Users\<user>\Documents\JourneyMap\.venv\Scripts\python.exe -m ruff format --check .
& C:\Users\<user>\Documents\JourneyMap\.venv\Scripts\python.exe -m mypy
git diff --check
```

테스트는 기존 차단 fixture, 합성 승인 객체·가짜 key 문자열·가짜 HTTPS를 사용했다.
실제 key나 실제 승인 파일을 열거나 생성하지 않았다. 위 결과는 오프라인 correctness
증거이며 실제 모델의 행동 성능·완주나 F1 해결 증거가 아니다. 과거 report의 실제 replay도
이번 작업에서 다시 수행하지 않았다.

증거 경로는 `C:\Users\<user>\Documents\JourneyMap\artifacts\actual-update-20261019`다.
`initial-state.json`, GitHub compare/tree, Notion 페이지 사본, `applied-changes.json`,
`desktop-integration.patch`, `scope-verification.json`, `pytest.xml` 및 각 검사 로그를 남겼다.
Git의 --no-index는 파일 차이가 있으면 exit1을 반환하므로 진단 출력과 함께 판정했다.
새 LF 문서에 대한 CRLF 전환 경고는 호출별 `core.safecrlf=false`로 억제했으며,
저장소 설정이나 whitespace 검증 규칙을 변경하지 않았다.

## 보존 및 남은 범위

원본 registry는 기존 `artifacts/phase4_3/runs.sqlite3` 위치에 둔다. 실제 report와
`artifacts/phase4_3_2/live-execution`의 기존 파일도 이동·변환·재봉인하지 않는다.
검사 전후 해시·크기·수정 시각이 모두 동일하다. 원본 DB의 SHA-256은
`318271053df19a437e5b3f7ace413a76588f1926067ecd0930d5206aed7dc150`이다.
과거 4,096 소스는 기존 백업과 동결 커밋에 남겼다. DB는 이번 작업에서 열거나 이전·초기화하지 않았다.

F1은 **조사 종료, 미재현·원인 미확정, 기존 수정 필요 판정 유지**다. 관련 코드나 판정을
바꾸지 않았다. 노트북의 미커밋 독립 검토 최신본과 F1 보관 자료를 이번에 인수했다고
가정하지 않는다. 이 자료의 접근 여부는 새 백업·반복 검사의 근거로 사용하지 않는다.

키 조회, 모델 계정 접근, 실제 승인 파일 생성, 실제 LLM API 호출, commit/push/merge는
모두 0회다. reset/clean/강제 checkout과 새 worktree 생성도 하지 않았다.
원격 접근은 요청된 GitHub 버전·Notion 자료의 읽기뿐이다.

남은 작업은 노트북의 최신 미커밋 독립 검토 문서·F1 종료 증거를 별도 인수하고,
이 개발 폴더와 기존 registry 경로에 대한 새 실행안을 검토하는 것이다.
F1 잔여 불확실성과 과거 replay 미검증 범위에 대한 사용자 판단 및 키 조회·새 실제 run의
명시적 승인도 아직 필요하다. 새 유료 실행이나 승인안·승인 파일 작성은 이번에 수행하지 않았다.
