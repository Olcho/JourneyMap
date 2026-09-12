# M8 single-actor experiment protocol and export

상태: 구현 및 offline gate 완료. 사용자 승인으로 첫 공식 OpenAI trial `m8-first-official-sol`을 한 번 실행해 tick 24와 replay equality를 확인했다. [공식 결과 보고서](M8_FIRST_OFFICIAL_TRIAL.md)에 실측 usage·비용·행동·한계를 기록했다. 추가 유료 trial은 별도 승인 대기다. 이 문서는 M8 실행·전송·기록의 기준이며 M0–M7 도메인 의미를 대체하지 않는다.

## 1. Provider와 모델 설정

- Provider: OpenAI Responses API, 고정 HTTPS `api.openai.com:443`, `POST /v1/responses`.
- 첫 공식 trial 목표: `gpt-5.6-sol`, `reasoning={"effort":"medium"}`, `max_output_tokens=4096`.
- 반복 실험 모델: `--model gpt-5.6-terra` 또는 `JOURNEYMAP_MODEL`. 모델은 외부 설정으로 유지한다.
- temperature/top_p, tools, browsing, previous_response_id, conversation, server memory는 사용하지 않는다. `store=false`.
- `OPENAI_API_KEY`는 실제 generate 시 environment에서만 읽어 Authorization header에 사용한다. import/construct/fake/replay는 key 없이 실행된다. key/header/HTTP error body/임의 exception message는 기록하지 않는다. key echo도 fail-closed한다.
- 표준 라이브러리 `http.client`를 사용하며 SDK/runtime dependency를 추가하지 않는다. redirect와 proxy environment도 사용하지 않는다.

2026-09-12 확인한 [Sol 모델 문서](https://developers.openai.com/api/docs/models/gpt-5.6-sol)는 Responses, structured outputs, medium effort와 alias를 명시한다. dated snapshot은 확인되지 않았으므로 추측하지 않는다. 요청 model과 응답 metadata의 `model`을 별도로 보존하고 manifest `response_models`에 관측된 identity들을 모은다. 응답이 alias만 제공하면 더 구체적인 weights/snapshot identity는 알 수 없다. [Terra 모델 문서](https://developers.openai.com/api/docs/models/gpt-5.6-terra)도 별도로 확인했다. 문서상 지원은 계정 접근이나 실제 endpoint 검증을 대신하지 않는다.

## 2. DecisionCandidate와 authority 경계

```text
trusted actor-scoped perception -> Observation
  -> LLMController prompt + ProviderRequest
  -> Provider -> RawModelResponse.text
  -> strict JSON decoding -> parsed_wire_candidate
  -> REQUEST object codec -> parsed_candidate {action_type,payload}
  -> trusted Observation binding -> final ActionRequest
  -> unchanged M7 normalization + payload validation
  -> existing turn helper -> GamePort.submit -> deterministic handlers
```

모델은 `action_type`, `payload`만 생성한다. `action_request_id`, `run_id`, `actor_id`, `based_on_observation_id`, `submitted_at`, `schema_version`, `correlation_id`를 output에 넣으면 extra field로 거부한다. Controller가 현재 Observation에서 scope/time을 복사하고 ID는 `<observation_id>:llm-v2`, version은 1, correlation은 None으로 만든다. 이는 새 LLM decision에 대한 binding이며 M7 직접 submit의 past Observation reuse와 application idempotency를 바꾸지 않는다.

`adapters.decision_schema.decision_schema()`는 version `decision-candidate-2`의 정확한 wire artifact다. `text.format`은 `type=json_schema`, `strict=true`다. root는 두 필드 object이고 payload는 exact object shapes의 nested `anyOf`다. root `anyOf`는 쓰지 않는다. action type과 payload branch의 대응은 마지막 M7 validator가 다시 확인한다. 범용 JSON-schema validator/중앙 action resolver를 추가하지 않는다.

공식 [Structured Outputs 제한](https://developers.openai.com/api/docs/guides/structured-outputs)에 따르면 root union은 허용되지 않고, 각 object는 지정된 properties만 생성하며 모든 property가 required여야 한다. 따라서 다음과 같이 처리한다.

| 계약 | wire 표현 | M7 의미 |
|---|---|---|
| MOVE/WAIT/ASK/REST/CONSUME/BUY | 기존 exact payload | 변경 없음 |
| INFORM optional reply | reply 없는 object / 있는 object의 두 branch | omission을 유지; null을 omission으로 보정하지 않음 |
| REQUEST arbitrary object | `request_payload_json` JSON string 한 필드 | strict decode 후 `request_payload` object로 손실 없이 복원 |

예: `{"action_type":"REQUEST","payload":{"target_actor_id":"hugh","request_kind":"advice","request_payload_json":"{\"topic\":\"travel\"}"}}`는 `request_payload={"topic":"travel"}`로 구성한다. 이 codec은 arbitrary nested arrays/objects/null/boolean/finite number/string을 보존한다. duplicate keys, NaN/Infinity, invalid UTF-8/surrogate, non-object root는 실패다. 원본 wire object도 별도로 저장한다. JSON string이 engine에 전달되거나 실행되는 일은 없다.

대안 감사: 전체 JSON mode는 schema 제약을 모두 잃으므로 채택하지 않았다. REQUEST payload를 고정 key 목록으로 줄이는 방법은 M7 의미를 바꾸므로 제외했다. recursive key/value AST는 가능하지만 모든 JSON 타입의 별도 encoding/decoder가 필요해 단일 subobject string보다 크다. 이 작은 codec은 runtime repair가 아니며 호출마다 자동 fallback을 선택하지 않는다.

## 3. Prompt, Memory, perception

`adapters.llm.PROMPT`는 `alderwick-decision-2` artifact다. 역할, 탐색/정보/자원 과제, exact action 설명, scope 제한, observation을 evidence로만 취급하는 규칙을 포함한다. 정답 경로, bridge collapse 시각, NPC 미래 activation 순서나 hidden state는 포함하지 않는다. trial horizon은 공개 실행 규칙으로 제공한다. 전체 실제 prompt와 SHA-256, wire schema와 version을 decision마다 저장한다.

첫 protocol은 정확한 `NoMemory`만 허용한다. MemoryContext에는 이미 같은 actor에게 전달된 ordered Observation history만 들어가며 선택 결과가 그 history의 실제 record인지 확인한다. provider prompt의 memory 배열은 항상 빈 배열이다. actor-visible 최근 receipt는 현재 Observation의 새 section이므로 별도 모델 memory가 아니다. 원본 Observation/Knowledge/social history를 삭제하거나 요약하지 않는다.

M8에서만 `create_alderwick_kernel/application(...,social=True,resources=True,experiment=True)`를 선택한다. 기존 M4–M7 composition의 Observation은 그대로다.

- `alderwick/local`: actor 현재 location에 있는 다른 actor ID, 그 location에서 출발하는 route ID/destination/traversal_cost만 positive projection한다. remote route/world graph, passability, hidden route properties, remote actor 위치/자원은 제외한다.
- 기존 bridge/resources/social/knowledge perception을 조합하며 그 의미는 변경하지 않는다.
- `application/last_receipt`: 자기 이전 intent와 M7 `ControllerActionResult` fields만 제공한다. receipt fields는 `action_request_id,run_id,actor_id,status,reason_code,started_at,resolved_at,schema_version`이다. 최신 trace가 다른 actor의 것이어도 자기 trace만 선택한다. raw ActionResult/digest/transition/private diagnostic은 전달하지 않는다.
- 마지막 receipt만 Observation에 요약 투영하지만 모든 ActionTrace와 결과는 Research/export에서 보존한다. History retention 정책 변경이 아니다.

## 4. 시간과 activation

최종 protocol `alderwick-24h-2`: **1 tick = 1 simulation hour, start=0, end=24**. scenario version은 `alderwick/social-resources-24h-1`이다. 기존 초기 world와 social knowledge, route duration, handler를 그대로 재사용한다.

`scenarios.alderwick.experiment.experiment_schedule()`은 bridge collapse tick 3/priority 0 한 건과 SurvivalTick tick 1–24/priority 10을 명시한다. recurring scheduler는 없고 M6 `resources-1` schedule 1–20은 수정하지 않는다. 같은 tick에는 기존 kernel의 `(due_time,priority,insertion_sequence)` 및 system-before-action-completion 순서를 따른다.

| 선택 | 장점 | 한계/결정 |
|---|---|---|
| 20 tick × 72분 | M6 schedule을 그대로 실행 | fixture 범위가 세계 시간 정의를 결정하고 hour 해석이 불편함; 공식 trial에서 제외 |
| 24 tick × 1시간 | tick/hour 일치, 24개 pressure input 명시, 기존 M6 회귀 독립 | 별도 scenario/schedule version 필요; 최종 채택 |

MOVE는 기존 2 tick, social/BUY/CONSUME는 1 tick, WAIT/REST는 payload duration을 전부 소비한다. 이는 첫 연구의 거친 시간 척도이며 현실 여행/거래 소요 시간의 보정된 물리 모델이 아니다. kernel tick이나 resource per-tick 계수를 바꾸지 않는다.

처음 Stranger를 tick 0에서 activate한다. 시간이 진행된 LLM turn 뒤에는 기존 `NPC_ACTIVATIONS=(hugh,thomas,hugh,thomas,hugh)` cycle의 다음 NPC 한 명을 기존 SocialNpcController로 activate하고, 완료 직후 다시 Stranger 차례다. 별도 idle tick을 넣지 않는다. NPC REJECTED는 그대로 기록한다. Marta/Edwin은 기존 passive fixture 역할을 유지한다. system event는 이 activation 목록의 actor가 아니며 ActionRequest로 변환하지 않는다. 긴 action 중에는 기존 synchronous kernel이 system events를 처리하고 NPC activation은 다음 완료 경계까지 기다린다.

끝 tick을 넘는 action은 duration을 줄이지 않고 제출 전에 `HORIZON_ACTION`으로 trial을 종료한다. NPC도 같은 제약을 받는다. 무효 모델 출력/provider 실패/시간이 전진하지 않은 engine rejection 뒤에는 NPC를 실행하지 않고 새 Observation으로 다음 decision 기회를 준다. 따라서 실패 output 자체는 world mutation을 일으키지 않는다.

기본 bounds는 decisions 64, provider calls 64, 연속 무진행/실패 3, wall 300초다. 공식 실행 제안은 decisions/calls 24, wall 240초, HTTP socket timeout 20초다. automatic retry/repair/fallback 없음. 다음 activation은 새 ID·Observation의 새 decision이며 원 실패를 덮어쓰지 않는다. 같은 provider response ID가 다시 오면 거부한다. 다른 response의 동일한 WAIT 의도는 새 bound request로 허용하며 기존 ActionRequest exact retry는 application cache가 처리한다.

Observation 실패, submission/engine 오류, NPC turn 오류는 즉시 종료한다. 실패에도 이미 commit된 world/system 결과는 보존한다. wall clock은 simulation-time termination과 별개이며 다음 decision 및 submit 전 검사다. HTTP timeout은 socket operation 한도이고 arbitrary blocking Python Provider를 강제 kill하는 watchdog은 아니다. 늦게 돌아온 모델 출력은 submit하지 않는다. `COMPLETED`는 tick 24 도달, `TERMINATED`는 별도 stop_reason과 실제 종료 tick을 가진다.

## 5. Research/export v1

`Record`는 detached JSON을 immutable string으로 저장하고 `data`는 매번 새 값을 반환한다. raw response는 invalid surrogate도 JSON escape로 보존한다. engine input은 별도로 canonical UTF-8 검증을 통과해야 한다.

| 파일 | 내용/연결 |
|---|---|
| `manifest.json` | trial/run, 실행 UTC 시각, engine manifest/version/seed/initial digest, git commit+dirty flag+source hash, scenario/schedule/knowledge identity, actor/NPC config, requested/returned models, parameters, prompt/protocol/MemoryPolicy, bounds/counts/time/status/final digest/replay equality, 파일 SHA-256 |
| `decisions.jsonl` | trial/run, decision/activation seq/tick/actor, delivered Observation snapshot, selected memory IDs, full ProviderRequest/prompt/hash, schema/version, raw output, parsed wire candidate, decoded candidate, final request, parse/failure/turn result, public receipt, ActionTrace seq, provider response identity/status/usage/latency, retry policy |
| `activations.jsonl` | LLM 및 NPC 순서, NPC request/public receipt/failure; LLM decision seq 참조 |
| `observations.jsonl` | 모든 성공한 actor-scoped Observation 전체 |
| `observation_attempts.jsonl` | actor/tick/canonical content bytes/overflow; 실패한 oversized 내용은 게시하지 않음 |
| `action_requests.jsonl` | engine에 실제 제출된 요청만, LLM+NPC; rejected/failed 포함, application retry/denial 제외 |
| `action_traces.jsonl` | 모든 live submit attempt와 boundary denial/retry/engine result |
| `action_results.jsonl`, `events.jsonl`, `system_event_outcomes.jsonl` | 전체 deterministic engine 출력 |
| `initial_knowledge.jsonl`, `knowledge.jsonl` | 초기+최종 actor별 전체 history; committed Events로 중간 prefix도 재구성 가능 |
| `initial_state.json`, `final_state.json` | Research 전용 canonical snapshots |
| `replay_input.json`, `engine_report.json` | 기존 ReplayInput 표현과 비교할 전체 ReplayReport |
| `metrics.json` | action type/status, failure/call, bytes distribution, 이동, 자원, Knowledge source, token/latency summary |

비용을 API가 반환하지 않으면 실제 비용은 null이다. subjective intelligence/planning score나 자동 truth 판정은 만들지 않는다. parser failure와 final ActionRequest는 분리되며 parser 예외 메시지는 저장하지 않는다. Observation을 만들지 못한 activation은 snapshot/request/raw가 null이고 provider_called=false다.

기록/export는 canonical mutation 밖에서 실행한다. 새 export directory만 허용하고 기존 trial을 overwrite하지 않는다. 파일 write 실패는 in-memory Record나 이미 완료된 world를 바꾸지 않으며 partial directory를 남긴다. manifest는 마지막 completion marker다. 파일 hashes는 무결성 검사이며 서명/인증이나 crash-safe persistence가 아니다. 동일 Record를 두 번 export하면 byte-identical하다. 서로 다른 실행의 wall timestamp/latency는 같다고 가정하지 않는다.

replay는 기존 ReplayHarness에 schedule+recorded actions+advance_to만 넘긴다. ActionResults, Events, system outcomes, time, canonical state/digest, RNG draw count를 모두 비교한다. Provider/NPC 정책/Observation 재생성은 요구하지 않는다. 과거 `resources-1` 20-tick export도 원래 schedule/version으로 replay 가능하다. 중단된 engine exception은 replay 불일치가 가능하며 equality=false를 숨기지 않는다.

## 6. 실행과 승인 전 데이터 범위

Python 3.12 환경에서 offline 실행:

```text
python -m journeymap.examples.alderwick_llm --trial-id m8-fake-24h-v2 --output trials/m8-fake-24h-v2
python -m journeymap.examples.alderwick_llm --replay trials/m8-fake-24h-v2
```

승인 후에만 실행할 command:

```text
python -m journeymap.examples.alderwick_llm --live --model gpt-5.6-sol --reasoning-effort medium --max-output-tokens 4096 --max-decisions 24 --max-provider-calls 24 --wall-timeout 240 --provider-timeout 20 --trial-id m8-first-official-sol --output trials/m8-first-official-sol
```

전송 body는 `adapters.openai_provider.request_body(ProviderRequest)`로 **network/key 접근 없이** 확인할 수 있다. body에 model/reasoning/token limit/store/schema와 current actor Observation을 포함한 prompt가 들어간다. Observation은 run/actor/observation ID, tick, content digest, 자기 identity/위치/자원/지식·출처/수신 social 이력, 공개 local actor/exits/offers, 자기 최근 intent/public receipt를 포함한다. 별도 memory는 빈 배열이다. World snapshot, Research history/debug, future schedule, RNG, remote graph/private actor state, 소스코드/로컬 경로/환경 변수는 전송하지 않는다. key는 body와 log 밖의 인증 header에만 들어간다. `store=false`는 전송을 하지 않는다는 의미가 아니다.

## 7. 2026-09-12 offline 결과와 예산 계획

`trials/m8-fake-24h-v2`는 tick 24/24h, decisions/provider calls **12**, invalid output/provider failure **0**, replay equality **true**다. 전체 action 22개: SUCCEEDED 20, REJECTED 2, FAILED 0. LLM action 12개는 SUCCEEDED 11/REJECTED 1, MOVE 3/ASK 2/BUY 1/CONSUME 1/INFORM 1/REQUEST 1/REST 3이다. NPC를 포함하면 WAIT도 사용해 frozen 8종 전체를 거친다.

Observation 22개 전체 max **3,875 bytes**, mean **3,058.09 bytes**, LLM 관찰만 max **3,739**, mean **3,209.58**, overflow 0이다. Knowledge 6→10개(INITIAL 6, DIRECT 1, INFORMED 3)로 증가하며 Thomas의 intact/collapsed 충돌은 모두 보존한다. Stranger 최종 hunger 58/fatigue 25/wallet 6/bread 1. fake 경로는 테스트 fixture이며 모델 행동/합리성의 증거가 아니다.

기존 `trials/m8-fake-development2` 20-tick/72분 run과 그 replay는 보존했다. 이전 전체 743 tests를 삭제하지 않고 새 candidate/time 계약에 필요한 assertion만 전환했으며 신규 27개 반례를 추가해 **770 passed**다. M0–M7 기존 656 tests는 변경하지 않았다.

아래 예산은 공식 live 승인 전에 작성한 계획이다. 이전 네트워크 차단 시도(`m8-first-live-20260912`)는 socket 실패 3회, tick 0의 failure artifact로 보존한다. 이후 사용자 명시적 승인을 받아 실행한 첫 공식 trial의 실측치는 [결과 보고서](M8_FIRST_OFFICIAL_TRIAL.md)를 따른다.

예산은 API 호출 없이 fake prompt+schema의 **6,408–7,977 UTF-8 bytes/call**을 측정해 잡은 범위다. tokenizer 실측이 아니며 메시지 framing과 모델 행동에 따라 달라진다. 2.5–4 bytes/token 가정으로 input 약 1.6k–3.2k/call, 10–16 decisions를 계획 범위로 잡으면 input 16k–51.2k이다. medium reasoning을 포함한 output은 미측정이며 계획상 0.5k–2k/call(총 5k–32k), 제한은 4,096/call이다. [Responses 문서](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)는 이 상한에 visible+reasoning tokens가 모두 포함됨을 명시한다.

[Sol 공식 단가](https://developers.openai.com/api/docs/models/gpt-5.6-sol) 입력 $4/1M, 출력 $20/1M을 적용하고 cache 할인 없이 계산하면 계획 범위 **약 $0.16–$0.85**다. 24 calls 모두 4,096 output tokens를 쓰고 input이 위 fake 관측 범위 상단에 머무르면 약 **$2.28**이다. 이는 입력 성장까지 보장하는 USD hard cap이 아니다. NoMemory라도 Knowledge/social history 증가로 current Observation이 커질 수 있다. 실제 usage를 받아야 실비를 확정할 수 있다. Terra 단가는 입력 $2/1M, 출력 $12/1M이며 첫 공식 Sol trial을 자동 대체하지 않는다.

승인 후 공식 trial은 10 calls, tick 0→24, COMPLETED/HORIZON, replay equality=true로 끝났다. M8의 first-live 및 기록/replay exit criteria를 충족했다. 단일 live run의 결과이며 복수 모델·trial 통계 비교는 하지 않았다. commit/push와 추가 유료 trial은 수행하지 않는다.
