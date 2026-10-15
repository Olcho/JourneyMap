# JourneyMap Phase 4.1 완료 보고서

2026-10-15. Phase 4 독립 검토의 F1/F2를 수정하고 오프라인 검증을 완료했다.
작업 위치는 `C:\Users\user\Documents\JourneyMap\.worktrees\phase1`, 브랜치는
`codex/phase1-concurrent-runtime`, HEAD는 `97a5b6a24079b1f3bf3afb45bac85d30c1468336`이다.
기존 Phase 1~4 미커밋 구현을 기준으로 이어서 작업했다. HEAD로 reset/checkout하지 않았다.

## 수정 원인과 전후 결과

| 항목 | 수정 전 재현 | 수정 후 실제 결과 |
| --- | --- | --- |
| F1: 3,001바이트 깊은 JSON | RecursionError가 전파되고 service/call은 READY, ready/observation은 None, 오류 없음 | JSON_DEPTH_EXCEEDED, call=ERROR, service=PAUSED_ERROR, ready/observation=None, 원문 보존 |
| F2: 기한 105, 완료 106, poll 110 | 중간 poll이 없으면 READY → SUCCEEDED, tick 1 | 완료 105.001, poll 110에서 TIMEOUT/PAUSED_ERROR, discarded_late=true, tick 0 |
| F2 대조: 기한 105, 완료 104.999 또는 105, poll 110 | 늦은 poll만으로 timeout 처리하면 잘못 버릴 수 있는 사례 | 두 경우 모두 READY → 행동 성공, tick 1 |

F1 수정 전 반례는 수정에 앞서 현재 작업 소스에서 실행했다. F2 수정 전 반례는 보존한
시작 소스의 src만 별도 경로에 풀어 같은 clock/gate로 실행했다. 현재 소스를 되돌리지 않았다.
각 전후 JSON과 F2 수정 전 재현 스크립트는 검토 ZIP의 evidence에 포함한다.

`LifeDecisionService.resolve`는 파싱 전에 READY 필드를 비우지 않으며, RecursionError를
생활 응답의 예상 가능한 오류로 처리한다. 공통 실패 경계가 원문과 오류를 남기고 상태와 필드를
함께 정리한다. 자동 retry/fallback/행동 접수는 없다. F1에서 tick 0, 요청 1, 접수 시도 0을
유지하며 조회·정지·저장·직접 복원과 Provider 무재호출 replay를 검증했다.

F2는 시작과 완료에 같은 단조 시계를 사용한다. worker는 Provider 반환과 identity 검증을
마친 최종 packet을 mailbox에 게시할 때 완료 시각을 기록한다. 완료 시각 ≤ 기한이면 허용하고,
완료 시각 > 기한이면 owner poll 시점이나 중간 poll 유무와 관계없이 버린다.
빈 큐에서는 현재 시각 > 기한일 때 timeout이다. 같은 lock으로 timestamp/publication과
empty/timeout 판정을 직렬화했다. 실제 상태 변경과 행동 접수는 계속 owner가 수행한다.
clock/thread/queue/lock/시작·완료 시각은 checkpoint에 추가하지 않았다.
정책의 자세한 내용은 [오류 경계 계약](PHASE4_1_ERROR_BOUNDARIES.md)을 따른다.

## 변경 파일

- `src/journeymap/experiments/life_decisions.py`: 일관된 실패 경계, 깊이 예외, 완료 시각 기반 timeout.
- `src/journeymap/experiments/mixed_life.py`: 테스트용 decision_clock 주입, 폐기된 late raw의 replay 검증.
- `src/journeymap/experiments/live.py`: 내부 poll API 호출 두 곳 조정.
- `tests/test_life_error_boundaries.py`: 새 회귀 7개. clock/gate로 경계 시각과 상태·원문·복원/replay를 검증.
- `tests/test_life_llm.py`: 내부 poll API/시계 주입에 맞춤. 기존 timeout 기대값은 유지.
- `scripts/verify_phase4_1.py`, `verify_phase4_1_boundaries.py`, `package_phase4_1_review.py`: 실행 증거와 배포 검증.
- README, ARCHITECTURE, API, TEST_STRATEGY, 새 계약/보고서, `.gitignore`: 현재 계약 및 증거 경로 반영.

공용 strict_json, 연구 adapter prompt/schema, 생활 fixture, 지도 HTML과 이전 계약/보고서는
변경하지 않았다. Core/domain 기능과 외부 의존성을 추가하지 않았다.
시작 스냅샷과 최종 소스의 파일별 변경은 ZIP의 PHASE4_1_CHANGES.json으로 확인할 수 있다.

## 실행 검증

Python 3.12.10, 현재 worktree의 src를 명시적으로 PYTHONPATH에 지정했다.

| 검사 | 결과와 증거 |
| --- | --- |
| 전체 pytest | **1,249 passed, 208.52초**, 실패/오류/건너뛰기 0. full-final.xml, pytest-final.log |
| Phase 4 + 신규 오류 경계 | **41 passed, 8.44초**. errors-final.xml, errors-final.log |
| Ruff check / format --check | 통과. ruff-check.log, ruff-format.log |
| strict mypy | 157 source files 통과. mypy.log |
| git diff --check | 통과. diff-check.log |
| 정상 혼합 24시간 | tick 24, 요청 12, 사건 171, 진행 중 행동 3. Phase 4 기록 보존 |
| 혼합 새 프로세스 복원 | tick 2 MOVE, tick 5 WORK, tick 13 REST 모두 연속 실행과 최종 digest 일치 |
| READY 및 일반 오류 흐름 | 기존 malformed/refusal/incomplete/크기/요청 한도/엔진 거부/READY 무재호출 테스트 유지·통과 |
| 여섯 RULE 시나리오 | normal/stockout/no-money/no-materials/no-wages/closure 각각 168시간, 두 replay 성공 |
| RULE 새 프로세스 복원 | MOVE/WORK/REST 세 경계에서 전체 checkpoint 및 inputs/decisions/report 동일 |
| Provider 무재호출 replay | 정상 혼합 및 F1/F2 오류 원문 검증. 실제 Provider generate를 차단한 F1 복원 검증 포함 |
| clock origin 변경 | 100과 1,000,000 시계의 정상 24시간 checkpoint digest 동일 |
| 방향별 지도 | 기존 실제 drawMap JS를 Node vm에서 실행. 양방향 열림/한쪽 폐쇄/양쪽 폐쇄 각각 화살표 8개와 marker/passability 확인 |
| 실제 브라우저 | 깊은 JSON 자동 진행 후 프로세스 생존, PAUSED_ERROR, tick 0, 요청 1, 접수 0. Save/Restore와 Ada 조회 후 오류 상태 유지, console error 없음 |

기존 1,242개 테스트를 유지하고 오류 경계 7개를 추가했다. 시간 경계 회귀는 sleep에 의존하지
않는다. Event.wait의 상한은 worker 게시 확인이 실패할 경우 테스트를 끝내는 용도다.
전체 테스트 후 runtime 소스는 바꾸지 않았으며, 최종 문서/helper/packager에 정적 검사를 다시 수행했다.

정상 혼합 summary/execution과 events/results/attempts/inputs/decisions/provider의 전체 구조를
Phase 4 기준선과 비교했다. provider.checkpoint_digest만 소스 호환성 hash 변경 때문에 제외했다.
Ada는 MOVE 6, BUY 2, WORK 2, CONSUME 1, REST 1, 임금 20을 유지했다.
최종 전체 통화 256, 밀가루 152, 빵 54로 생산·소비·지급·구매 결과가 같다.
수정 소스의 연속 실행과 세 혼합 복원의 최종 digest는 아래와 같다.

`905bc3ff10b887cca0efd83c45dd4a83e72b86b1a845e5d9ac24c3c3a45d0d09`

여섯 RULE 시나리오는 Phase 2 기준선에 대해 summary의 두 측정 시간 필드만 제외하고,
execution과 다섯 JSONL 기록 전체를 비교했다. Phase 4에서 이미 동일했던 결과를 유지했다.
증거는 final/verification.json과 final/mixed-verification.json에 있다.

지도 검증 helper 첫 실행은 Windows 기본 cp949로 Node의 UTF-8 stdout을 해석하다 실패했다.
그 전에 F1/F2와 혼합 실행·복원은 완료됐다. helper에 encoding="utf-8"을 명시한 뒤 지도 부분을
다시 실행해 세 경우를 통과했다. boundaries.log는 이 진단 이력이며 전체 pytest 실패 기록이 아니다.
map-verification.json과 SVG는 수정 후 성공 증거다. 모바일 UI나 실제 모델 호출을 검증했다고 주장하지 않는다.

## 보존 검사와 산출물

보호 대상 870개 파일의 SHA-256을 다시 확인했고 변경/누락은 0개다.
기존 Phase 1~4 계약과 보고서, 공용 parser/prompt/schema, 지도와 핵심 기존 테스트도
시작 스냅샷과 바이트 동일하다. 과거의 전체 74,942개 파일 검사를 이번에 반복했다고 주장하지 않는다.
이전 Phase 4 ZIP은 SHA-256
`7cf82dd9d4f38978f34ceb18e6dd325190bb458b4530e1fe80896e889729b01e`로 유지했다.

새 검토 ZIP: `artifacts/phase4_1/JourneyMap_Phase4_1_review_2026-10-15.zip`.
현재 전체 source/test/config/doc, HEAD_TO_FINAL.patch, 시작 소스 191개 스냅샷,
Phase 4.1 파일별 변경, 이 보고서와 계약, JUnit/정적 검사/실행/지도/UI 증거를 포함한다.
HEAD archive + tracked patch + 새 소스 + 명시적 LF/CRLF 복구로 최종 소스를 재구성하여
파일 목록과 바이트 일치를 검사한다. MANIFEST.json은 자신을 제외한 모든 payload의 크기와
SHA-256을 담는다. ZIP 전체 hash는 옆의 `.zip.sha256`에 제공한다.
재구성한 소스의 src를 PYTHONPATH로 지정한 새 프로세스에서도 WORK 저장본을 tick 24까지
재개해 연속 실행과 digest 동일성을 확인한다. 증거는 reconstruction-resume.json이다.
기존 ZIP, venv/cache/Git 내부, 자격 증명, 과거 연구 raw 원본은 포함하지 않는다.

## 남은 제한과 실행 범위

깊은 JSON은 Python parser의 RecursionError를 안전하게 분류한다. 플랫폼에 관계없는 고정
최대 nesting depth를 새로 보장하는 것은 아니다. 완료 시각은 transport 반환 순간만이 아니라
최종 응답 게시 시각이다. 안전 저장은 worker가 종료되고 owner가 packet을 회수한 경계에서
가능하며 불확정 전송은 계속 거부한다. 소스 hash가 다른 저장본의 자동 migration은 추가하지 않았다.

실제 외부 LLM/API 호출 **0회**, 실제 키 조회 **0회**, 유료 실행 **0회**,
commit/push/merge **각 0회**, subagent 생성 **0회**다. 로컬 loopback UI만 사용했다.
모의 응답과 테스트 성공은 protocol/correctness 증거다. 실제 모델의 판단 능력·비용·생존 성과·
기억 효과 또는 연구 construct validity의 증거가 아니다. 실제 API 실행 승인도 생기지 않는다.
