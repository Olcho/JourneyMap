# Phase 4.2 · 생활 Responses 연결 사전 검증과 실행 제안

2026-10-15. Phase 4.1 독립 검토는 F1/F2를 통과로 판정했다. 이번 단계는 다음 지시인
생활용 Responses 요청 형식·응답 분류의 오프라인 검증과 구체적인 실행 계획 고정이다.
실제 모델 실행과 키 조회는 포함하지 않는다. 기본 RULE과 기존 offline/UI/checkpoint는 유지한다.

## 현재 구현의 경계

adapters.life_responses는 기존 생활 prompt/schema와 request_body를 재사용하는 별도 wire adapter다.
구체적인 RecordingResponsesTransport만 허용한다. HTTP status와 JSON envelope를 합성해
body·raw·usage·분류·회계를 검증한다. 임의 transport와 kind=live는 거부한다.
network transport, credential 코드, 실행 승인 flag 또는 유료 실행 CLI는 없다.
이는 실제 연결의 wire/accounting 사전 검증이며 실제 연결 실행 가능 판정은 아니다.

MixedLifeSession에는 fixture Provider로 주입한다. 기존 service에 보존되는 offline 요청 envelope와
새 adapter의 제안 wire body는 서로 다른 기록이다. 후자는 wire_records에 따로 남긴다.
RawModelResponse.metadata에 wire_model과 synthetic-recording-transport를 명시하고 live로 위장하지
않는다. 모의 bridge로 실제 엔진의 행동 접수·완료와 무재호출 replay까지 연결한다.
custom Provider checkpoint 저장은 기존 계약대로 거부한다. live checkpoint를 추가하지 않았다.

## 고정 실행 제안

모델과 상한은 사용자의 승인을 받기 위한 제안이며 현재 허가가 아니다.
개발 도구의 Sol / High 설정과 세계 안에서 사용할 아래 API 모델은 별개다.

| 항목 | 고정값 |
| --- | --- |
| 계획 identity | life-responses-preflight-1 |
| 계획 digest | 89447928f2357efef57f0f9cbc9ce8a53beced16cdb7a5f6136301d34b95380d |
| API 모델 | gpt-5.4-mini-2026-03-17, alias 대체 금지 |
| endpoint / tier | POST https://api.openai.com/v1/responses / service_tier=default |
| reasoning | high |
| 세계 / 인물 | seed 42, 기존 5명 세계, Ada만 Provider, 나머지 4명 RULE |
| 기간 / 시작 | 새 실행 tick 0~24, 불확정 유료 요청 resume 금지 |
| 요청 상한 | 전송 시도 최대 32회, 동시 요청 1개, 실패 시도 포함 |
| 출력 | max_output_tokens=4096, reasoning 포함, text 최대16,384 UTF-8 bytes |
| 입력 | 전체 body 최대65,536 UTF-8 bytes, 현재 Observation만 |
| 누적 token caps | input 500,000 / output 131,072; cached⊆input, reasoning⊆output |
| 비용 상한 | USD 1.00, generation 요금 기준, 세금·환전 제외 |
| 단가 / 1M tokens | input $0.75 / cached input $0.075 / output $4.50 |
| timeout | 시작~최종 packet 게시 120초, Phase 4.1 정각 허용/late 폐기 |
| 실패 | refusal/incomplete/파싱/identity/tier/usage/전송/크기 오류 첫 발생에 중단 |
| 엔진 실패 | rejected/failed 별도 기록, 기존 연속 무효 3회 중단 |
| 재시도 / 대체 | 자동 retry, JSON 수리, RULE/WAIT fallback 없음 |
| 도구 / 대화 / 저장 | tools·previous_response_id·추가 memory 없음, store=false |

모델의 Responses/Structured Outputs, high reasoning, snapshot과 400,000 context는
[공식 모델 문서](https://developers.openai.com/api/docs/models/gpt-5.4-mini)를 2026-10-15에 확인했다.
같은 문서의 위 단가를 고정했다. 계정별 모델 접근·rate limit·미래 가격은 확인하지 않았고,
실제 실행 직전에 다시 확인해야 한다. 변경이면 계획도 새로 검토한다.
service_tier=default는 [공식 API reference](https://developers.openai.com/api/reference/python/resources/responses)의
standard 설정이다. 다른 응답 tier면 중단한다. regional/Fast/Batch/Flex 단가는 사용하지 않는다.

## 요청과 응답

body는 model/input/store/text/reasoning/max_output_tokens/service_tier 일곱 필드만 사용한다.
생활의 life-observation-decision-1 prompt와 life-candidate-1 schema는 변경하지 않는다.
text.format은 json_schema, strict=true, root object와 nested payload anyOf이며 객체마다
required/additionalProperties=false를 유지한다. [Structured Outputs 계약](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)을
대조한 wire 검증이다. 실제 서버 schema 접수 성공은 확인하지 않았다.
행동과 payload의 교차 조합은 기존 life_candidate의 엄격한 로컬 검증으로 거부한다.
authority ID, 세계 truth, 타인 private 자원이나 미래 사건은 모델 input에 없다.

HTTP 200 이후 envelope는 strict JSON으로 읽어 중복 key/깊이 예외/잘못된 형식을 거부한다.
model은 pinned ID, tier는 default여야 하며 response ID는 비어 있지 않은 string이어야 한다.
usage의 input/output/total/cached/reasoning은 boolean이 아닌 음이 아닌 정수이며 부분집합과 합을
검사한다. assistant message의 output_text/refusal만 해석하고 tool output은 거부한다.
reasoning item은 행동으로 읽지 않는다. hidden chain-of-thought나 summary는 요청하지 않는다.
completed만 기존 후보 parser로 넘기며 refusal/incomplete는 원문·상태를 보존해 중단한다.

4096은 최적값이라는 주장이 아니다. reasoning 때문에 visible JSON 전에 상한에 도달해
incomplete가 될 수 있다. 이때도 사용량과 비용이 생길 수 있으며 재시도하지 않는다.
[공식 reasoning 문서](https://developers.openai.com/api/docs/guides/reasoning)는 max_output_tokens가
reasoning과 visible output을 함께 제한하고 reasoning이 output으로 과금됨을 설명한다.

## 한도와 usage 기록

부동소수점 대신 nano-USD 정수로 계산한다. 전송 전에 한 회 입력을 모델의 전체 context
400,000 tokens로 보수적으로 예약한다. body bytes를 실측 token 수라고 해석하지 않는다.
context 전체와 출력4096을 각각 상한으로 잡아 한 요청에 $0.318432를 예약한다.
잔여 호출·입력·출력·비용 중 하나라도 예약을 수용하지 못하면 전송 전에 중단한다.
실제 작은 입력이라도 잔여 예산이 이 예약보다 작으면 일찍 멈출 수 있다.

검증된 usage가 도착하면 reported input/output/cached 수로 예약을 정산한다.
output_tokens에 reasoning을 다시 더하지 않는다. cached input만 할인 단가를 적용한다.
명시한 누적 token caps를 전부 uncached로 계산한 generation 상한은 $0.964824다.
이는 모델 context/출력 상한과 고정 standard 단가가 지켜진다는 조건의 상한이며 예상 소비액이 아니다.
서버 과금서 검증이나 세금·환전까지 보장하지 않는다. 실제 paid 사용량은 이번 작업에서 0이다.

usage 누락/오류, HTTP 오류, timeout/전송 불확정, 응답 초과 크기, usage의 예약 초과는 예약을
해제하지 않고 terminal stop으로 남긴다. 추가 요청은 없다. usage가 유효한 refusal/incomplete,
identity/tier 오류도 사용량을 정산한 뒤 멈춘다. 오류별 wire reason은 별도 기록이며 기존 fixture
service의 transport-error 분류와 구분한다. accounting은 worker의 전송 메타데이터다.
canonical mutation/시간/행동 접수는 계속 owner만 한다.

현재 JSON의 token 수와 rate-card 금액은 **합성 fixture 값**이다. 실제 사용량이나 비용 추정치가
아니다. 누적 usage/예약/stop도 network transport와 최종 연결할 때 owner에게 전달·기록해야 한다.
불확정 유료 요청의 crash recovery/자동 재개는 이번에 구현하지 않는다.

## 오프라인 실행과 다음 승인 경계

```powershell
Set-Location C:\Users\user\Documents\JourneyMap\.worktrees\phase1
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_preflight --output artifacts/phase4_2/my-preflight.json
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m pytest tests/test_life_responses.py -q --basetemp=artifacts/phase4_2/my-test-temp
```

output은 새 파일이어야 한다. live/key/approval 옵션이 없고 계획 digest는 허가 token이 아니다.
실제 호출과 키 조회에는 이 계획과 당시 소스에 대한 별도 사용자 승인이 필요하다.
그 뒤 승인에 결합된 신뢰된 HTTP composition, credential redaction, durable attempted-call 기록,
usage journal, timeout/late 회수 경계를 연결·오프라인 검증해야 한다.
기존 OpenAIProvider는 연구 schema를 사용하므로 그대로 생활용 연결로 간주할 수 없다.
현재 단계는 그 최종 transport/admission 연결을 했다고 주장하지 않는다.
과거 연구/M8 승인, key 존재, 테스트 성공이나 이 문서 첨부만으로 실행하지 않는다.

검증·보존 범위와 ZIP은 [완료 보고서](PHASE4_2_REPORT_2026-10-15.md)를 따른다.
