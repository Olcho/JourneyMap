# JourneyMap 본체 Phase 1 개발 보고서 — 2026-10-14

## 기준선과 작업 위치

- 원래 작업 폴더/저장소: `C:\Users\user\Documents\JourneyMap`
  - branch `codex/experiment03-pickup-diagnostic`
  - HEAD `97a5b6a24079b1f3bf3afb45bac85d30c1468336`
  - 기존 tracked 변경 8개와 E03 untracked 코드/문서/테스트, 별도 FantasyLife 폴더를 보존했다.
    FantasyLife/다른 프로젝트 코드를 본체 구현에 사용하지 않았다.
- 기존 실험 worktree: `C:\Users\user\.codex\worktrees\experiment03-live-preparation\JourneyMap`
  - branch `codex/experiment03-live-preparation`
  - HEAD `557fb3df10149d7b7738137b7ac1038dfba24e84`
  - tracked 문서 변경 6개 및 E03/E04 untracked 구현·fixtures·검사·결과 원본을 보존했다.
- 새 개발 worktree: `C:\Users\user\Documents\JourneyMap\.worktrees\phase1`
  - branch `codex/phase1-concurrent-runtime`
  - 시작 및 종료 HEAD `97a5b6a24079b1f3bf3afb45bac85d30c1468336` (커밋하지 않음)

GitHub 연결에서 [PR #15](https://github.com/Olcho/JourneyMap/pull/15)를 다시 읽어
OPEN/미병합, head `557fb3d...`, base main `97a5b6a...`를 확인했다. 로컬 main ref는
`eaa4b4d`로 뒤처져 있었으며 이를 최신 공식 기준선으로 착각하지 않았다. origin/main과 PR의
현재 base가 일치하는 안정된 커밋 `97a5b6a...`를 새 개발의 출발점으로 선택했다.

새 worktree에는 미커밋 변경이 자동 복사되지 않는다. 이번에는 E03/E04 미커밋 코드를 가져올
필요가 없어 반영한 실험 원본 변경은 0개다. 두 기존 작업 트리에서 파일을 이동/삭제/복구하거나
checkout을 바꾸지 않았다. Git의 새 branch/worktree metadata만 생성했다.

첨부 Phase1 지시문과 로컬 AGENTS/README/spec/architecture/API/ERD/test strategy/roadmap을
읽었고, 연결된 Notion Handoff/작업 규칙/Overview/E04도 조회했다. Notion의 이전 05A 우선순위는
이번 사용자의 본체 개발 결정으로 대체하되 연구 기록은 폐기하지 않았다. 지정된
JourneyMap_Independent_Review_2026-10-13.md는 제공된 로컬 파일 목록에서 발견하지 못했으며
읽었다고 주장하지 않는다. E04의 1,607개 검사는 이번 개발 기준선의 검사 수와 구분한다.

## 실제 기준선 확인

기존 `.venv\Scripts\python.exe`는 Python 3.12.10에서 정상 실행됐다. 시스템 기본 Python은
3.14이고 별도 Python312 설치에는 pytest가 없으므로 해당 실행 파일로 환경을 재설치하지 않았다.
실행 시 PYTHONPATH를 새 worktree의 src로 지정해 editable install이 원래 src를 읽지 않도록 했다.
실제 import 경로는 `...\.worktrees\phase1\src\journeymap\__init__.py`다.

기존 synchronous kernel에서 동일한 2-tick 이동 두 개는 A `0→2`, B `2→4`였고, Controller 없이
tick 24까지 진행한 새 세계는 위치 불변/action result 0개였다. 기존 resource schedule은 1–20의
유한 입력임을 소스에서 확인했다. SQLite adapter를 전체 저장·재개 구현으로 해석하지 않았다.
실행 증거: `artifacts/phase1/baseline-behavior.json`.

## 구현과 실제 장면

기존 modular monolith와 module handler를 사용한다. actor별 세계 복제/병합, 별도 장난감 엔진,
CPU 병렬 처리, 새 runtime dependency는 추가하지 않았다.

| 실제 장면 | 관측 결과 |
|---|---|
| 정상 동시 이동 | A/B 모두 tick 0 접수, 접수 후 시계 0, tick 1에 두 pending, 둘 다 0→2 성공; 최종 tick 2 |
| A 경로 tick 1 폐쇄 | A는 tick 2 FAILED/ROUTE_IMPASSABLE, origin square 유지; B는 tick 2 b-end 도착 |
| 완료와 같은 tick 2 폐쇄 | 환경 우선 후 A FAILED, B SUCCEEDED; 완료 순서 계약과 일치 |
| 이동 중 추가 행동 | engine ACTOR_BUSY/REJECTED, public ACTION_REJECTED; 시간/위치/재고 불변, B 완료 유지 |
| 재고 1개 구매 경쟁 | A 성공/B 실패, bread A/B/seller=1/0/0, wallet=8/10/2; item 합계1/통화 합계20 유지 |
| 반복 구매 동률 6회 | B부터 제출해도 승자는 A/B/A/B/A/B; 마지막 wallet=4/4/12 |
| 시작 후 재고·지갑·가격 변화 | 완료 시 FAILED, 독립 system 변화만 남고 구매 효과/실패자 차감 없음 |
| 이동 중 대상 | BUY/ASK/REQUEST/실제 유효 INFORM의 시작 및 완료 조건에서 이동 대상을 차단 |
| 현장 정보 경계 | 이동 중 Hugh는 bridge witness가 아님; 이동자 local ghost 없음; remote Observation content 불변 |
| 자율 순찰 | A/B가 tick 0/2/4에 판단·접수, 6개 MOVE 완료, horizon 6에서 정상 종료/pending 0 |
| 재현 | fresh seed/input, 중간 tick 분할, split runner, 실제 in-flight frame replay의 world/records/RNG 일치 |

새 실행 모드는 ConcurrentKernel/ConcurrentApplication/ConcurrentGamePort(receipt schema 2)다.
동일 tick 환경→기존 완료→새 판단/접수, 순환 completion 우선권, 완료 시 거래 경쟁,
자기 transit 표현, immutable start timing과 최신 조건 재검증을 명시했다. canonical domain
mutation은 기존 _commit_plan을 통과하고, actor/system dispatch와 정보 경계를 유지한다.

기존 synchronous API 및 기존 replay 입력은 새 시간 의미로 재해석하지 않는다. 기존 테스트
기대값 수정/삭제는 0개다. 세부 실패 정책/기록/저장 schema는
[시간 실행 계약](PHASE1_CONCURRENT_RUNTIME.md)을 따른다.

## 검증 결과

| 검사 | 최종 실제 결과/증거 |
|---|---|
| 전체 offline pytest | **1,150 passed**, 181.88초; 기존 1,117 + 신규 33; `full-final.xml`, `pytest-final.log` |
| 신규 반례 pytest | **33 passed**, 1.16초; `targeted-final.xml`, `pytest-targeted.log` |
| 아키텍처+당시 신규 30개 | **39 passed**; `new-4.xml` |
| Ruff check | 통과; `ruff-check.log` |
| Ruff format --check | **152 files already formatted**; `ruff-format.log` |
| strict mypy | **133 source files**, 오류 없음; `mypy.log` |
| git diff --check | 통과; `diff-check.log` |
| 기존 이동 예제 | tick 5/기존 Knowledge와 replay equality; `legacy-alderwick.log` |
| 기존 사회 예제 | tick 9/기존 간접 Knowledge와 replay equality; `legacy-social.log` |
| 기존 자원 예제 | tick 10/wallet6/bread1/hunger30/fatigue11 및 replay equality; `legacy-resources.log` |
| 신규 실행 예제 | 정상/폐쇄1/폐쇄2/재고경쟁/자율순찰; `scenes-final.json` |

초기 전체 실행의 92개 setup error는 공용 임시 폴더 권한 때문이었다(1,025개 통과).
worktree 내부의 새 basetemp로 재실행했다. 첫 full gate의 아키텍처 실패 1개는 scenario가
bootstrap을 import한 실제 결함이었고 조립을 bootstrap_phase1로 옮겨 고쳤다. type/format
진단도 수정했다. 마지막 replay 검토에서 미등록 action은 live UNKNOWN_ACTION/attempt로 남기고
실행 효과가 없는 dispatch miss를 executable command stream에서 제외했다. invalid policy도
replay할 수 있는 반례를 추가 확인하고 최종 신규/정적/전체 gate를 다시 실행했다.

실행하지 않은 검사는 영속 save/restore 및 E03/E04 미커밋 작업 트리의 전체 1,607개 재실행이다.
후자는 본체 개발 기준선에 포함하지 않았으며 원본 hash 불변을 확인하는 범위다. 과거 ZIP 전체
재감사, Linux 별도 실행, 실제 LLM behavioral validation을 수행했다고 주장하지 않는다.

## 원본 보호

초기 manifest는 796개였다. inventory의 상위 `.codex` 경로 exclusion 때문에 별도 실험 작업
트리가 누락된 것을 발견해 relative-path 기준으로 보정했다. 기존 실험 트리나 원본에 쓰기 작업을
하지 않은 상태에서 **74,942개**의 확장 baseline을 남겼다. 초기 불완전 manifest도 삭제하지
않고 보존했다. source/미추적 파일, trials의 원본·audit·ZIP, 관련 Downloads ZIP의 파일명/크기/
SHA-256을 기록했다. 가상환경/캐시/Git metadata/credential 경로와 key 파일은 범위에서 제외했다.

최종 대조: **before 74,942 / after 74,942, 변경·누락 0개, 추가 0개**.
원본 위치의 증거는 `C:\Users\user\Documents\JourneyMap\.phase1-evidence`에 있으며 최종 manifest와
비교 요약은 개발 worktree의 `artifacts/phase1/protection`에도 복사한다.

GitHub/Notion의 승인된 read-only 조회 외 실제 LLM/유료 호출은 **0회**, API key 조회 **0회**다.
commit/push/merge **0회**다. 과거 호출 승인/상한을 재사용하지 않았다.

## 변경 파일

새 구현: application/concurrent.py, core/concurrent.py, core/concurrent_replay.py,
bootstrap_phase1.py, scenarios/phase1.py, examples/concurrent.py, tests/test_concurrent.py.

기존 구현 변경: bootstrap.py의 opt-in kernel_type, core/kernel.py의 trusted perception snapshot
hook, application/perception.py와 session.py의 snapshot 연결, movement/handlers.py의 absence,
movement/perception.py의 transit 공간 query, scenarios/alderwick/experiment.py의 local ghost 제거.

문서/설정: README.md, docs/API.md, ARCHITECTURE.md, ERD.md, ROADMAP.md, TEST_STRATEGY.md,
PHASE1_CONCURRENT_RUNTIME.md, 이 보고서, .gitignore의 새 검증 출력 경로 제외.
모든 경로는 새 개발 worktree 기준이다. 변경은 총 **23개 파일**(기존 14개 수정, 신규 9개)이며
machine-readable 목록과 파일 해시는 artifacts/phase1에 남긴다.

## 미구현과 다음 단계

영속 save/restore는 미구현이다. 실행 중 snapshot/frame은 재현 검증용이며 복원 API가 아니다.
clock/queue insertion sequence/pending timing/idempotency/RNG/tie/application/activation 상태의
구체적 schema와 복원 acceptance gate를 시간 계약 문서에 정의했다.

UI, 생산/임금, 복잡한 욕구 모델, 기억 선택, LLM 연결, 장기 최적화는 추가하지 않았다.
기존 survival 1–20 schedule의 자동 연장도 구현하지 않았다. 동률 rotation은 작은 고정 cohort에서
검증했으며 일반적인 starvation freedom을 증명하지 않았다. application/runner 기록의 영속화와
독립 artifact tamper audit도 후속이다.

다음 소규모 생활 순환 단계의 **오프라인 구현을 시작할 수 있다**. 새 실행 모드에서 생활 정책과
module-owned 반복 욕구 입력을 유한 horizon으로 조립하고, 자원 보존/정보 경계 검사를 확대해야 한다.
장기 저장·재개나 E04 live protocol에 새 시간 의미를 소급 적용할 준비가 됐다는 뜻은 아니다.

## 사용자가 실행할 명령

```powershell
Set-Location 'C:\Users\user\Documents\JourneyMap\.worktrees\phase1'
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$pythonExe = 'C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe'
& $pythonExe -m journeymap.examples.concurrent
& $pythonExe -m pytest tests/test_concurrent.py -q --basetemp=artifacts/phase1/tmp-user-new
& $pythonExe -m pytest -q --basetemp=artifacts/phase1/tmp-user-full
& $pythonExe -m ruff check .
& $pythonExe -m ruff format --check .
& $pythonExe -m mypy
git diff --check
```
