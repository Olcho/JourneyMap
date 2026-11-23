# Experiment 04 live preparation (2026-10-09)

이번 작업은 후보 설정의 오프라인 연결 준비다. 유료 호출 승인과 실행 완료가 아니다.
검토 기준 normalized working source는 `79c3aac38bed65735b644427e4c1dd1c6fd3e2cb45abf6f0773a7ac68e03dc86`다.
기존 dirty working tree와 E03, E04 offline v1/v2 소스 및 원본을 보존한다.

## 유지한 연구 계약

[기존 E04 계약](EXPERIMENT04_OFFLINE.md)의 v2 그래프, 정보 경계, 목표 문구,
전체 닫힌 경험, tick 2 checkpoint/tick 3 평가, 첫 최적 MOVE/도착/회복,
6결정/24tick 계약을 사용한다. 첫 점수는 후속 도착으로 바꾸지 않는다.
A의 변경 정보 부재를 일반 추론 결함으로 해석하지 않는다.

smoke는 초기 tick 0 단일 호출만 준비하며 분기와 파일럿 통계가 없다.
파일럿은 초기 시도 6개, 정상 prefix당 새 application 세 개, 최대 114호출이다.
smoke 1회와 파일럿 114회는 각자 plan/digest/gate를 가지며 최대 합계 115회다.
각 초기 시도 p001~p006에 세 조건의 모든 순열을 사전순으로 고정한다.
각 위치에 조건이 두 번씩 배정되며 무작위가 아니다. 실패 번호를 당기거나 A를 재표집하지 않는다.

정상 초기 선택을 실제 GamePort에 제출한 뒤, 새 application마다 의미 의도를 새 Observation 권한에
재결합하여 prefix를 재생한다. provider 호출은 없다. 모든 분기에서 첫 관찰/경험을 한 번 열고
세 첫 wire의 ①② 동일성/③ 통보만의 차이를 검증한 다음, 배정 순서로 한 분기씩 호출한다.
다른 분기 응답/비용/지식은 모델 입력으로 전달하지 않는다.

## 별도 live 연결

`route_update_live.py` adapter는 E04 전용 wire/config/HTTP/mock identity를 사용한다.
새 live Session은 기존 Session의 관찰/archive/검증된 엔진 제출/metrics를 공유하되,
fake-only `finish`를 호출하거나 완화하지 않는다. mock provenance는 `e04-inert-http-mock`,
live는 `openai`다. 둘은 상호 교환할 수 없다. 모델 ID를 offline-fixture로 바꾸지 않는다.
HTTP transport는 고정 Responses HTTPS endpoint에서 1회만 send한다. 키는 send 내부에서만
조회하도록 구현했으며 이번 작업에서는 그 함수 또는 키 조회를 실행하지 않았다.

후보는 gpt-6.1-sol/low, max_output_tokens=2048, timeout=120초, 응답 최대 2,000,000bytes,
반환 exact allowlist=[gpt-6.1-sol], store=false, service_tier=default(Standard)다.
previous_response_id/conversation, reasoning 재주입, sampling/API seed/tools/background/streaming이 없다.
실제 bytes 원문과 hash를 먼저 기록하고 동일 bytes를 send에 전달한다. 인증 헤더는 기록하지 않는다.
응답 UTF-8 원문과 parse/metadata/status/refusal/incomplete/usage, engine receipt/Events/경험/지식을 기록한다.
실제 계정 접근, E04 schema 수용 및 출력 한도 충분함은 확인하지 않았다.

## readiness와 비용 미완료

공식 근거는 2026-10-09 KST 확인했다.
[모델](https://developers.openai.com/api/docs/models/gpt-6.1-sol)은 Low, Structured Outputs와
Standard 단가를 명시한다. 날짜 snapshot ID는 만들지 않았다. alias와 반환 model/실행 시각을 기록한다.
[가격](https://developers.openai.com/api/docs/pricing)은 서비스 범위를 확인하는 근거다.
[Reasoning](https://developers.openai.com/api/docs/guides/reasoning)의 출력 상한은 추론을 포함한다.
2048은 충분함이 검증되지 않은 후보이며 token cap incomplete에서 전체를 중단한다.
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)는
refusal/incomplete가 일반 JSON 성공과 다름을 확인하는 근거다.

[Counting tokens](https://developers.openai.com/api/docs/guides/token-counting)는 로컬 계수의
schema 및 모델별 처리 한계를 설명한다. 여기에는 gpt-6.1-sol의 검증된 offline tokenizer 매핑,
버전과 schema/서비스 envelope의 추가 token 상한 근거가 없다. bytes/문자 나눗셈을 계수로 쓰지 않는다.
실제 계수 API도 호출하지 않았다. `COUNTER_REVIEW.status=INCOMPLETE`와 live `DRAFT`를 유지한다.
**현재 코드에는 LIVE READY 발급기가 없다.** 승인 JSON이나 DRAFT 상태/hash를 수동 편집해도
이 미완료 gate를 우회할 수 없다. 후속 작업은 검증 가능한 사전 계수 구현 및 추가 overhead 근거,
새 소스 검토와 plan 생성이 필요하다. 승인만 추가해 현재 코드를 유료 실행할 수 없다.

USD 10은 smoke+pilot 합계 PROPOSED이며 approved_usd=null이다. 단가는 MTok당 입력2,
캐시입력0.10, 캐시쓰기2.50, 출력10이며, 모든 입력을2.50으로 예약하고 중복 가산하지 않는다.
20,000 입력 token 상한이 신뢰 가능하게 검증된다는 조건에서 호출0.07048,
smoke0.07048+pilot8.03472=8.10520 USD다. 이는 실제 측정 청구액이나 무조건 상한이 아니다.
세금/환율/지역 추가요금/특수 계약은 포함되지 않았고 계정별 적용 범위는 후속 검토가 필요하다.

mock은 서비스 계수와 무관한 synthetic-mock-only-1 계약(기본 입력1000/출력100)을 쓴다.
그 usage와 예약액은 예산 로직 검증만을 위한 합성 수치다. live readiness 증거가 아니다.
Ledger는 매 send 전 최대액을 예약하고 모든 최대 예약을 끝까지 유지한다.
usage 비용은 전 입력을 최고 입력 단가로 잡은 보수적 추정값이며 invoice가 아니다.
확정 usage 추정액과 미확정 최대 예약액을 따로 보고한다. timeout 또는 usage/tier 범위 오류에서
예약을 지우지 않고 중단한다. 사후 usage 확인만으로 사전 금액 보장을 주장하지 않는다.

## 실행 gate 및 중단

계획은 stage, source/runtime, v2 profile/schema/config digest, 전체 순서, 호출/예약 한도,
모델·가격·계수 근거와 중단 규칙을 동결한다. readiness와 authorization은 별도다.
source/profile/config/mode/output 불일치와 잘못된 순서/상한/승인 digest는 transport 생성 전 거부한다.
실제 경로는 exact human approval digest/stage/call cap/금액 cap/새 output이 필요하고,
현재 추가로 live 계수 미완료에 의해 차단된다. 이번에 승인 파일을 만들지 않았다.
Git checkout을 전제로 source_identity를 사용하며 standalone ZIP runner를 추가하지 않았다.

실행 시작 시 `trials/e04-plan-claims/<digest>.json`을 exclusive 생성하여 같은 계획 재실행을 막는다.
output을 지워도 claim은 남는다. retry/fallback/resume/실패 대체가 없다.
유효한 오답/WAIT/정상 Engine REJECTED/FAILED는 기회 내에서 계속한다.
refusal/malformed/일반 incomplete는 해당 초기/분기를 종료한다.
max_output_tokens incomplete, infrastructure, identity/integrity, 기록 실패, usage/비용 오류는 전체 중단한다.
입력/비용/호출 한도는 발송 전 중단이며 모델 오답이 아니다.
SEND는 durable send intent다. HTTP 도달/실제 과금의 증명으로 쓰지 않으며 미확정 예약을 보존한다.
기록이 끊기면 durable prefix만 재생하고 마지막 검증된 첫 점수와 unknown/null 최종 결과를 유지한다.

## 보고 및 audit 범위

A/B 층을 분리하고 각 조건의 판정 가능한 첫 선택 분모, 검증된 최종 분모, 회복 가능 분모를 표시한다.
A의③/② 대응 true/false/null 네 조합과 null 쌍을 따로 보고한다.
계획/실제 순서 및 미시도/정상 prefix 없음도 표시한다. 미시도 score를0으로 채우지 않는다.
raw branch snapshot에는 모든 결정/제출/이동/WAIT/경과 및 초과 tick이 보존된다.
smoke/과거 E03/다른 mock 또는 live와 합산하지 않는다. 6prefix/18분기/114호출을 독립 표본으로 해석하지 않는다.

audit/inspect는 Provider, key, network와 Git source lookup 없이 원문을 새 application에 재생하여
wire/receipt/Event/position/metrics/비용·예약 및 전체 순서와 lifecycle을 비교한다.
Session/metrics/계획·비용 계산 함수는 실행기와 공유한다. 독립 평가 알고리즘의 증명은 아니다.
별도 source/runtime gate와 ZIP manifest로 소프트웨어/원본 보존을 검사한다.

## 오프라인 명령

Python 3.12와 src가 PYTHONPATH에 설정된 실제 Git checkout에서 다음 명령을 사용한다.
출력은 모두 기존 경로와 다른 새 경로여야 한다. 검증 scripts는 별도 trials 폴더에 포함한다.

```powershell
python -m journeymap.examples.route_update_live plan trials/e04-smoke-draft.json --stage smoke --output trials/e04-smoke-candidate
python -m journeymap.examples.route_update_live plan trials/e04-pilot-draft.json --stage pilot --output trials/e04-pilot-candidate
python -m journeymap.examples.route_update_live mock trials/e04-http-mock-smoke --stage smoke
python -m journeymap.examples.route_update_live mock trials/e04-http-mock-pilot --stage pilot
python -m journeymap.examples.route_update_live audit trials/e04-http-mock-pilot
python -m journeymap.examples.route_update_live inspect trials/e04-http-mock-pilot
python -m pytest tests/test_route_update_live.py
python -m ruff check .
python -m ruff format --check .
python -m mypy
python -m pytest
```

`live <new-output> --plan <frozen-plan> --authorization <human-record>`와 `smoke` 명령은
실제 실행 경로의 gate만 구현되어 있고 현재 DRAFT에서 차단된다. 이번 검증에서는 둘 다 실행하지 않는다.
완료물과 실제 검사 결과는 [완료 보고서](EXPERIMENT04_LIVE_PREPARATION_REPORT_2026-10-09.md)를 확인한다.


## 2026-10-09 후속 계약: counted protocol v2

이 절은 앞선 v1의 항상 차단되는 token gate와 기준선 누락을 후속 구현에서 대체한다. 과거 보고서와 raw는 소급 수정하지 않는다. CLI 기본 새 기록은 execution/export/audit v2이며 legacy v1 감사도 유지한다.

### 경로와 코드 기준선

Readonly validate_plan은 output_path를 원본 출처 문자열로 유지하고 resolve/existence를 요구하지 않는다. audit는 전달받은 bundle 아래의 raw만 읽으며 원본 plan hash/config/profile/source 바인딩을 그대로 검증한다. native preflight는 현재 source와 정확한 새 output 위치를 요구한다. 각 정상 prefix의 세 입력을 공통 검사한 뒤 OLD-MAP/RECEIVED-UPDATE를 semantic input만으로 계산한다. input·최적 첫 route 전체·선택·lexicographic tie-break·첫 이동 평가 범위를 export와 summary에 남긴다. 미시도 모델 분기도 policy_computed와 model_called를 구분한다. audit에서 새 세계·관찰을 만들어 정책을 다시 계산한다.

### 계수 계약과 비용

공식 count endpoint는 POST https://api.openai.com/v1/responses/input_tokens. 생성 endpoint는 POST https://api.openai.com/v1/responses. 투영은 model/input/text(strict schema 포함)/reasoning을 동일하게 유지한다. store/max_output_tokens/service_tier는 count reference 허용 필드가 아니어서 제외하고 timeout/response byte cap/returned_models는 transport 설정으로 분리한다. count와 생성은 각각 timeout=120, max_response_bytes=2000000이다. 생성 설정은 gpt-6.1-sol / low / max_output_tokens=2048 / exact returned_models=[gpt-6.1-sol] 그대로다. engine seed=42, 기존 fixed balanced 6순서를 유지한다.

공통 입력 검사→COUNT_INTENT(요청 bytes와 generation digest durable 저장)→COUNT_SEND→COUNT_RESPONSE→엄격 object/input_tokens 검증→COUNT_RESULT→생성 비용 예약→INTENT/SEND→응답/결과. 한 관찰을 공유하며 count 중 tick/행동/모델 입력 변화는 없다. 각 생성 기회마다 count 단 1회, count cache/retry/fallback/resume 없음. 음수/bool/잘못된 object/초과 20,000/transport 실패는 전체 중단하며 생성하지 않는다. count 뒤 wire 변경과 반환 usage.input_tokens 불일치도 전체 중단한다. raw와 미확정 예약은 보존한다.

상한은 smoke count 1 + generation 1 = network 2, pilot count 114 + generation 114 = network 228, 합계 count 115 + generation 115 = network 230. count는 행동 표본이 아니다. count-only smoke는 1+0=1이며 해당 승인으로 생성 gate가 열리지 않는다. 별도 scope, endpoint, count/generation/network caps, frozen digest, 금액을 exact 승인해야 한다. 발송 직전에 현재 source/plan도 재확인한다.

2026-10-09 공식 guide/reference/pricing 재검토: count 서비스는 문서화되어 있으나 이 계정·모델의 reasoning/schema 수용 및 count=usage는 실제 미검증이다. 별도 count fee의 명시적 단가는 확인하지 못하여 UNKNOWN, 무료라고 가정하지 않는다. USD10은 PROPOSED, 승인 금액=null. 기존 8.10520은 입력 20,000·출력 2048과 보수적 입력 $2.50/M·출력 $10/M에 따른 생성 115회의 조건부 계산이며 count 포함 총액이 아니다. Mock fee 0.00010은 테스트용 합성 예약 단위일 뿐이다.

공식 근거: [guide](https://developers.openai.com/api/docs/guides/token-counting), [reference](https://developers.openai.com/api/reference/resources/responses/subresources/input_tokens/methods/count), [pricing](https://developers.openai.com/api/docs/pricing). guide의 메시지 formatting count와 reference의 reasoning/schema 필드 지원을 투영에 반영했다. 실제 모델 수용 여부는 후속 승인 smoke에서 확인해야 한다.

### 미래 smoke 준비 명령과 승인 구분

현재 과제에서 아래 첫 두 plan 준비만 수행하며 실제 smoke/계수·생성/key lookup은 수행하지 않는다. 새 경로는 존재하지 않아야 하고 부모는 존재해야 한다.

```powershell
python -m journeymap.examples.route_update_live plan trials/NEW/draft-smoke-plan.json --stage smoke --mode live --output trials/NEW/smoke
python -m journeymap.examples.route_update_live plan trials/NEW/draft-pilot-plan.json --stage pilot --mode live --output trials/NEW/pilot
```

기본 결과는 DRAFT, 서비스 UNVERIFIED·count price UNKNOWN, UNAUTHORIZED. 향후 실제 count 요금/상한에 대한 외부 검토 JSON(출처·검토일·상한·근거 원문/hash)을 확보한 뒤 `plan ... --price-review COUNT_PRICE_REVIEW.json`으로 새 smoke 후보 READY_SERVICE_CHECK를 만든다. 이것은 서비스 확인 완료가 아니라 확인을 위한 후보다. 별도 사용자의 count+generation 1+1·endpoint·금액·digest exact 승인 기록이 있어야 `smoke ... --plan FROZEN_PLAN.json --authorization NEW_HUMAN_AUTHORIZATION.json`으로 1회씩 발송 가능하다. count-only는 plan에 --count-only를 붙이고 1+0 scope로 승인하며 generation은 금지된다. 실패 뒤 다시 발송하지 않는다.

승인된 smoke 완료 이후 `review-smoke SMOKE_BUNDLE --output NEW_SERVICE_REVIEW.json`으로 저장 raw를 무호출 재검증한다. 서비스 확인용 witness는 live mode/승인/요청/schema/reasoning/valid response/count=usage/ledger/source를 재생하며 mock이나 수동 status flag는 거부한다. Pilot 새 plan에는 `--price-review ... --service-review ...`가 모두 필요하다. source/runtime가 witness와 다르면 거부한다. 실제 서비스와 가격 증거 없이 이 과제에서 smoke 승인이나 live READY 파일을 만들지 않는다.

Witness와 audit는 외부 서명이 없는 내부 일관성 증거다. hash 전체를 재작성한 forged raw가 실제 서비스 응답인지 암호학적으로 입증하지 못하며 SEND도 전달/청구 완료의 증명이 아니다. Provider/key/network/Git 없는 replay와 별도 현재 source gate를 구분한다.


## 2026-10-10 제한된 smoke 예외: v3 SMOKE_COUNT_FEE_UNKNOWN_V1

이 절은 최신 합의에 따른 **별도 smoke 한 번의 준비**다. 과거 v1/v2의 의미와 기본 v2 단가 확정 요구를 유지한다. 새로운 명시적 옵션을 선택한 경우에만 execution/export/audit v3를 만들며, runner/auditor/행동 엔진은 공용 경로를 재사용한다. 실제 발송 승인은 별도이고, 이번 준비에서 실제 API·키 조회·외부 전송은 0회다.

허용 조합은 smoke, count-and-generate, 초기 prefix 1, count cap=1, generation cap=1, network cap=2. count-only/pilot/다른 모델·endpoint·늘어난 상한에는 적용하지 않는다. gpt-6.1-sol / low, 입력 20,000 이하, max_output_tokens=2048, 각 timeout=120 / max_response_bytes=2000000, exact returned_models=[gpt-6.1-sol], service_tier=default, store=false, 기존 strict E04 schema/초기 goal/prompt를 유지한다. tick3 세 분기는 생성하지 않으며 smoke를 pilot 초기 표본으로 재사용하지 않는다.

계수 요금 상태 UNKNOWN, count_fee_upper_bound_usd=null, count_charge_estimate_usd=null, combined_cost_upper_bound_usd=null, total_cost_bound_guaranteed=false. 생성 부분의 조건부 최대 예약만 USD0.07048이다(입력20,000/출력2,048과 검토된 보수적 단가). 이는 전체 청구 상한이 아니다. 과거 USD10이나 USD8.10520을 이번 smoke의 승인 금액 또는 총액으로 재사용하지 않는다. mock 예외도 같은 unknown/null 의미를 사용한다. Ledger는 generation_* 금액 소계와 미확정 계수 시도·SEND 수를 분리하고, 실패/미수신에도 이를 지우지 않는다. v3에는 총액으로 오해할 budget_cap_usd/combined_reserved_usd를 출력하지 않는다.

```powershell
# 존재하는 부모 아래 새 파일·새 실행 경로. 기본 v2는 이 옵션을 쓰지 않는다.
python -m journeymap.examples.route_update_live plan trials/NEW/smoke-plan.json --stage smoke --mode live --smoke-count-fee-unknown --output trials/NEW/smoke --authorization-template trials/NEW/authorization-template.json
# 독립 mock correctness만; 실제 서비스 증거가 아니다.
python -m journeymap.examples.route_update_live mock trials/NEW/mock-smoke --stage smoke --smoke-count-fee-unknown
python -m journeymap.examples.route_update_live audit trials/NEW/mock-smoke
python -m journeymap.examples.route_update_live inspect trials/NEW/mock-smoke
```

후속 최종 승인 때만(이번에는 실행하지 않음):

```powershell
python -m journeymap.examples.route_update_live smoke trials/NEW/smoke --plan trials/NEW/smoke-plan.json --authorization trials/NEW/authorization-approved.json
```

계획 상태 READY_SERVICE_CHECK는 검토 가능한 코드 후보를 뜻하며 실제 서비스 확인 또는 발송 승인이 아니다. plan.authorization은 UNAUTHORIZED, 템플릿은 PENDING_USER_APPROVAL/accept_count_fee_unknown=false다. 실제 승인 파일은 HUMAN_APPROVED/accept_count_fee_unknown=true와 live/smoke/count-and-generate, 정확한 digest/output/cost policy/endpoints/1/1/2 상한, generation reservation0.07048, 계수 UNKNOWN/null 총액/total bound false, retry false/pilot false/additional execution false를 exact canonical JSON 타입으로 검증한다. source/runtime/config는 plan digest로 묶고 승인 확인 후 실제 요청 직전 current gate에서 다시 대조한다.

입력 준비 1회→count body와 COUNT_INTENT/SEND durable 기록→count1회→결과/상한검사→동일 generation wire 예약/INTENT/SEND→generation1회→결과/usage로 끝난다. count 실패/형태/크기/상한 초과이면 생성하지 않는다. Wire 변경·source 변경·usage 불일치·기록 실패를 비롯한 오류, refusal/incomplete/유효 WAIT·초기 행동 실패 이후 원하는 MOVE를 얻으려 다시 호출하지 않는다. 자동 retry/fallback/resume/new digest/new output 재실행 없음. Claim은 지우지 않는다.

Readonly v3 감사는 저장 live 승인과 plan의 연결, 비용 정책·null·상한, raw count/gen wire·응답·일회성 바인딩·usage·생성 소계·초기 행동과 종료를 재생한다. Provider/키/network/current Git 없이 실행하고 저장 Windows output_path를 resolve하지 않는다. legacy offline v1/v2/live v1/counted v2 raw는 그대로 읽고 과거 판정을 유지한다.

`review-smoke`는 v3의 정상 live-format raw/정확한 승인/count/schema/usage를 재생하고 cost_review에 UNKNOWN/null 의미를 함께 저장한다. 이는 기술적 연결 확인이며 가격 확인이 아니다. unsigned 내부 일관성으로 서버 응답 진위를 증명하지 않는다. 단위 테스트의 synthetic live-format 기록은 실제 서비스 증거가 아니며 실제 후보와 함께 서비스 witness로 포장하지 않는다. 기본 pilot에는 여전히 별도 계수 가격 근거와 별도 사용자 승인이 필요하다. Pilot용 비용 불확실성 예외는 구현하지 않는다.

이번 사용자 환경의 최종 plan/runtime/source/digest/정확한 PowerShell 경로와 승인 문장은 `trials/experiment04-limited-smoke-preparation-20261010/SMOKE_APPROVAL_REQUEST.md`에서 확인한다.

## 2026-10-10 별도 제한 pilot 준비: v4

명시적 `--pilot-count-fee-unknown`만 `PILOT_COUNT_FEE_UNKNOWN_V1`을 선택한다. v3 smoke 1/1/2와 기본 v2 가격 gate, 과거 원본 의미는 유지한다. 새 초기 시도 p001~p006과 기존 6가지 조건 순열, 각 분기 6회/24tick 및 engine seed42를 유지한다. 생성114/count114/network228은 시도 상한이고 성공 표본 수가 아니다. 기존 smoke 응답은 표본으로 재사용하지 않는다.

생성 부분 조건부 예약 USD8.03472=114*0.07048만 기록한다. 계수 가격/상한/청구액은 UNKNOWN/null이고 전체 비용 상한=null, total_cost_bound_guaranteed=false다. 예약은 실패 뒤 환급해 호출 수를 늘리지 않는다. USD10 총액 보장이나 추가 실행 승인이 아니다.

v4는 실제 v3 smoke witness와 이번 전환에 한정된 호환성 기록을 요구한다. 기록은 검증된 이전 ZIP/source 전체 inventory, 원래 witness bytes/digest, before/after 변경 목록과 hash, 보호 파일 전체 목록, 이전/현재 실제 smoke 및 A/B 후속 저장 응답 재생의 입력/wire/행동/평가를 묶는다. current preflight는 현재 전체 source/runtime를 별도로 검사한다. readonly audit는 저장 source·증거로 재구성하며 현재 Git/원본 Windows 경로/Provider/key/network에 의존하지 않는다. unsigned 내부 일관성으로 서비스 진위나 모든 프로그램 동등성을 증명하지 않는다.

서비스 증거 RECORDED_ACTUAL_V3_SMOKE, 코드 호환성 VALIDATED_V3_TO_V4_UNSIGNED, count 가격 UNKNOWN, 최종 승인 PENDING_USER_APPROVAL을 분리한다. READY_FOR_REVIEW는 발송 허가가 아니다. 실제 발송에는 새 frozen plan과 exact HUMAN_APPROVED 승인이 별도로 필요하다. 이번 준비에서 실제 API 및 키 조회0회, 새 actual output/claim/승인 파일은 생성하지 않는다.

정확한 계약은 [제한 pilot 준비](EXPERIMENT04_LIMITED_PILOT_PREPARATION.md)를 따른다. 과거 절의 미실행/미구현 상태는 당시 이력이며 소급 재작성하지 않는다.