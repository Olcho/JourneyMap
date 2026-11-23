# Experiment 04 제한 smoke 준비 완료 보고서

2026-10-10 KST. `JourneyMap_Experiment04_Limited_Smoke_Codex_2026-10-10.md`에 따라 비용 불확실성 예외 구현, 오프라인 검증, 실제 로컬 후보 동결과 승인안 작성을 완료했다. 실제 API/계수/생성 호출 0회, 실제 키 조회 0회, 외부 전송 0회다. commit/push/merge를 하지 않았다. 방향 변경의 추가 승인을 요청하지 않았으며 실제 발송 승인은 받거나 만든 것으로 기록하지 않았다.

## 기준과 보존 범위

작업 위치 `C:\Users\<user>\.codex\worktrees\experiment03-live-preparation\JourneyMap`. HEAD `557fb3df10149d7b7738137b7ac1038dfba24e84`, branch `codex/experiment03-live-preparation`을 유지했다.
시작 normalized working source `05a5823a94db68b99b8dcb229b9c7b03a9531b8080202bb43797db5d43f583c8`와 일치했다. 이전 미커밋 60개는 이번 신규 변경이 아니다. before-code에 그 원문 60개를 보존했다.
최종 normalized source `94150b4f5a635a8931f297249a3306f094a77d8df43435911a9157be9eb5de94`, raw source `39a63f1fd721bff3c5ba85c4f669584618f8ee63695276ee1a75b963ff57dc67`. 범위 src/journeymap/**/*.py+pyproject.toml, Python3.12.14를 사용했다.
기준 ZIP SHA-256 `55226db92ad3f839a9857cd119b4d41521c29f7f44b35726853f244b73e8a305`도 직접 시작 상태와 대조했다.

이번 수정은 기존 source7개와 문서5개 append, 신규 정책 helper/test/본 보고서3개다. 전체 현재 변경은 63개(tracked6, untracked57)이며 이전 60개를 전부 이번에 새로 만들었다고 보고하지 않는다. Source7개는 counted plan/cost/runner/audit/service, live audit dispatcher, CLI이며 HTTP adapters·engine·지도·Observation·Event Memory·prompt/schema·goal·기준선·파일럿 배정은 변경하지 않았다.

보존 검사는 시작에 캡처한 4,015개 파일에서 의도적으로 수정한12개를 제외한4,003개와 이전 변경 원문60개에 한정한다. 대상은 변경 코드/문서, 과거 trials raw/보고서/claim/ZIP 등이다. 중복 payload/extracted 사본, cache, pytest 임시 폴더, 금지 경로는 시작 캡처에서 제외했으며 이들을 전부 검증했다고 확대하지 않는다. Git HEAD/status와 전체 tracked diff, 검토 working 기준 이번 task diff 및 목록을 별도로 남긴다. 이전 raw 및 ZIP은 덮어쓰지 않았다.

## 구현

새 명시 정책 `SMOKE_COUNT_FEE_UNKNOWN_V1`, 실행/plan/export/audit version3. CLI `--smoke-count-fee-unknown` 옵션 또는 make_plan의 명시 cost_policy 인수에서만 선택된다. 기본 v2 계획과 legacy v1/v2의 의미는 그대로다. 새 범용 비용 프레임워크나 복제한 runner/auditor는 만들지 않고 기존 실행·행동·감사 lifecycle을 공유한다.

허용 조합: smoke/count-and-generate, initial prefix1, count1/generation1/network2. count-only/pilot/증가한 cap/모델·endpoint 변경은 scope gate로 거부한다. 모델 gpt-6.1-sol/low, max_output_tokens2048, 입력count<=20,000, timeout 각120초, max_response_bytes 각2,000,000, exact returned_models=[gpt-6.1-sol], default tier/storefalse/기존 strict E04 초기 goal와 prompt를 유지한다. tick3 분기를 만들거나 pilot으로 이어가지 않는다.

비용은 count_price_status=UNKNOWN, count_fee_upper_bound_usd=null, count_charge_estimate_usd=null, combined_cost_upper_bound_usd=null, total_cost_bound_guaranteed=false다. 생성 부분의 조건부 예약액만 USD0.07048이다. 과거 USD10을 이번 승인 금액으로 재사용하지 않으며 UNKNOWN을0/0.00010/임의 상한/무한대/무료로 바꾸지 않는다. Ledger 생성과 매 예약에서 정확한1/1/2 범위를 확인한다. v3 snapshot은 generation_* 소계와 count intent/SEND 수·UNKNOWN을 분리한다. budget_cap_usd/combined_reserved_usd 같은 총액 보장으로 보일 필드는 v3에서 출력하지 않는다. 알려진 생성 소계 비교만 Decimal로 계산하며 count 금액은 덧셈에서 숨기지 않는다.

최종 승인은 frozen digest로 source/runtime/model/config까지 묶고 mode/stage/scope/output/endpoints/caps/cost policy/generation reservation/unknown acceptance/null 총액/false guarantee/retryfalse/pilotfalse/additionalfalse를 strict canonical JSON으로 대조한다. bool과 int/float를 같은 값으로 취급하지 않는다. 승인 확인 후 실제 요청 직전 current source/plan을 다시 검증한다. 누락/PENDING/미수용/과거 금액 승인/다른 hash·path·policy는 발송 전에 거부한다.

기존 한 관찰·한 wire를 준비→durable COUNT_INTENT/SEND→count1→결과검증→동일 생성 wire 비용 예약/INTENT/SEND→generation1→원문/usage/결과로 종료한다. count 실패/invalid/한도/크기 초과는 생성0, count/usage 또는 wire/source 불일치는 종료한다. WAIT·초기 행동 실패·refusal·incomplete·timeout·recording 오류에도 추가 호출 없이 raw·미확정 비용·claim을 보존한다. 자동 retry/fallback/resume/new output/new digest 재실행은 없다.

v3 readonly audit는 저장 live 승인까지 포함해 비용정책·null·범위·raw 바인딩·usage·ledger 소계·초기 행동·종료를 재생한다. 키/Provider/network/current Git 없이 원래 출처 경로 문자열을 유지한다. review-smoke는 v3 기술적 서비스 확인 witness에 cost_review UNKNOWN/null 의미를 함께 유지하고 이를 가격 확인으로 승격하지 않는다. 단위 fixture의 synthetic live-format 기록은 서비스 사실이 아니다. unsigned raw/hash가 서버 응답 진위를 증명할 수 없다는 한계를 명시했다. 기본 pilot의 가격 근거·별도 승인 요구는 유지하며 pilot용 unknown 예외는 구현하지 않았다.

## 검증 결과

| 검사 | 결과 |
| --- | --- |
| 신규 집중 회귀 | 42 passed in12.93초 |
| 전체 pytest | 1,567 passed in258.53초, failure/error/skip0, 최종 전체 실행1회 |
| Ruff check/format | 통과 |
| strict mypy | 통과,178 source/test files |
| git diff HEAD --check | 통과, 기존 EOL 경고는 로그 보존 |
| 새 v3 mock 원본 | 6개(정상초기MOVE, validWAIT, counttimeout, countlimit, generationtimeout, usagemismatch) |
| 정상 mock smoke | count1+generation1=network2, 분기0, fee와 총액상한null |
| count 실패/한도초과 | generation0, UNKNOWN 시도기록 보존 |
| generation 실패/usage불일치 | 추가 호출0, raw/예약/usage 유지 |
| live 경계 | 실제 HTTP/키/socket 차단, 합성 transport와 pytest 임시 승인만 사용 |
| 승인/범위/source/wire | 엄격 타입/변조·미수용 거부, 승인 뒤 source변경과 일회성 wire 검사 통과 |
| witness | synthetic live-format 검증과 UNKNOWN 보존, mock/가격승격/승인누락 거부, pilotDRAFT 유지 |
| legacy 원본 | 이전22개+counted v2의21개=43개를 한 번씩 재감사, 저장 결과와 exact 일치, raw hash 보존 |
| 새 사본 audit | 집중 테스트에서 별도 사본/Path.resolve 금지 상태 감사 통과 |
| 로컬 후보 CLI | 실제 plan 생성과 PENDING 승인 거부 확인, 실행dir/claim미생성 |

기존 engine/E03/E04 연구 핵심 불변식은 전체 회귀로 확인했다. Mock 또는 synthetic live-format 결과를 실제 LLM 행동·실제 서비스 확인·효과 입증으로 해석하지 않는다. 과거43개 raw는 재생성/재봉인하지 않았고 이동 사본을 새로43개 복제하지 않았다. 새 ZIP에는 검토할 원본 한 벌만 포함한다.

개발 중1회 실패는 source-change fixture가 readonly plan 재구성까지 바꾸어 KeyError를 만든 테스트 문제였다. Current source 제공 함수만 변경하도록 fixture를 고친 뒤 집중42개와 전체1,567개가 통과했다. 개발 실패 로그는 final gate와 구분했다. 환경 전체 dump/인증정보가 포함된 로그는 생성하지 않았다. 실제 키 조회를 차단했고 pytest의 기존 HTTP unit tests에는 자식 프로세스의 가짜 key만 사용했다.

## 실제 로컬 후보와 미완료 승인

Plan: `C:\Users\<user>\.codex\worktrees\experiment03-live-preparation\JourneyMap\trials\experiment04-limited-smoke-preparation-20261010\smoke-plan.json`
Plan SHA-256: `bd5e66be8865631774a1c735cac2182a17d3e989ca70148f32df4714d7e1f305`
출력 경로: `C:\Users\<user>\.codex\worktrees\experiment03-live-preparation\JourneyMap\trials\experiment04-limited-smoke-preparation-20261010\live-smoke` (부모만 존재, 실행dir 없음).
Python: `C:\Users\<user>\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe` /3.12.14.

Plan 상태 READY_SERVICE_CHECK는 검토 가능한 코드 후보다. 실제 발송 UNAUTHORIZED, 서비스 확인 UNVERIFIED이며 계수 가격 UNKNOWN이다. `C:\Users\<user>\.codex\worktrees\experiment03-live-preparation\JourneyMap\trials\experiment04-limited-smoke-preparation-20261010\authorization-template.json`는 PENDING_USER_APPROVAL/accept_count_fee_unknown=false다. HUMAN_APPROVED 파일은 만들지 않았다. 후보의 claim은 미소비이고 live output도 없다. Pending template으로 실제 로컬 smoke CLI를 호출한 검사는 preflight 거부까지만 진행했고 actual HTTP/key/socket을 차단했다. 이 검사가 실제 smoke 실행은 아니다.

정확한 실행 조건, 사용자 승인 문장 초안, plan/source/runtime/hash/output과 승인 후 사용할 Windows PowerShell 명령은 [SMOKE_APPROVAL_REQUEST.md](../trials/experiment04-limited-smoke-preparation-20261010/SMOKE_APPROVAL_REQUEST.md)에 실제 값으로 채웠다. 현재 방향 변경 승인을 실제 발송 승인으로 기록하지 않았다. 별도 count-only 확인이나 pilot 불확실성 예외는 없다.

생성 예약액USD0.07048은 기존 보수적 입력20,000·출력2,048·단가로 계산한 생성 부분의 조건부액이다. 계수비용UNKNOWN이므로 전체 비용 상한은 미확정이다. 현재 실제 usage/청구 추정액은 없고 mock 값은 합성이다. 모델/HTTP request는 이 비용정책 때문에 바꾸지 않았다. 기존 공식 문서 검토 근거를 유지하고 이번에는 외부 전송이나 가격 확인 API를 수행하지 않았다.

## 산출물과 ZIP

증거: `trials/experiment04-limited-smoke-preparation-20261010`의 source-before/final, implementation-contract, candidate plan/PENDING template/승인안, raw6개, audit/inspect, offline/historical/candidate/static/preservation/final gate, JUnit 및 로그, offline_cli/run_checks/verify_offline/prepare_candidate/final_check/package_review scripts. 최종 raw와 승인안을 생성한 뒤 실행 source는 수정하지 않았다. Linux native 실행은 하지 않았고 같은 호스트 사본 감사/경로 해석 금지 테스트 범위와 구분한다.

새 ZIP은 `trials/review-bundles/experiment04-limited-smoke-<새 UTC>` 경로에 생성한다. 전체 src/tests/docs, 변경·신규63개 원문과 이전60개 원문, Git 상태와 full tracked/task diff, 본 계약/보고서/후보/승인안, mockraw6개와 기존43개 원본 한 벌 및 비교표, 검증 scripts/logs/gates를 포함한다. 과거 ZIP 본문이나 중복 relocated 사본을 중첩하지 않는다. API key/인증 헤더값/환경dump/.env/.venv/.git/cache/pytest 임시 synthetic 승인·witness/진행중 packaging log를 제외한다.

Manifest는 파일별 상대경로·크기·SHA-256을 기록한다(자체hash는 외부verification). ZIP CRC·경로·credential pattern, 압축해제payload=bytes/hash=현재원본, 보존scope4,003개와 기존ZIP/raw불변, HEAD/status/diff불변을 확인한다. 검증범위를 manifest/preservation 기록 밖의 모든 파일로 확대하지 않는다. 실제 ZIP 경로/hash와 검증 기록은 완료 응답과 외부 verification에 남긴다.
