# Experiment 03 실제 LLM 연결 준비 — 2026-10-05 완료 기록

첨부한 2026-10-04 live 준비 지시문의 코드·문서·오프라인 검증 범위를 수행했습니다.
실제 외부 LLM/API 호출과 실제 API key 조회·사용은 하지 않았습니다. 모델·금액을 임의로 정하지
않았으며 최종 설정은 [실행 설정표와 smoke 기준](EXPERIMENT03_LIVE_PREPARATION.md)에 남겼습니다.
mock correctness는 실제 LLM의 behavioral effect 또는 construct validity의 증거가 아닙니다.

## 기준과 보존

- 시작 원본: `C:\Users\<user>\Documents\JourneyMap`, `codex/experiment03-pickup-diagnostic`,
  HEAD `97a5b6a24079b1f3bf3afb45bac85d30c1468336`, 기존 미커밋 17개 파일을 보존했습니다.
- GitHub PR #15가 open이고 최신 HEAD가 `557fb3df10149d7b7738137b7ac1038dfba24e84`임을
  확인했습니다. remote main도 확인했으며 선행 PR을 포함한 최신 기준으로 별도 worktree를 만들었습니다.
- 작업 경로: `C:\Users\<user>\.codex\worktrees\experiment03-live-preparation\JourneyMap`.
  작업 브랜치: `codex/experiment03-live-preparation`, HEAD는 위 PR HEAD입니다.
- Notion Handoff/Research Objective/Working Conventions/Experiment 03/Preflight를 읽고
  이번 명시적 작업 계약과 의미상 충돌이 없음을 확인했습니다. Notion은 수정하지 않았습니다.
- commit, push, PR 생성, merge는 하지 않았습니다. 모든 새 변경은 이 worktree의 미커밋 파일입니다.

기존 변경 코드와 actual historical/raw baseline/matrix/이전 ZIP 중 보존 검사용으로 기록한
**94개 파일 SHA-256이 모두 불변**입니다. 이전 ZIP은 원본 checkout의
`trials/review-bundles/JourneyMap_Experiment03_review_2026-10-04.zip`에 그대로 있습니다.
그 ZIP에는 이번 live 준비 변경을 덧씌우지 않았습니다.

## 변경 코드

| 파일 | 변경 이유 |
| --- | --- |
| `adapters/pickup_cue_live.py` | 전용 configuration/allowlist body, one-shot Provider, 실제 HTTP/recording byte transport, 안전한 응답 provenance |
| `experiments/pickup_cue_live_plan.py` | DRAFT/READY, source/runtime/digest, 별도 seeds/고정 allocation/호출 상한 |
| `experiments/pickup_cue_session.py` | fresh 실제 5-action 준비와 마지막 응답 1회 평가, 기존 projection/parser/성공 predicate 재사용 |
| `experiments/pickup_cue_live.py` | explicit live preflight, exclusive 기록, durable intent/response/export, 첫 오류 중단 |
| `experiments/pickup_cue_live_audit.py` | Provider 없는 계획·journal·관찰/body 재구성, 별도 replay, 응답 재평가, 중단 전체 allocation 보고 |
| `examples/pickup_cue_live.py` | plan/mock/live/audit/inspect 전용 CLI, 기본 도움말 및 offline 실행 |
| `tests/test_pickup_cue_live.py` | 전송 bytes, 정보 차이, preflight, 실패·변조·중단 및 fake HTTP 테스트 |
| README/API/ARCHITECTURE/ERD/ROADMAP/TEST_STRATEGY | 현재 준비 범위와 독립 계약 링크/소유권/검증 gate |
| 이 보고서와 `EXPERIMENT03_LIVE_PREPARATION.md` | 한국어 계약, 정확한 명령, 미확정 설정과 실제 서비스 검증 한계 |

코드 경로는 `src/journeymap/` 아래입니다. 기존 실행 코드와 tests, 지난 완료 보고서는 수정하지
않았습니다. 새 의존성/SDK, Core 기능, 새 memory policy, Experiment 04는 추가하지 않았습니다.

## 오프라인 산출물

모든 새 실행 자료는 작업 worktree의 `trials/live-preparation-verification/`에 있습니다.

- `draft-final.json`: 실험 대상 model/출력 한도 등 미확정 DRAFT, 실행 거부 대상입니다.
- `mock-plan-final.json`, `matrix-final/`: mock 전용 40 allocation의 동결 계획과 원본 42개 파일입니다.
- `matrix-final-cli.json`, `offline-gate.json`: 최종 실행 및 Provider를 차단한 재감사 결과입니다.
- `historical-gate.json`: actual M8 원본의 이번 replay/audit와 17개 파일 해시입니다.
- `preserved-before.json`, `preservation-gate.json`: 원본 94개 파일 및 이전 baseline/matrix 보존 gate입니다.
- `offline_cli.py`, `offline_gate.py`, `historical_gate.py`: 실제 사용한 로컬 검증 wrapper/scripts입니다.
- `pytest-final.log`: 이번 전체 회귀 결과입니다. 초기 wrapper 오류 로그도 삭제하지 않았습니다.

최종 matrix는 **준비 Engine 제출 200회, 평가 mock 시도 40회, 평가 Engine 제출 40회**입니다.
선택된 원본 경험에 안내가 있는 20개에서 fixture가 해당 수령처로 이동하고 나머지 20개는 WAIT합니다.
40개 모두 기록 audit가 INCLUDED이며 실제 API 호출 수는 0입니다. 이는 입력/기록 통합 검증입니다.
안내 없는 A/B 10쌍의 전체 HTTP body는 동일하고, 안내 있는 10쌍은 과거 notice 값만 다릅니다.
transport에 실제 전달된 UTF-8 bytes와 저장 body/hash의 일치도 테스트에서 확인합니다.

새 세계의 실제 관찰부터 전체 body·응답 parse·receipt·성공을 재구성하고 별도 Engine replay를
통과했습니다. mock 원본 42개 파일의 audit 전후 해시와 기록된 source/current source가 같습니다.
오류 테스트의 추가 mock/fake HTTP 호출은 최종 40-call matrix와 별도입니다.

## 실패·중단 경계

live flag 누락, DRAFT, digest/source/runtime 차이, 기존 출력, 초과 cap, 중복/순서 변경,
branch 누락, mock 계획의 live 사용, extra metadata를 transport/key 접근 전에 거부했습니다.
정상 WAIT, 실제 REJECTED MOVE, refusal/incomplete/잘못된 JSON/action을 구분했습니다.
timeout/connection failure와 returned model mismatch는 첫 사례 후 중단하며 원 allocation을 보존합니다.
HTTP double은 명시적인 fake key만 사용하고 header/오류/echo/close 실패에서도 secret을 기록하지 않습니다.

intent 이후 crash, torn tail, fsync/response/export/result/end 기록 오류를 검사했습니다.
응답이나 export가 있어도 완전한 RESULT가 없으면 실행 성공을 추정하지 않습니다.
fsync 실패 직후 읽을 수 있는 줄이 있어도 executor는 INCOMPLETE를 보고하며 가능한 경우 독립 실패
표식을 남깁니다. 모든 저장이 실패한 경우는 반환값과 마지막 durable prefix의 증거 범위를 구분합니다.
body/응답/성공/계획/allocation/mode를 재봉인한 모순 및 export 삭제를 거부했습니다.
named branch와 detached null source 처리도 검증했습니다.

## 환경과 명령

원본 `.venv` launcher가 없는 Python 경로를 참조하므로 번들 Python과 기존 개발 패키지를 사용했습니다.
환경 설치나 원본 venv 수정은 하지 않았습니다.

```powershell
Set-Location 'C:\Users\<user>\.codex\worktrees\experiment03-live-preparation\JourneyMap'
$journeyPython = 'C:\Users\<user>\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$env:PYTHONPATH = "$PWD\src;C:\Users\<user>\Documents\JourneyMap\.venv\Lib\site-packages"
& $journeyPython trials/live-preparation-verification/offline_cli.py pytest -q --basetemp trials/live-preparation-verification/pytest-final -o cache_dir=trials/live-preparation-verification/pytest-cache
& $journeyPython -m ruff check .
& $journeyPython -m ruff format --check .
& $journeyPython -m mypy
git diff --check
& $journeyPython trials/live-preparation-verification/offline_cli.py journeymap.examples.pickup_cue_live plan --output trials/live-preparation-verification/draft-final.json
& $journeyPython trials/live-preparation-verification/offline_cli.py journeymap.examples.pickup_cue_live plan --mock-config --output trials/live-preparation-verification/mock-plan-final.json
& $journeyPython trials/live-preparation-verification/offline_cli.py journeymap.examples.pickup_cue_live mock --plan trials/live-preparation-verification/mock-plan-final.json --output trials/live-preparation-verification/matrix-final
& $journeyPython trials/live-preparation-verification/offline_cli.py journeymap.examples.pickup_cue_live inspect trials/live-preparation-verification/matrix-final
& $journeyPython trials/live-preparation-verification/offline_gate.py
& $journeyPython trials/live-preparation-verification/historical_gate.py
```

이미 존재하는 결과 경로는 재사용하지 않습니다. 일반 audit와 향후 승인 후 live 명령은
[계약의 실행 명령](EXPERIMENT03_LIVE_PREPARATION.md)에 있습니다.
pytest wrapper는 socket을 차단하고 테스트 자식 프로세스의 key 값을 실제 값 조회 없이 가짜 값으로
교체합니다. CLI/audit wrapper는 key 조회 자체를 차단합니다. 처음에는 기존 테스트의 fake-key
설치/삭제까지 조회 guard가 막아 7 failed/88 setup errors가 발생했습니다. 이는 wrapper 문제로,
기존 테스트나 제품의 key guard를 완화하지 않고 자식 프로세스 검증 wrapper를 수정했습니다.

최종 결과는 다음과 같습니다. 선행 검증 수를 재사용하지 않고 이번 worktree에서 실행했습니다.

| 검사 | 결과 |
| --- | --- |
| 전체 pytest | **1238 passed in 94.35s**, 선행 1184 + 새 경계 54, skip/xfail 없음 |
| Ruff check | All checks passed |
| Ruff format --check | 161 files already formatted |
| strict mypy | Success, 140 source files |
| git diff --check | exit 0, LF→CRLF 안내만 있고 diff 오류 없음 |
| Python / pytest | 3.12.14 / 8.4.2 |
| Ruff / mypy | 0.16.6 / 1.20.2 (compiled) |

각 정적 검사 exit code는 `static-gate.json`, 로그는 `ruff-check.log`, `ruff-format.log`,
`mypy.log`, `git-diff-check.log`입니다. 최종 mock plan digest는
`89c0608be6fc8d559634b4a05153e3b4f24b86bdbebadb00b94133aec7362ee5`입니다.
기존 수정 6개 문서와 새 9개 파일(코드 6, 테스트 1, 문서 2)이 미커밋 상태입니다.

## actual historical gate와 남은 사항

원본 `C:\Users\<user>\Documents\JourneyMap\trials\m8-first-official-sol`의 실제 17개 파일을
읽기 전용으로 검사했습니다. replay equality=true, tick 24, final digest는
`c8357de86dc6066843a24e047e241b824f565be2d12ba540317e46f639c07733`입니다.
strict audit는 기존 의미대로 **EXCLUDED / NEW_PROVENANCE_REQUIRED**입니다. 원본을 새 live 증거로
승격하지 않았습니다. 이전 baseline 및 pickup matrix도 이번 코드에서 INCLUDED로 재검증했습니다.

최종 모델·reasoning·출력/timeout/응답 크기·returned model 목록·예산·사용자 실행 승인은 미확정입니다.
표와 smoke 중단 기준을 문서로 남겼으며 이번 offline 구현의 blocker로 취급하지 않습니다.
실제 서비스의 schema 수용, 모델별 parameter 지원, 비용/usage 및 실제 행동 결과는 무호출 검증으로
확정할 수 없습니다. 선행 CI/다른 환경의 1184 tests를 이번 결과로 재사용하지 않습니다.
