# API Contracts

M7에서 확정한 Python in-process 0.1 계약이다. HTTP/JSON transport는 구현하지 않는다. 아래 JSON은 실제 공개 필드의 표현이며 snake_case를 사용한다. 7–12절은 M1–M6의 도메인·실행 의미를 설명하고, live retry와 Controller 경계는 13절의 M7 정책을 따른다.

## 1. Game / Controller interface

`Controller.decide(observation: Observation) -> ActionRequest`는 Observation 하나만 받아 행동 의도를 반환한다. trusted 호출자는 actor가 고정된 `GamePort.observe()`와 `GamePort.submit(request) -> ControllerActionResult`를 사용한다. Controller에 kernel, ResearchView, 다른 actor GamePort, persistence, scheduler, RNG 또는 module mutation handle을 전달하지 않는다.

### ActionRequest v1 envelope

| 필드 | 정확한 live 계약 |
|---|---|
| `action_request_id` | run/application 내 요청 identity; 비어 있지 않은 내장 str |
| `run_id`, `actor_id` | 비어 있지 않은 내장 str; Game binding과 일치해야 함 |
| `based_on_observation_id` | 실제 같은 run/actor history에 존재하는 Observation의 ID |
| `submitted_at` | bool을 제외한 내장 int, 0 이상; 새 요청은 현재 simulation tick |
| `action_type` | 비어 있지 않은 내장 str; 대소문자 변환 없음 |
| `schema_version` | bool을 제외한 양의 내장 int; action pair의 version |
| `payload` | 내장 dict, 문자열 key와 canonical JSON 값만 허용 |
| `correlation_id` | 기본 None; 제공하면 비어 있지 않은 내장 str |

추가 envelope constructor 인자는 허용하지 않는다. live submit은 frozen dataclass여도 nested payload와 envelope를 다시 검증하고 복사한다. None/dict/임의 객체는 직접 Game.submit에서 고정 메시지 TypeError, 정규화 불가능한 ActionRequest는 `GameSubmissionError("INVALID_REQUEST")`다. 이 경우 ID·ActionTrace sequence·engine을 소비하지 않는다. turn helper는 둘 다 안전한 실패 record로 처리한다.

```json
{
  "action_request_id": "request-1",
  "run_id": "run-alderwick",
  "actor_id": "stranger",
  "based_on_observation_id": "run-alderwick:observation:00000001",
  "submitted_at": 0,
  "action_type": "MOVE",
  "schema_version": 1,
  "payload": {"route_id": "west-gate-to-village-square"},
  "correlation_id": null
}
```

### 0.1 actor action v1 전체 목록

`S`는 비어 있지 않은 내장 문자열, `I`는 bool을 제외한 양의 정수다. 모든 top-level payload는 아래 필드만 허용한다. required 누락, 추가 필드(utterance 포함), 잘못된 타입을 거부한다. 비정규 JSON, cycle, NaN/Infinity, UTF-8로 표현 불가능한 값도 live normalization에서 거부한다.

| Action / version | exact payload | 소유자 |
|---|---|---|
| MOVE / 1 | `{route_id: S}` | movement |
| WAIT / 1 | `{duration: I}` | core |
| ASK / 1 | `{target_actor_id: S, subject_ref: S, predicate: S}` | social |
| INFORM / 1 | `{target_actor_id: S, claim_record_id: S, reply_to_event_id?: S}` | social |
| REQUEST / 1 | `{target_actor_id: S, request_kind: S, request_payload: object}` | social |
| REST / 1 | `{duration: I}` | survival |
| CONSUME / 1 | `{item_id: S, quantity: I}` | survival + inventory |
| BUY / 1 | `{offer_id: S, quantity: I}` | trade + inventory |

REQUEST의 nested object는 임의 canonical JSON 구조를 담는 전달 데이터이며 실행·자동 fulfillment·world mutation capability가 아니다. INFORM의 optional reply는 생략 가능하지만 명시적 null은 허용하지 않는다. 해당 Module을 composition에서 활성화해야 action을 실행할 수 있다.

`OBSERVE`는 actor action이 아니라 별도 `GamePort.observe()` read다. 시간 진행·due system dispatch·canonical mutation이 없으며 성공한 Observation history만 추가한다. `TALK`, `OBSERVE` handler를 추가하지 않았다. `availableActionTypes`, 자연어 publicSummary, ISO 날짜 기반 tick 등 과거 기획 예시는 실제 계약이 아니므로 제거했다.

### ControllerActionResult

공개 필드는 정확히 `action_request_id, run_id, actor_id, status, reason_code, started_at, resolved_at, schema_version=1`이다. state_digest_after, transition, emitted_event_ids, handler_id와 exception detail은 Research에만 존재한다.

```json
{
  "action_request_id": "request-1",
  "run_id": "run-alderwick",
  "actor_id": "stranger",
  "status": "SUCCEEDED",
  "reason_code": null,
  "started_at": 0,
  "resolved_at": 2,
  "schema_version": 1
}
```

REJECTED는 시작/authority 검증 거부, FAILED는 duration 경과 후 완료 조건 실패, SUCCEEDED는 성공 transition이다. FAILED의 경과 시간과 독립 system commit은 유지한다. 과거 Observation 자체는 만료 사유가 아니며 handler가 현재 truth를 다시 검증한다. `STALE_OBSERVATION` 코드는 사용하지 않는다.

## 2. Provider / Memory interface

`adapters.provider.Provider.generate(ProviderRequest) -> RawModelResponse`는 최소 Protocol이다. ProviderRequest의 필드는 `prompt, prompt_version, model, configuration_version, parameters={}, schema_version=1`, RawModelResponse는 `text, metadata={}, schema_version=1`이다. 설정·응답 JSON은 construction에서 canonical 검증·복사한다. 모델 설정의 허용 값과 raw metadata의 기록 정책은 M8 adapter가 소유한다. metadata를 actor에게 자동 전달하지 않는다. Provider에 GamePort/Research/World capability를 제공하지 않는다.

`adapters.memory.MemoryPolicy.select(MemoryContext) -> tuple[Observation, ...]`도 최소 Protocol이다. MemoryContext는 `current, prior=()`만 가지며 prior는 같은 run/actor의 증가하는 sequence, 비역행 tick, current 이하 tick 및 current보다 이전 sequence를 가진 v1 Observation이어야 한다. 실제 합법적으로 전달한 기록만 넣는 것은 trusted caller의 책임이다. output은 이 prior의 부분집합이어야 하며 새로운 사실·다른 actor context를 만들지 않는다. `NoMemory.select`는 항상 빈 tuple을 반환한다.

M8 LLMController는 Provider/MemoryPolicy/parser를 사용해 ActionRequest를 만들 예정이다. M7에는 실제 LLMController, API 호출/SDK, prompt engineering, tokenizer, retry/rate-limit framework, reflection/vector memory가 없다. 이 Protocol들은 Python capability 계약이며 악성 Python 코드의 closure/private-field introspection sandbox가 아니다.

## 3. System/Event interface

Controller에 노출되지 않는 실제 engine API다.

```text
SimulationKernel.schedule(ScheduledEventSpec) -> ScheduledEvent
SimulationKernel.advance_to(tick) -> tuple[SystemEventOutcome, ...]
SystemEventRegistry.get(event_type, schema_version) -> SystemEventHandler
SystemEventHandler.validate(event, ValidationContext)
SystemEventHandler.resolve(event, ResolutionContext) -> TransitionPlan
```

ScheduledEvent는 versioned scenario input이며 ActionRequest로 변환하지 않는다. actor registry와 다른 경로로 검증하지만 canonical commit은 공통 deterministic mutation boundary를 사용한다. Event에는 source kind/ref, tick, causation/correlation, transition과 ordering을 남긴다. Controller가 이 권한을 갖지 않는다.

## 4. Research / Debug interface

권한 있는 trusted 호출자만 별도 ResearchView를 받는다. 현재 실제 read 표면은 `world_snapshot, manifest, simulation_time, state_digest, rng_snapshot, observations, knowledge_history(actor_id), action_traces, action_results, events, pending_scheduled_events, system_event_outcomes`다. mutable JSON은 저장 상태와 분리된 복사본이다.

ResearchView 자체에는 world mutation·schedule·advance·replay 실행 method가 없다. scenario 구성과 진행은 trusted application 호출자가 kernel을 사용하고 replay는 별도 ReplayHarness가 담당한다. 과거 snapshot query, 네트워크 credential/API, export/영속 resume, controller invocation DB는 M7에 구현하지 않았다.

## 5. 권한 매트릭스

| 기능 | Controller | Engine Module | Research/Debug |
|---|:---:|:---:|:---:|
| 자기 Observation 읽기 | yes | scoped | yes |
| ActionRequest 제출 | yes | handler | test only |
| raw World Truth 읽기 | no | owned/scoped | yes |
| 다른 actor Knowledge 읽기 | no | knowledge rules only | yes |
| scheduler future item 읽기 | no | scheduler only | yes |
| canonical state 직접 변경 | no | 검증된 handler + 공통 mutation 경계만 | scenario harness도 ScheduledEvent handler 경유 |
| 전체 Event log 읽기 | no | subscription only | yes |

## 6. 계약 규칙

- 모든 request/record는 run과 schema version을 명시한다.
- live 동일 request ID의 exact retry는 13절에 따라 기존 결과를 반환하고 중복 실행하지 않는다.
- v1은 strict payload이며 등록된 type/version pair만 사용한다. 암묵적 migration은 없다.
- 에러 문자열이 아니라 machine-readable reason code를 실험 데이터로 사용한다.
- Observation과 actor-visible ActionResult에 debug detail을 섞지 않는다.
- API 직렬화 순서는 결과 의미에 영향을 주지 않지만 digest 계산 시 canonical serialization을 사용한다.

## 7. M1 내부 커널 계약

M1은 외부 transport나 완성된 Game API를 구현하지 않는다. 다음 Python in-process 계약만 제공한다.

```text
SimulationKernel.schedule(ScheduledEventSpec)
SimulationKernel.advance_to(logical_tick) -> SystemEventOutcome[]
SimulationKernel.submit_action(ActionRequest) -> ActionResult
ReplayHarness.run(ReplayInput) -> ReplayReport
```

M1 `ActionRequest`는 run/actor/request identity, 필수 `based_on_observation_id`, 제출 tick, action type/schema version, canonical JSON payload와 correlation을 가진 최소 replay envelope이다. M1은 이 필드를 비어 있지 않은 opaque reference로 보존해 actor intent가 Observation에 기반한다는 계약을 약화하지 않는다. 실제 Observation envelope·perception·저장소는 M3 범위이므로 M1은 가짜 Observation을 만들거나 참조의 존재·actor 권한·staleness를 검증하지 않는다. 구체 action payload와 M7 live 정책은 1절·13절에서 확정한다.

ScheduledEvent는 versioned ScenarioSchedule의 system input이며 `SystemEventRegistry`로만 전달된다. 같은 문자열 type을 ActionRegistry와 SystemEventRegistry에 각각 등록할 수 있지만 두 dispatch path는 교차하지 않는다. 두 handler는 validation과 resolution 동안 canonical state를 직접 받지 않고 복사본과 transactional RNG만 받으며, 성공한 TransitionPlan만 공통 mutation 경계에서 적용된다.

## 8. M2 구현 계약

M2에서 확정했고 M7에서도 보존하는 Python in-process 실행 의미는 다음과 같다.

| 항목 | 구현 계약 |
|---|---|
| MOVE v1 | `action_type="MOVE"`, `schema_version=1`, `payload={"route_id": str}`. 비어 있지 않은 route ID 하나만 허용; destination 표현·추가 field 거부 |
| WAIT v1 | `action_type="WAIT"`, `schema_version=1`, `payload={"duration": int}`. 양의 정수 tick만 허용; bool·float·0·음수·추가 field 거부 |
| actor | 두 action 모두 canonical Entity 존재 필요. WAIT에는 position이 필요하지 않음 |
| Route | origin → destination 단방향. 반대 이동은 별도 route ID로 명시 |
| 시간 | 제출 tick까지 세계 진행 → start validate/prepare → duration 동안 세계 진행 → 완료 validate → resolve/commit |
| 동일 tick | 완료 tick의 모든 due system event가 먼저, action completion은 그 뒤 |
| WAIT 결과 | duration 소비, 성공 transition/ActionResult 기록. 자체 domain mutation/Event 없음 |
| MOVE 성공 Event | `ActorMoved` v1, payload는 `actor_id`, `route_id`, `origin`, `destination`. 완료 tick에 기존 envelope/provenance로 기록 |

M2의 `entity_type`은 설명용 metadata이며 actor 자격 enum이 아니다. 특정 type만 MOVE/WAIT하도록 제한하지 않는다. MOVE에는 해당 Entity의 유효한 ActorPosition이 필요하다. `origin == destination`인 명시적 Route도 양의 정수 cost를 가지면 허용하며, 그 cost를 소비하고 ActorMoved를 기록한다. 초기 builder는 중복 identity와 location 참조를 검사하지만 Entity 존재는 action handler가 검사한다.

`TimedActionHandler`는 기존 handler에 `prepare -> ActionTiming(duration, data)`와 `validate_completion`을 추가하는 선택적 계약이다. 시작 준비에는 RNG가 없고, 완료 resolve는 최신 상태와 transactional RNG를 받는다. `TransitionPlan`은 변경하지 않았다.

`ActionResult`는 기존 request/handler identity, state digest, emitted event IDs에 다음 필드를 추가한다.

| 필드 | 의미 |
|---|---|
| `status` | `SUCCEEDED`, 시작의 `REJECTED`, 완료 조건 실패의 `FAILED` |
| `reason_code` | 성공은 None, 예상된 실패는 안정적인 코드 |
| `started_at` / `resolved_at` | action 시작과 결과 기록 tick. REJECTED는 두 값이 같음 |
| `transition` | 성공은 기존 TransitionIdentity, REJECTED/FAILED는 None |

실패 결과는 request ID로 연결하고 transition/event ID를 소비하지 않는다. 시작 거부는 제출 tick에서 상태·clock·RNG·scheduler·domain Event를 그대로 둔다. 제출 tick까지 이미 진행된 세계는 유지한다. 완료 실패는 경과 시간과 system commit을 유지하며 action의 성공 mutation/Event만 적용하지 않는다.

M2 reason code: `INVALID_PAYLOAD`, `INVALID_DURATION`, `UNKNOWN_ACTOR`, `INVALID_ENTITY`, `MISSING_POSITION`, `INVALID_POSITION`, `UNKNOWN_ROUTE`, `INVALID_ROUTE`, `UNKNOWN_LOCATION`, `INVALID_LOCATION`, `WRONG_ORIGIN`, `ROUTE_IMPASSABLE`, `INVALID_TRAVERSAL_COST`, `INVALID_PASSABILITY`, `ROUTE_CHANGED`.

Trusted kernel 경로에서 예상 밖 engine/handler 오류, unknown registry key, 잘못된 run ID와 역행 제출 tick은 기존처럼 예외다. 자동 재시도·idempotency·중단된 action의 재개는 구현하지 않았다. kernel 자체는 Observation 참조를 검증하지 않으며 M3 live Game 검증은 아래 절을 따른다. ReplayInput은 그대로이며 다음 요청 tick과 최종 advance tick은 이전 action 완료 tick보다 작을 수 없다.

handler는 엔진 내부의 신뢰된 Python 코드다. 제공되는 context 복사본과 public mutation API 재진입 방지는 canonical 상태 보호 계약이며, private 속성 접근이나 임의 Python 실행을 격리하는 보안 sandbox는 아니다. `ActionValidationError`를 결과로 변환하는 단계는 시작 validate/prepare와 완료 validate이며, resolve/commit의 예외는 전파한다. commit 뒤 subscriber 오류에서는 이미 기록된 성공 ActionResult와 Event를 유지하고 나머지 delivery를 중단한다.

## 9. M3 구현 계약

HTTP API 없이 Python in-process port를 제공한다. run당 하나의 `create_application(kernel, initial_knowledge=(), pipeline=None)`이 `(SimulationApplication, ResearchView)`를 반환한다. kernel boot/close와 scenario schedule/advance는 trusted 호출자가 관리하며 trusted turn caller에는 `application.game_for(actor_id)`의 반환값을 주고 Controller.decide에는 Observation만 전달한다. GamePort에 actor 선택, kernel, ResearchView, registry 또는 store를 주입하지 않는다.

| 표면 | 실제 계약 |
|---|---|
| `GamePort.observe()` | 현재 actor/tick의 Observation을 성공적으로 조립한 뒤 history에 append하고 반환. 시간·canonical state·지식은 변경하지 않음 |
| `GamePort.submit(ActionRequest)` | binding의 run/actor/Observation 권한과 `submitted_at == current tick` 검증 후 기존 actor handler 경로로 전달 |
| `ControllerActionResult` | `action_request_id, run_id, actor_id, status, reason_code, started_at, resolved_at, schema_version=1`만 포함 |
| Game에 없는 정보 | kernel ActionResult의 state digest, transition/handler identity, emitted Event IDs, raw diagnostics와 전체 Event log |

기본 Observation의 `content.sections`는 `{module_id, contributor_id, content}` object의 ordered list다. bootstrap은 `(0, core, self)`, `(10, movement, position)`, `(20, knowledge, records)`를 등록한다. 기본 section content는 각각 `{self: {entity_id, entity_type}}`, `{position: {location_id}}`, `{records: [actor-owned KnowledgeRecord JSON...]}`다. position이 없으면 빈 object이며 route/다른 actor 위치는 제공하지 않는다. 사용자 지정 pipeline은 기본 pipeline을 대체하며 신뢰된 composition에서만 등록한다.

PerceptionContext의 확정된 필드는 `run_id: str`, `actor_id: str`, `simulation_time: int`, `perceived: JsonObject`, `known: JsonObject`다. perceived와 known은 perception 이후의 safe JSON이며 raw World Truth나 query capability가 아니다. contributor의 입력과 출력은 각각 canonical 검증 후 분리한다. 순서는 `(priority, module_id, contributor_id)`이며 M7에서는 priority와 무관하게 동일 `(module_id, contributor_id)` identity를 거부한다. invalid/noncanonical 출력 또는 callback 오류에서는 partial Observation이나 sequence gap을 만들지 않는다.

Observation envelope은 `observation_id, run_id, actor_id, observation_sequence, simulation_time, schema_version, content, content_digest`다. sequence는 run-local 성공 generation 순서로 1부터 증가하고 ID는 `<run_id>:observation:<sequence:08d>`다. schema는 기본 1이며 tick은 정수다. digest는 **content만** 기존 canonical JSON(key 정렬, compact UTF-8, finite JSON 값) SHA-256으로 계산한다. envelope은 frozen이고 content는 내부 문자열에서 매번 새 값을 반환한다. history는 append-oriented이며 replace/delete/import API가 없다. 동일 actor 호출 순서·tick·허용 정보·pipeline에서 같은 Observation stream을 얻으며 wall clock/RNG는 사용하지 않는다.

Game의 run/actor/Observation/제출 tick 권한 실패는 kernel 진입 전 `REJECTED` receipt와 application ActionTrace를 남긴다. `UNKNOWN_ACTION`은 kernel의 최초 actor handler 조회 실패를 변환한 receipt다. 그 조회 전에 현재 tick의 due system event가 처리될 수 있으며, 해당 system commit은 별도 outcome/Event로 보존한다.

| 조건 | reason_code |
|---|---|
| request run 불일치 | `WRONG_RUN` |
| bound actor와 request actor 불일치 | `WRONG_ACTOR` |
| 현재 application history에 없는 reference | `UNKNOWN_OBSERVATION` |
| 실제 record가 다른 actor/run 소유 | `UNAUTHORIZED_OBSERVATION` |
| request tick이 현재 tick과 불일치 | `INVALID_SUBMISSION_TIME` |
| actor registry에 없는 type/version | `UNKNOWN_ACTION` (system registry로 fallback하지 않음) |

다른 run에서 생성된 ID도 현재 history에 없으므로 `UNKNOWN_OBSERVATION`으로 거부한다. ID prefix나 Controller가 만든 Observation object를 증거로 신뢰하지 않는다. 동일 actor의 과거 Observation 재사용은 M7에서도 허용한다. ID 재시도·version 정책은 13절과 별개로 적용한다. 현재 제출 tick 검사는 Controller가 임의 미래 시간을 지정하는 권한을 막는 것으로 Observation 만료 정책과 별개다. kernel은 기존처럼 현재 제출 tick의 due system event를 action validation 전에 처리할 수 있다.

M7의 명시적 reason allowlist만 receipt에 전달한다. 다른 handler reason은 `ACTION_REJECTED`/`ACTION_FAILED` 같은 일반 status code로 제한한다. engine 결함은 `GameSubmissionError("ENGINE_ERROR")`로 감싸며 원래 문자열/handler/Event 정보를 Game 오류 메시지에 넣지 않는다. Research trace에는 error type과 존재하는 kernel result를 보존한다. commit 후 Event delivery 오류라면 성공 result가 남을 수 있고, commit 전 오류라면 result가 없다. action 완료 전 system Event delivery가 실패하면 system commit만 남고 해당 action result는 없다. 등록된 handler 내부의 registry 조회 실패도 engine 오류이며 `UNKNOWN_ACTION`으로 바꾸지 않는다. Game 생성 실패는 `OBSERVATION_UNAVAILABLE`, 미부팅/종료/재진입은 `GAME_UNAVAILABLE`이다.

ActionRequest가 아닌 객체는 TypeError다. live Game은 request/run/actor/Observation identity와 action type이 비어 있지 않은 내장 문자열인지, correlation이 None 또는 비어 있지 않은 내장 문자열인지 검사한다. 잘못된 envelope이나 noncanonical payload는 kernel 진입 및 trace 저장 전에 `INVALID_REQUEST`로 거부한다. 순환 payload 등 JSON 정규화 중 recursion 실패도 같은 입력 오류로 처리한다. 정규화 불가능한 요청은 attempt sequence를 소비하지 않는다. 이 검사는 기존 trusted kernel/replay envelope schema를 변경하지 않는다.

ResearchView의 read API는 `world_snapshot`, `manifest`, `simulation_time`, `state_digest`, `rng_snapshot`, `observations`, `knowledge_history(actor_id)`, `action_traces`, `action_results`, `events`, `pending_scheduled_events`, `system_event_outcomes`다. 반환된 mutable JSON은 저장 상태와 분리되어 있다. 과거 World snapshot query, scenario editing, replay 실행 method와 외부 transport는 추가하지 않았다.

ActionTrace는 `attempt_sequence, request, result, controller_result, boundary_reason, error_type, engine_submitted, retry_of_attempt`를 보존한다. `request.based_on_observation_id`로 Research Observation과 연결하고 result는 해당 시도의 정확한 kernel ActionResult다. 같은 ID의 retry/conflict도 별도 attempt로 기록하지만 kernel에 다시 전달하지 않는다. retry trace의 result는 None이며 retry_of_attempt로 원 시도를 연결한다. authority/registry 거부에는 kernel result가 없고 receipt와 boundary reason이 있다. ScheduledEvent는 trace를 만들지 않는다. 직접 kernel/replay 경로의 요청 원본은 기존처럼 호출자가 보관하며 ReplayInput/Report는 변경하지 않았다.

## 10. M4 구현 계약

`create_alderwick_kernel(run_id="run-alderwick", seed=42, event_bus=None)`은 미부팅 kernel을 반환한다. 호출자가 boot하고 `scenario_schedule(collapse_tick=3).events`를 한 번 enqueue한다. factory 자체는 schedule을 설치하지 않으므로 기존 ReplayHarness에도 그대로 전달한다. `create_alderwick_application(kernel)`은 scenario `alderwick:1`에서 초기 경로 지식, bridge perception과 Knowledge projection을 조립한다. 별도 runner framework 없이 [실행 예제](../src/journeymap/examples/alderwick.py)의 GamePort 루프를 사용한다.

| 계약 | 내용 |
|---|---|
| ScheduledEvent | `CollapseEastBridge` v1, payload `{bridge_id: "east-bridge"}`. due tick은 scheduler에만 존재 |
| system handler | `alderwick.collapse-east-bridge.v1`, intact bridge와 affected routes/위치 검증 후 단일 TransitionPlan |
| committed Event | `BridgeCollapsed` v1, payload `{bridge_id, condition: "collapsed", closed_route_ids, witness_actor_ids}`. route IDs와 witness IDs는 정렬된 목록 |
| visibility | East Road 또는 East Bridge에 있는 유효한 Entity/position. 다른 actor의 위치나 witness 목록은 Observation에 전달하지 않음 |
| bridge Observation | `(15, alderwick, bridge)` contributor. `{bridge: {bridge_id: "east-bridge", condition}}`; 보이지 않으면 `{bridge: {}}` |
| runtime Knowledge | 기존 `(20, knowledge, records)` section의 actor-owned 목록에 초기 지식과 함께 포함 |
| Controller | `Controller.decide(Observation) -> ActionRequest`. ScriptedController 객체에 상태·서비스 handle 없음 |

ScriptedController는 자기 위치에 대응하는 actor/run-owned `travel_route` Knowledge의 첫 route를 선택한다. bridge가 실제 Observation에서 collapsed로 보이거나 경로 지식이 없으면 duration 1의 WAIT를 선택한다. route ID는 초기 actor Knowledge에서 얻으며 canonical route/passability나 hidden schedule을 읽지 않는다. request ID는 `<observation_id>:scripted-v1`, 나머지 run/actor/Observation ID/제출 tick은 받은 envelope에서 가져온다. 매 submit은 기존 Game authority 검증을 통과해야 한다. 붕괴를 미리 아는 시간표나 action sequence는 없다.

runtime 읽기 경로는 `committed Events → KnowledgeProjection → for_actor(actor).history() → perceive → PerceptionContext.known → contribute_knowledge → Observation → decide`다. `ResearchView.knowledge_history(actor)`도 같은 projection을 읽는다. future schedule이나 최종 World snapshot은 projection 입력이 아니다.

`DIRECT_OBSERVATION`은 acquisition 방식이며 source_ref는 실제 획득을 유발한 committed Event identity다. 목격은 BridgeCollapsed, 나중 발견은 ActorMoved를 가리킨다. 나중 발견의 collapsed value는 해당 이동보다 앞선 BridgeCollapsed의 bridge identity/condition과 ordered Event history로 설명한다. 동일 tick에서도 event_sequence를 사용한다. 정확한 ID/dedup 규칙은 [ERD M4 절](ERD.md#10-m4-실제-상태와-projection-provenance)을 따른다.

projection 읽기 실패는 기존 world commit을 취소하지 않는다. Research에는 오류를 전파하고 Game observe에는 `OBSERVATION_UNAVAILABLE`을 반환하며 성공 Observation sequence를 소비하지 않는다. 반복 읽기/재구성은 같은 지식 history를 제공하고 Event delivery 실패에도 committed log에서 복구할 수 있다. bridge MOVE 거부/완료 실패는 기존 M2의 `ROUTE_IMPASSABLE`, `REJECTED`/`FAILED` 의미를 그대로 사용한다.

ReplayInput/ReplayHarness/ReplayReport schema는 변경하지 않았다. engine replay는 초기 world/manifest + versioned schedule + 기록된 ActionRequest stream만 요구한다. 그 결과 Events와 동일 초기 Knowledge로 runtime history를 별도 비교한다. Observation/Knowledge stream을 replay 필수 입력으로 추가하지 않는다. M5 확장은 아래 절의 명시적 composition으로 제공한다.

## 11. M5 구현 계약

`create_alderwick_kernel(social=True)`는 SocialModule과 ASK/INFORM/REQUEST v1을 등록한다. `create_alderwick_application(kernel, social=True)`는 social 초기 입력, projection rule, perception과 `(30, social, interactions)` contributor를 추가한다. 기본 `social=False`는 M4 fixture/Observation 동작을 유지한다. generic composition은 등록된 social handlers와 `create_application(..., social=True)`를 함께 사용한다. custom pipeline은 여전히 전체 pipeline을 대체하므로 knowledge/social contributor의 명시적 등록도 호출자 책임이다. social이 비활성인 Game에서 v1 social 제출은 `SOCIAL_UNAVAILABLE`이다.

| action / schema_version | 정확한 payload |
|---|---|
| ASK / 1 | `{target_actor_id: str, subject_ref: str, predicate: str}` |
| INFORM / 1 | `{target_actor_id: str, claim_record_id: str, reply_to_event_id?: str}` |
| REQUEST / 1 | `{target_actor_id: str, request_kind: str, request_payload: object}` |

모든 문자열은 비어 있지 않아야 한다. 필수 field 누락, 추가 field, 잘못된 type은 `INVALID_PAYLOAD`다. optional reply는 생략할 수 있지만 null/빈 문자열은 거부한다. request_payload는 nested canonical JSON object이고 비어 있어도 된다. request_kind는 구조화된 식별자일 뿐 실행할 handler의 이름이 아니다. payload 안에 action/mutation처럼 보이는 값이 있어도 실행하지 않는다. unknown version은 registry에서 `UNKNOWN_ACTION`으로 거부한다.

모두 고정 1 tick이며 시작과 완료 때 sender/target Entity, 자기 자신이 아닌 target, 양쪽 유효 position/Location, 같은 Location을 확인한다. entity_type은 M2처럼 actor 자격 enum이 아니다. 양쪽이 시작·완료 모두 같은 장소라면 그 두 장소가 서로 달라도 허용한다. 중간 이동 경로나 지속 접촉은 모델링하지 않는다. 완료 tick의 due system input이 먼저 처리된다. 시작 실패는 REJECTED/추가 시간 없음, 완료 실패는 FAILED/경과 시간과 system commit 유지다. social 성공은 domain state/RNG를 변경하지 않고 공통 commit boundary에서 transition/result/Event만 만든다.

| committed Event v1 | 정확한 payload |
|---|---|
| ActorAsked | `{sender_actor_id, target_actor_id, location_id, subject_ref, predicate}` |
| ActorInformed | `{sender_actor_id, target_actor_id, location_id, claim_record_id, reply_to_event_id?}` |
| ActorRequested | `{sender_actor_id, target_actor_id, location_id, request_kind, request_payload}` |

location_id는 완료 장소다. 공통 envelope의 source_kind는 ACTION, source_ref는 ActionRequest ID, transition_id/causation_id는 commit transition ID이며 correlation과 sequence/tick은 기존 Core 계약이다. ASK는 질문 전달만 기록하고 REQUEST는 요청 전달만 기록한다. 자동 답변·지식 조회·내용 실행·fulfillment는 없다.

INFORM의 claim source는 현재 sender-owned Knowledge의 한 record다. live application은 저장된 원본 Observation의 knowledge section에 동일 record JSON이 실제 포함됐는지와 run/actor/획득 시점을 검사한다. unknown·타 actor·타 run·미래·미노출 record에는 동일 `INVALID_CLAIM_REFERENCE`를 반환한다. Controller가 변조한 Observation object나 추측한 ID는 권한 증거가 아니다. stale claim은 소유하고 Observation에 있으면 전달할 수 있다. canonical truth와 일치하는지 검사하지 않는다.

reply_to_event_id는 앞서 받은 ActorAsked ID다. 원본 Observation의 social section에 해당 interaction이 있어야 하며 질문자=INFORM target, 질문 대상=INFORM sender, subject/predicate=claim topic이어야 한다. 실패는 `INVALID_REPLY_REFERENCE`. 새 request ID로 같은 질문에 다시 답하는 것은 허용한다. 같은 ID exact retry는 M7에 따라 재실행하지 않으며 NPC는 최신 Observation의 answered를 보고 불필요한 새 답변을 피한다.

추가 actor-visible canonical reason은 `SELF_TARGET`, `UNKNOWN_TARGET`, `INVALID_TARGET`, `TARGET_MISSING_POSITION`, `TARGET_INVALID_POSITION`, `OUT_OF_RANGE`다. sender는 기존 `UNKNOWN_ACTOR`, `INVALID_ENTITY`, `MISSING_POSITION`, `INVALID_POSITION`을 사용한다. social의 position/location integrity 오류는 해당 actor의 INVALID_POSITION 계열로 묶는다. live schema/claim/reply 거부는 kernel 진입 전 receipt와 boundary ActionTrace만 남긴다. canonical 거부/완료 실패에는 kernel ActionResult가 있다.

social Observation content는 `{social: {interactions: [...]}}`다. incoming interaction은 `{event_id, event_type, simulation_time, sender_actor_id}`와 다음 allowlist field만 가진다.

- ActorAsked: `subject_ref, predicate, answered: bool`.
- ActorInformed: 존재하면 `reply_to_event_id`. 실제 claim은 receiver의 knowledge section에 별도 projected record로 전달한다.
- ActorRequested: `request_kind, request_payload`.

목록은 committed Event 순서다. target 자신, sender의 claim_record_id, global event_sequence, transition, 전체 Event log를 복사하지 않는다. 과거 incoming은 남고 answered는 committed reply로만 계산한다. 같은 장소의 unrelated actor도 해당 interaction을 받지 않는다.

`INFORMED` record의 ID는 `<ActorInformed.event_id>:social-informed-v1:00000001`, source_ref는 해당 Event ID, learned_at은 완료 tick이다. subject/predicate/value는 prefix의 sender record에서 복사하고 supersedes_id는 None이다. 반복 rebuild는 동일하며 새 INFORM Event는 같은 claim을 전달해도 별도 획득 기록이다. 신뢰도·자동 winner·stale 제거는 없다.

Research는 기존 observations/action_traces/action_results/events/knowledge_history로 chain을 조회한다. live claim projection 오류는 `GameSubmissionError("KNOWLEDGE_UNAVAILABLE")`와 result 없는 error trace다. observe projection 오류는 `OBSERVATION_UNAVAILABLE`이고 partial Observation/sequence를 남기지 않는다. post-commit delivery 오류는 기존 `ENGINE_ERROR` receipt 경계와 성공 result/Event를 유지하며 Knowledge는 committed log에서 복구한다.

engine replay에는 kernel에 전달된 recorded requests만 넣는다. boundary-denied attempts는 연구 trace로 별도 보존한다. 정상·kernel REJECTED/FAILED 요청은 그대로 replay한다. interrupted/delivery-failed run은 기존 오류 계약을 따르며 자동 resume/retry는 없다. replay kernel은 claim/Observation authority를 다시 검사하지 않는 trusted 경로이며, 재구성한 Knowledge는 sender prefix ownership/source를 검증한다. 이 경계는 arbitrary untrusted replay input에 live 권한을 부여하지 않는다.

비LLM 예제 `alderwick_social`은 tick 3부터 Hugh/Thomas/Hugh/Thomas/Hugh 순서로 actor를 activate한다. policy는 제공된 Observation으로 MOVE/ASK/INFORM/WAIT/WAIT를 결정하고 target-scoped ASK가 실제 별도 INFORM으로 이어진다. recorded ActionRequest만으로 tick 9까지 engine replay되며 NPC policy 실행은 불필요하다.

## 12. M6 구현 계약

`create_alderwick_kernel(resources=True)`와 `create_alderwick_application(kernel, resources=True)`가 resource fixture와 세 모듈을 명시적으로 구성한다. `social=True`와 함께 사용할 수 있다. manifest/scenario schedule version은 `resources-1`이며 기본 M4/M5의 `1`과 호환되는 것으로 취급하지 않는다. `resource_schedule()`을 live caller가 설치한다. replay에는 같은 schedule을 ReplayInput으로 전달한다. generic application에는 자동 resource section이 없다.

| Action / schema_version | 정확한 payload | duration | 성공 효과 |
|---|---|---|---|
| REST / 1 | `{duration: positive_int}` | duration tick | 최신 fatigue에서 duration ×3 감소; 최소 0 |
| CONSUME / 1 | `{item_id: nonempty_str, quantity: positive_int}` | 1 tick | 자기 item 수량 감소, 최신 hunger에서 quantity ×현재 recovery 감소; 최소 0 |
| BUY / 1 | `{offer_id: nonempty_str, quantity: positive_int}` | 1 tick | seller→buyer item 이전, buyer→seller 정수 통화 이전 |

필수 field 누락/추가 및 잘못된 identity type은 INVALID_PAYLOAD다. quantity/duration의 bool, 0, 음수, float/string/null은 거부한다. version은 registry에서 정확히 dispatch하며 미등록 version은 Game에서 UNKNOWN_ACTION이다. resource module이 없는 기본 Alderwick의 세 action도 UNKNOWN_ACTION이다. quantity 제한은 별도로 두지 않고 실제 stock/funds를 검증한다. M6의 one-tick bulk CONSUME는 최소 계약이며 nutrition/효과 framework가 아니다.

BUY는 시작·완료 양쪽에서 buyer/seller Entity, self purchase 금지, 양쪽 valid position/Location와 같은 Location, active offer, item identity, 양쪽 inventory, stock, buyer funds와 seller wallet을 검증한다. 가격은 0 이상의 정수여서 무료 offer도 가능하다. offer 자체는 재고 수량을 갖지 않으며 seller inventory만 권위다. out-of-stock active listing은 Observation에 계속 보일 수 있고 BUY에서 INSUFFICIENT_STOCK으로 거부한다.

`ActionTiming.data`에는 시작 offer의 `{offer_id, seller_id, item_id, unit_price, active}`만 저장한다. 완료 시 없는 offer는 UNKNOWN_OFFER, malformed offer는 INVALID_OFFER, 비활성화는 OFFER_UNAVAILABLE, 유효한 seller/item/price 변경은 OFFER_CHANGED다. 같은 의미의 metadata 변경은 구매를 막지 않는다. quote가 같으면 현재 stock/funds/위치를 재검증하고 현재 잔액·수량에 이전을 적용한다. 양쪽이 시작과 완료 모두 같은 장소라면 장소 자체가 바뀌어도 허용한다. 중간 경로의 지속 접촉은 모델링하지 않는다.

CONSUME는 시작·완료에 actor/SurvivalState, inventory item/quantity와 consumable 설정을 검증한다. recovery는 survival 소유 `consumables[item_id]`의 양의 정수다. bread fixture는 10이며 여러 개는 선형 적용한다. 완료 시 현재 유효한 효과 설정을 사용하며 BUY와 달리 effect quote를 고정하지 않는다. REST는 actor/SurvivalState를 재검증하고 hunger/inventory/wallet을 직접 변경하지 않는다. REST 중 system input의 hunger 증가는 그대로 남는다.

`SurvivalTick / 1`은 정확히 `{}` payload의 별도 ScheduledEvent다. `survival.tick.v1` handler는 현재 등록된 survival actor들의 hunger +2/fatigue +1을 100에서 제한하고 actor ID 순서로 SurvivalAdvanced Event를 발행한다. 전체 actor 갱신은 하나의 system transition이다. Alderwick finite schedule은 tick 1–20, priority 10이며 bridge collapse는 tick 3/priority 0이다. 그 뒤의 action completion은 최신 상태에 적용된다. tick 20 이후에는 추가 schedule 없이는 pressure 증가가 없다. system input은 ActionRequest/ActionTrace를 만들지 않는다.

| Committed Event / v1 | Payload |
|---|---|
| SurvivalAdvanced | `{actor_id, before: {hunger, fatigue}, after: {hunger, fatigue}}` |
| ActorRested | `{actor_id, duration, fatigue_before, fatigue_after}` |
| ItemConsumed | `{actor_id, item_id, quantity, hunger_before, hunger_after}` |
| ItemPurchased | `{buyer_id, seller_id, offer_id, item_id, quantity, unit_price, total_price}` |

actor Event의 source_ref는 ActionRequest ID이고 system Event의 source_ref는 ScheduledEvent ID다. 공통 transition_id/causation_id, correlation, sequence/tick은 기존 envelope을 사용한다. CONSUME item 감소량은 해당 성공 Event quantity와 같으며 hunger 감소는 0 clamp 때문에 recovery ×quantity보다 작을 수 있다. BUY는 양쪽 item/currency 합계를 보존한다. REST/SurvivalTick은 inventory/currency를 변경하지 않는다.

| Reason | 의미 |
|---|---|
| INVALID_PAYLOAD / INVALID_DURATION / INVALID_QUANTITY | 정확한 payload 또는 양의 정수 계약 위반 |
| UNKNOWN_ACTOR / INVALID_ENTITY | 실행 actor 없음 또는 malformed identity |
| UNKNOWN_ITEM | 등록되지 않은 item; invalid inventory entry reference도 이 코드 |
| UNKNOWN_INVENTORY_OWNER / INVALID_INVENTORY_STATE | owner/Entity 없음 또는 malformed inventory tables/identity/quantity |
| INSUFFICIENT_QUANTITY | CONSUME에 필요한 자기 수량 없음 |
| MISSING_SURVIVAL_STATE / INVALID_SURVIVAL_STATE | actor survival record 없음 또는 malformed table/pressure/effect |
| ITEM_NOT_CONSUMABLE | inventory item은 있지만 survival 효과 없음 |
| MISSING_WALLET / INVALID_WALLET | 대상 wallet 없음 또는 malformed table/정수 잔액 |
| UNKNOWN_OFFER / INVALID_OFFER | offer 없음 또는 identity/price/active 형태 오류 |
| OFFER_UNAVAILABLE / OFFER_CHANGED | inactive 또는 duration 동안 quote 의미 변경 |
| SELF_PURCHASE | buyer와 seller가 동일 |
| UNKNOWN_SELLER / INVALID_SELLER | seller Entity 없음 또는 malformed identity |
| MISSING_POSITION / INVALID_POSITION | buyer 위치/Location 없음 또는 유효하지 않음 |
| SELLER_MISSING_POSITION / SELLER_INVALID_POSITION | seller 위치/Location 없음 또는 유효하지 않음 |
| OUT_OF_RANGE | buyer와 seller의 Location이 다름 |
| INSUFFICIENT_FUNDS / INSUFFICIENT_STOCK | buyer 잔액 또는 seller 실제 수량 부족 |

inventory의 내부 공개 helper는 동일 owner 이전을 SELF_TRANSFER로 거부한다. trade payment helper의 잘못된 total은 INVALID_PRICE다. 이 두 helper-only 코드는 등록된 actor payload에서 발생하지 않으며 Game 공개 allowlist에 추가하지 않았다. wallet/owner/item의 cross-module Entity/reference 무결성은 handler/query 시 검증하고 초기 builders는 자신이 받은 module records의 shape/중복/local references를 검증한다.

시작 검증 실패는 REJECTED이며 제출 tick까지의 독립 세계 진행 이후 추가 시간을 소비하지 않는다. 완료 검증 실패는 FAILED로 시간·system commits를 유지하고 action 성공 mutation/Event를 남기지 않는다. resolve/serialization 예외는 기존 engine defect 경로이며 kernel ActionResult를 만들지 않고 live Game은 ENGINE_ERROR와 result 없는 trace를 남긴다. commit 뒤 delivery 예외는 두 module state/Event/성공 result와 연결된 trace를 보존한다. kernel 자동 retry는 없으며 M7 live idempotency는 13절을 따른다.

Observation의 `(40, inventory, resources)` content는 `{inventory: {item_id: quantity}}`, `(40, survival, resources)`는 `{survival: {hunger, fatigue}}`, `(40, trade, resources)`는 `{trade: {wallet: int, offers: [{offer_id, seller_id, item_id, unit_price, active}]}}`다. item ID와 offer ID 순서로 안정 정렬하며 같은 priority의 module 순서는 기존 pipeline 규칙이다. active local 타인 offer만 공개하고 자신의 seller listing은 제외한다. 다른 actor의 전체 inventory/wallet/survival, remote offer, effect configuration, future schedule, raw tables와 Research log는 contributor에 도달하지 않는다. 반복 observe는 canonical world/time/RNG/schedule을 변경하지 않는다.

ReplayInput/ReplayHarness/ReplayReport와 Core kernel schema는 변경하지 않았다. kernel에 전달된 성공/REJECTED/FAILED ActionRequest와 versioned schedule로 결과, system outcomes, Event/order, world/digest, time/RNG draw count를 비교한다. live Observation과 Knowledge는 추가 replay 입력이 아니며 Knowledge는 기존 M4/M5 committed Event projection으로 별도 검증한다. 예제는 trusted caller가 고정 resource path를 GamePort로 제출하는 최소 실행이며 새 NPC policy/LLM controller는 없다. M7의 최종 schema compatibility, idempotency, budget/redaction/conformance는 아래 13절을 따른다.

## 13. M7 최종 compatibility / retry / Observation / Controller 계약

### Version compatibility

actor dispatch는 정확한 `(action_type, schema_version)` pair다. unknown type와 알려진 type의 unknown version은 모두 `UNKNOWN_ACTION`으로 처리한다. v2를 v1으로 추측하거나 payload를 coercion/upgrade/downgrade하지 않는다. 새 version은 별도 ActionRegistry 등록과 contract test가 필요하며 지원 중인 old version의 의미는 그대로 유지한다. application의 명시적인 v1 parser map은 turn preflight이며 domain resolution은 기존 registry/handler가 담당한다. 새로운 지원 version을 Controller 경로에 추가하려면 이 preflight 계약도 명시적으로 갱신한다.

Observation은 schema_version=1을 발행한다. generic envelope는 미래 version 기록을 담을 수 있어도 현재 Scripted/Human/SocialNpc reader와 MemoryContext는 v1 외 version을 거부한다. section 구조를 암묵 해석하지 않는다.

### Live idempotency

run당 하나의 SimulationApplication을 생성하고 모든 actor GamePort에 공유한다. cache는 그 application 수명 동안 유지되는 메모리이며 저장·복원·TTL·eviction·distributed exactly-once는 없다. 재시작이나 application 재생성은 retry 보존을 보장하지 않는다. 이미 실행 중인 kernel에 두 번째 live authority를 생성하지 않는다.

key는 action_request_id이며 equality는 1절의 **9개 필드 전체**를 canonical JSON으로 직렬화한 값이다. run/actor/Observation/submitted_at/type/version/payload/correlation 모두 포함한다. object key 삽입 순서와 Python object identity는 무관하며 배열 순서와 값의 JSON 표현은 구분한다. 예를 들어 1과 1.0, None과 문자열 correlation은 서로 다르다. Unicode normalization이나 correlation fallback을 equality에 적용하지 않는다. cache는 실제 호출 GamePort의 actor binding도 보존하므로 다른 actor에게 이전 receipt를 반환하지 않는다.

| 상황 | 최초 호출 / exact retry |
|---|---|
| SUCCEEDED | 성공 receipt / 동일 receipt, kernel 호출·clock·Event·transition·RNG 소비 없음 |
| kernel REJECTED | 거부 receipt / 동일 receipt, 재검증·재실행 없음 |
| elapsed-time FAILED | 완료 실패 receipt / 동일 receipt, 경과 시간·system commit 반복 없음 |
| boundary-denied | 확정된 denial receipt / 시간이 지나 권한 조건이 달라져도 같은 denial |
| 같은 ID + 다른 normalized 요청 또는 다른 bound actor | `REQUEST_ID_CONFLICT` REJECTED, 원 cache·state 유지, 새 payload 실행 없음 |
| action commit 뒤 delivery error | 최초 ENGINE_ERROR와 성공 kernel result를 Research에 유지 / 그 시도의 확정된 actor-visible 성공 receipt 반환 |
| kernel 진입 후 result 없는 예외 | 최초 ENGINE_ERROR / ID consumed/indeterminate로 유지, retry도 ENGINE_ERROR, 자동 재실행 없음 |
| kernel 진입 전 INFORM projection/claim read defect | KNOWLEDGE_UNAVAILABLE, engine_submitted=False / 예약 해제, 복구 후 같은 ID retry 허용 |
| normalize 불가 또는 GAME_UNAVAILABLE | 확정 identity/실행 없음 / cache·trace 예약 없음 |

중복 판정은 새로운 요청의 current-tick/Observation 검증보다 먼저 수행한다. 따라서 이미 완료된 request의 예전 submitted_at도 exact retry에는 유효하다. conflict receipt의 tick은 해당 충돌 시도 시각이며 conflict를 원 cache 대신 저장하지 않는다. conflict는 ID 충돌 여부만 알리고 기존 actor/run/request 내용·status·이유·시간을 알려주지 않는다. 타 actor의 receipt나 타 run history 조회는 없다.

post-commit 복구는 submit 직전의 action_results offset 이후에 추가된 결과만 사용한다. 예전 request ID 검색으로 결과를 붙이지 않는다. system delivery만 실패했으면 action result가 없으므로 성공 receipt를 만들지 않는다. kernel 진입 전에 확실하게 끝난 읽기 실패만 retry 가능하며, projection의 ActionValidationError도 행동 거부로 오분류하지 않는다.

### Actor-visible reason taxonomy

| 종류 | 코드 |
|---|---|
| envelope / JSON | `INVALID_REQUEST` (GameSubmissionError) |
| payload schema | `INVALID_PAYLOAD`, `INVALID_DURATION`, `INVALID_QUANTITY` |
| Game authority | `WRONG_RUN`, `WRONG_ACTOR`, `UNKNOWN_OBSERVATION`, `UNAUTHORIZED_OBSERVATION`, `INVALID_SUBMISSION_TIME`, `INVALID_CLAIM_REFERENCE`, `INVALID_REPLY_REFERENCE` |
| availability/version | `UNKNOWN_ACTION`, `SOCIAL_UNAVAILABLE` |
| idempotency | `REQUEST_ID_CONFLICT` |
| 비공개/미등록 domain diagnostic | `ACTION_REJECTED`, `ACTION_FAILED` |
| application/engine | `GAME_UNAVAILABLE`, `OBSERVATION_UNAVAILABLE`, `KNOWLEDGE_UNAVAILABLE`, `ENGINE_ERROR` (GameSubmissionError) |
| turn helper | `CONTROLLER_ERROR`, `INVALID_CONTROLLER_OUTPUT`, `SUBMISSION_ERROR`; observation/schema 실패는 위 안정된 코드 사용 |

나머지 공개 domain codes는 정확히 다음 집합이다. 이는 runtime reason registry가 아니라 `application.reasons`의 고정 allowlist이며 새 코드는 문서·test와 함께 명시적으로 추가한다.

```text
UNKNOWN_ACTOR INVALID_ENTITY MISSING_POSITION INVALID_POSITION UNKNOWN_ROUTE
INVALID_ROUTE UNKNOWN_LOCATION INVALID_LOCATION WRONG_ORIGIN ROUTE_IMPASSABLE
INVALID_TRAVERSAL_COST INVALID_PASSABILITY ROUTE_CHANGED SELF_TARGET UNKNOWN_TARGET
INVALID_TARGET TARGET_MISSING_POSITION TARGET_INVALID_POSITION OUT_OF_RANGE
UNKNOWN_ITEM UNKNOWN_INVENTORY_OWNER INVALID_INVENTORY_STATE INSUFFICIENT_QUANTITY
MISSING_SURVIVAL_STATE INVALID_SURVIVAL_STATE ITEM_NOT_CONSUMABLE INVALID_WALLET
MISSING_WALLET UNKNOWN_OFFER INVALID_OFFER OFFER_UNAVAILABLE SELF_PURCHASE
INSUFFICIENT_FUNDS INSUFFICIENT_STOCK OFFER_CHANGED UNKNOWN_SELLER INVALID_SELLER
SELLER_MISSING_POSITION SELLER_INVALID_POSITION
```

동일 domain condition이 시작에 실패하면 REJECTED, 완료에 실패하면 FAILED다. 이유 code 자체가 시간을 재정의하지 않는다. 성공 reason은 None이다. BUY에서는 `INVALID_WALLET`, `MISSING_WALLET`, `INVALID_INVENTORY_STATE`, `UNKNOWN_INVENTORY_OWNER`, `UNKNOWN_ITEM`을 `ACTION_<status>`로 숨긴다. shared validator가 buyer와 seller 중 누구의 private table 오류인지 구분하지 않기 때문이다. Research의 kernel reason, validation 순서, 구매 조건, 금액·재고·원자성은 바꾸지 않는다. `INSUFFICIENT_FUNDS`/`INSUFFICIENT_STOCK` 같은 정상 구매 거부는 계속 공개한다. 내부 helper의 `SELF_TRANSFER`/`INVALID_PRICE`는 public contract가 아니며 일반 fallback을 사용한다.

### Observation v1 schema / ordering / budget

공개 envelope은 `observation_id, run_id, actor_id, observation_sequence, simulation_time, schema_version, content_digest, content`다. ID/sequence/tick/digest·immutability는 9절과 같고 content는 정확히 다음 구조다.

```json
{"sections":[{"module_id":"core","contributor_id":"self","content":{"self":{"entity_id":"stranger","entity_type":"person"}}}]}
```

section의 공개 identity는 `(module_id, contributor_id)`이며 중복 등록은 priority가 달라도 거부한다. 실행·배열 순서는 `(priority,module_id,contributor_id)`로 정렬하고 priority는 content에 노출하지 않는다. contributor 등록 순서/hash-map 순서가 결과를 바꾸지 않는다. Knowledge는 `(learned_at,knowledge_record_id)`, social은 committed Event 순서, offer/item은 ID 순서로 정렬한다. nested object는 canonical serializer가 key 정렬하며 배열의 의미 순서는 contributor가 소유한다.

예산은 `len(canonical_json(content).encode("utf-8")) <= 65_536`이다. envelope/ID/digest 자체는 content 예산에 포함하지 않는다. `ObservationPipeline(max_content_bytes=...)`로 trusted composition만 양의 정수 상한을 지정할 수 있다. 기본값은 모든 generic/Alderwick composition에서 동일하다. tokenizer/model/wall clock/RNG와 무관하다.

전체 content를 검증한 뒤 성공할 때만 history/sequence를 append한다. 초과·잘못된 JSON·contributor/projection 오류는 live에서 `OBSERVATION_UNAVAILABLE`이며 partial record/sequence gap을 만들지 않는다. exact 65,536 bytes는 허용하고 1 byte 초과는 거부한다. 문자열 중간 절단, section 임의 삭제, semantic 요약, truncation marker를 만들지 않는다. canonical World Truth·KnowledgeLedger·Research Event/Knowledge 이력도 변경하지 않는다.

M4/M5/M6/결합 예제의 초기 5 actor 및 예제 경로 전후를 측정한 범위는 각각 375–1,858 / 471–1,650 / 664–1,854 / 760–1,950 bytes였다. 이는 작은 fixture의 크기 측정이며 24h 성능 보장이 아니다. Knowledge와 incoming social history는 증가하므로 상한을 넘으면 그 actor의 새 Observation은 생성되지 않는다. 상충 주장·경로 지식·미응답 ASK를 선택적으로 지우지 않으며 다른 actor의 작은 Observation은 계속 생성 가능하다. full-log projection·Research history 메모리/처리 비용은 여전히 증가한다. M8은 tick-to-hour, 활동량, 기록/export와 24h 용량을 실험 protocol로 정해야 한다.

### Redaction and Controller turn

정보 경계는 trusted actor-scoped perception의 **positive projection**이다. contributor는 PerceptionContext만 받고 raw world, 다른 actor Knowledge, scheduler/future events, seed/RNG, Research handle을 받지 않는다. trusted formatter/composition에 숨은 capability를 closure로 주입하지 않는다. section envelope는 strict하고 content는 module-owned JSON이다. 합법적인 주장/REQUEST 값에 있는 단어를 재귀 denylist로 삭제하지 않는다. 이 구조 검증을 authorization이나 Python sandbox로 취급하지 않는다.

`application.turns.run_controller_turn(game, controller)`는 observe → decide → normalize/binding/v1 payload preflight → submit 하나만 실행한다. 반환 `ControllerTurnResult`는 `observation?, request?, receipt?, failure_code?`이며 trusted caller가 보관하는 최소 연구 기록이다. 원시 malformed object·exception message·stack trace는 보관하지 않는다. 타입/JSON/binding/schema 오류나 Controller 예외는 submit하지 않으므로 due-now scheduler조차 소비하지 않는다. 성공 observe record는 남을 수 있지만 world/action result/Event/RNG는 바뀌지 않는다.

turn helper는 자신이 전달한 Observation에 request가 binding되는지 확인한다. 직접 GamePort를 사용하는 actor는 여전히 과거 Observation을 참조할 수 있다. 직접 live Game은 기존 M3처럼 kernel 진입 뒤 payload start-validation 전에 due-now system input을 처리할 수 있다. 이는 M2 독립 세계 진행 의미이며 Controller preflight failure와 구분한다. submit 오류는 `SUBMISSION_ERROR`로 남기고 자동 retry하지 않는다. 이 단계의 오류는 이미 world commit을 포함할 수 있어 no-mutation으로 표시하지 않는다. caller는 record의 같은 request를 명시적으로 Game에 재제출하여 위 idempotency 정책을 적용할 수 있다.

HumanController는 injected `Callable[[Observation], ActionRequest]`만 보관한다. terminal/UI나 GamePort/Research를 저장하지 않으며 source에 world capability를 capture하지 않는 것은 composition 책임이다. Scripted/Human/SocialNpc는 같은 reusable conformance suite로 입력 불변성·binding·canonical output·determinism·권한 표면을 검사한다. Human source 자체의 정책이 결정적이라는 보장은 없으며 결정적 test source에서 반복 동일성을 검증한다.

### Research / replay

ActionTrace의 `engine_submitted`는 실제 kernel 진입 여부, `retry_of_attempt`는 원 live attempt sequence다. retry/conflict의 `result=None`은 새 engine result가 없다는 의미이며 반환 receipt와 모순되지 않는다. 최초 post-commit delivery failure trace는 성공 result와 error_type을 보존하고 controller_result=None을 유지한다. cache의 복구 receipt는 다음 retry에서만 반환한다. ScheduledEvent는 actor trace를 만들지 않는다.

ReplayInput/ReplayHarness/ReplayReport와 kernel semantics는 M7에서 변경하지 않았다. 완전히 resolved된 run의 engine action stream은 `trace.engine_submitted and trace.result is not None`인 원 요청들이다. 성공·REJECTED·FAILED는 포함하고 live duplicates/conflicts/boundary denials는 포함하지 않는다. unknown registry miss 또는 result 없는 interrupted engine call까지 있는 이력을 이 필터만으로 완전 재현했다고 주장하면 안 된다. 그러한 call의 독립 system 진행/중단 경계는 Research에 보존되며 자동 interrupted-run replay/resume는 범위 밖이다. trusted raw engine replay는 이미 승인·기록된 입력을 재생하고 live authority나 idempotency를 재구현하지 않는다.
