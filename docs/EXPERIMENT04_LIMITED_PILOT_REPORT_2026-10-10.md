# Experiment 04 제한 pilot 준비 완료 보고서 — 2026-10-10 KST

v4 PILOT_COUNT_FEE_UNKNOWN_V1 구현과 오프라인 검증을 완료했다. 새 초기 시도6개, 생성114/count114/network228, 생성 부분 조건부 예약 USD8.03472에 한정한다. 실제 API/count/generation 호출0회, 실제 키 조회0회, 외부 전송0회다. 기존 smoke는 이미 소비되었고 표본·승인·예약에 재사용하지 않는다. 새 HUMAN_APPROVED 파일이나 실제 pilot output/claim을 만들지 않았다. 남은 실행 요건은 아래 정확한 frozen plan에 대한 별도 최종 사용자 승인이다.

**최종 plan digest `feb9ca0f63e8b1368bac8ec3a72239b9afba381cac341462a390b4f35018a021`**
정규화 source `ee2ab56b42567a5c263b7f46223891a0255d61c68e8c945d470c5885cbda24c9`. Runtime Python3.12.14, 지정 CPython 실행 파일. HEAD `557fb3df10149d7b7738137b7ac1038dfba24e84` / branch `codex/experiment03-live-preparation` 유지. 승인안: trials/experiment04-limited-pilot-preparation-20261010/PILOT_APPROVAL_REQUEST.md. plan READY_FOR_REVIEW/authorization UNAUTHORIZED, 템플릿 PENDING_USER_APPROVAL/accept_count_fee_unknown=false. 정확한 출력은 `C:\Users\<user>\.codex\worktrees\experiment03-live-preparation\JourneyMap\trials\experiment04-limited-pilot-preparation-20261010\live-pilot`이며 현재 없다.

## 구현과 변경 범위

새 제한 pilot helper와 이번 v3→v4 호환성 helper를 추가하고 기존 counted plan/cost/audit, live audit dispatcher, CLI만 수정했다. Source 변경은 source-change-inventory.json의 정확한7개(기존5+신규2)에 한정한다. runner는 변경하지 않았다. core/scenario/profile/schema/model input/count projection/HTTP/decode/session/상태 복원/metrics/평가 등 과거 inventory의 보호133개 파일은 byte 동일하다. 코드에 새 source hash를 자기참조로 넣지 않았다.

기본 v2의 가격 gate와 기존 canonical 생성/검증 출력, v3의 smoke1/1/2 제한을 유지한다. v4는 명시적 옵션에서만 pilot/count-and-generate, 고정 p001~p006 allocation와 6순열, exact model/config/seed/6회/24tick 및 상한을 strict canonical JSON 타입으로 검사한다. old smoke 승인/pending/누락/다른 digest/path/source/runtime와 bool-int 혼용은 키/HTTP 생성과 actual output/claim 전에 거부한다. 최종 approval은 소스/runtime/model/endpoints/증거digest를 포함한 plan digest에 묶인다. 실제 CLI는 frozen plan과 별도 승인만 받는다.

v4 ledger는 UNKNOWN count 금액을 null로 보존하고 generation_* 소계를 분리한다. 실패·기록 중단 뒤 시도와 예약을 환급하지 않는다. 114*0.07048=8.03472는 생성 부분의 조건부 예약일 뿐이다. count 요금/charge/상한과 combined 상한은 미확정이며 total_cost_bound_guaranteed=false. USD10 전체 보장이나 이미 끝난 smoke 합산은 없다.

서비스 상태는 RECORDED_ACTUAL_V3_SMOKE, 코드 호환성 VALIDATED_V3_TO_V4_UNSIGNED, 가격 UNKNOWN, 발송 승인 PENDING으로 분리했다. 유효 witness가 있어도 가격 때문에 현재 연결 증거를 UNVERIFIED로 숨기지 않는다. 현재 서비스 판정은 v4 readiness의 service_evidence/source_compatibility 및 원래 witness/호환성 digest를 따른다. 상속한 model_support_review/count_projection/counter_review의 이전 검토 문구는 당시 검토 이력으로 보존하며 현재 발송 승인으로 승격하지 않는다. READY_FOR_REVIEW는 발송 허가가 아니다.

## 호환성 근거

기준 승인 smoke ZIP SHA256 38ceb2a093e6ffcd4ce91595ceedae917e18efcbf6f76377cf7ac9d4cf2e7279 및 manifest 전체를 검증하고 과거 source138개를 별도로 확보했다. 과거 source 정규화94150b4f5a635a8931f297249a3306f094a77d8df43435911a9157be9eb5de94, 원래 plan bd5e66be8865631774a1c735cac2182a17d3e989ca70148f32df4714d7e1f305, witness 파일b055fd4039409191a2774b5f63a50d27bcfad98d317dc79f99025567a47a168d/내부41b9c6830cdc00ea3186527b001a7a1d7579901d911b72e739bd863e28bcac38를 서로 구분해 pin했다. 과거 witness 안의 source/hash/status를 수정하지 않았다.

Compatibility digest `1d58052e39c0b35092b37ecde131db7c929431175f62780446438dcef53d856c`. 기록은 과거 full source와 새 full source hash/runtime, 변경별 before/after hash·원문/전체 보호 inventory 및 이전/현재 의미 재생을 포함한다. 실제 smoke prepared semantic input/generation wire/count wire와 raw decode/Engine 결과가 일치한다. A/B 각 세 조건과 후속 경험 누적·첫/최종 평가, WAIT/비적격/잘못된 prefix를 합한9개 재생 case가 일치했다. 과거 source 별도 Python subprocess와 현재 source에서 같은 저장 텍스트를 재생했으며 Provider/HTTP를 호출하지 않았다.

v2/v4 동일 fake 흐름에서도 prefix/후속 모델 입력·행동·평가·코드 기준선·summary가 일치했다. 버전이 달라 생기는 parent export record_sha256 메타데이터만 비교에서 분리했다. 이 hash 차이를 행동 차이로 해석하지 않았다. readonly 감사는 저장된 source 자료와 증거로 재구성하며 현재 Git/Windows 원본 경로를 읽거나 resolve하지 않는다. 실제 발송 current gate만 전체 native source/runtime/path를 검사한다. 이동 사본에서 Git·Path.resolve 금지 감사도 통과했다.

호환성 증거는 unsigned 내부 검증이며 서버 진위나 일반적인 프로그램 동등성 증명은 아니다. Session/metrics를 공유한다. 실제 서비스 witness만 생산 후보에 연결하며 test fixture/mock를 서비스 증거로 승격하지 않는다. tests/fixtures의 자료는 원래 실제 증거 및 과거 source의 테스트 사본으로, 새 호출 결과가 아니다.

## 직접 실행한 검증

- 신규 집중 회귀40 passed. 개발 단계의 typing/비교 보조 라벨/버전 parent digest 비교 문제를 수정한 뒤 통과했다. 초기 개발 실패 로그를 유지한다.
- 최종 전체 pytest **1607 passed**, 550.043초, errors/failures/skips0. 최종 전체 실행1회.
- Ruff check/format, strict mypy(181 source/test files), git diff HEAD --check 통과.
- 새 v4 recording-mock 원본6묶음: 정상 A/B 6prefix/18분기, WAIT 비적격 prefix, countlimit, counttimeout, generationtimeout, usagemismatch. 각 raw/감사 및 정상 CLI inspect를 보존했다. mock는 프로토콜 correctness만 증명한다.
- 115번째 count/generation 및 229번째 network 기회 차단, 실패/기록 오류 뒤 예약 유지, unknown/null 비용, wire drift/usage mismatch/stop, exact 승인/보호 inventory/재봉인된 의미 변조를 검사했다.
- 기존43 + v3mock6 + 실제v3smoke1 = **50개 원본**을 한 번씩 재감사하여 저장 보고와 exact 일치했다. INCOMPLETE/EXCLUDED 판정도 원래대로 유지했다. 기존 raw를 재생성하거나 재봉인하지 않았다.
- source/runtime/호환성 최종 식별 뒤 정식 plan CLI와 pending 거부 CLI를 HTTP/socket/key 차단 상태에서 실행했다. 후보 actual output/claim과 새 actual approved 파일은 여전히 없다.

Linux native 실행은 수행하지 않았다. Windows 호스트의 이동 사본/원래 Windows provenance 문자열 감사와 구분한다. 모델 API 호출/추가 smoke/실제 pilot/효과 비교/통계 검정은 하지 않았다. A와 B의 진단적 차이, 첫 선택/직접 발견 후 회복/최종 도달/시간/호출 수를 그대로 별도 기록하는 설계다.

## 보존과 제출

기존 사용자 WIP를 reset/stash/clean/delete하지 않았고 commit/push/merge하지 않았다. 명시 보존 inventory에서 의도한 기존 source/docs10개 변경만 제외해 원본 size/SHA256을 확인했다. 실제 smoke raw8개, witness/plan/승인/소비 claim 및 기준 ZIP 모두 불변이다. 보존 scope는 preservation-gate.json에 명시하며 수천 파일 검사 자체를 연구 성과나 모든 파일시스템 불변 보장으로 확대하지 않는다.

새 ZIP에는 현재 src/tests/필요docs와 미추적 변경 원문/전체tracked diff, 기준 source·manifest와 변경 대조, 계약/보고서, compatibility/frozen plan/PENDING 승인안, 새mock6개, 기존50개 raw 한 벌 및 저장/재감사 보고, scripts/logs/JUnit/gates를 포함한다. 원본 service witness/actual claim을 증거로만 포함한다. 과거ZIP 자체, .git/.venv/cache/pytest임시폴더/.env/키/인증헤더값/환경dump는 제외한다. 파일별 상대경로·크기·SHA256 manifest와 ZIP hash/압축해제payload=현재원본 검증은 새bundle 외부 verification에 남긴다. Source는 최종 plan 동결 후 변경하지 않았다.
