# API Contracts

이 문서는 transport와 분리된 논리 계약이다. M0는 in-process contract를 우선하며 FastAPI 같은 외부 transport를 도입하지 않는다. 모든 envelope은 `schemaVersion`을 가진다.

## 1. Game / Controller interface

Controller가 접근할 수 있는 유일한 게임 표면이다. actor-scoped이며 다른 actor의 지식, raw component, scheduler, RNG, 전체 Event log 또는 World Truth query를 제공하지 않는다.

### `Controller.decide(observation) -> ActionRequest`

```json
{
  "observationId": "obs-000042",
  "runId": "run-alderwick-001",
  "actorId": "actor-stranger",
  "simulationTime": "1348-05-12T09:10:00",
  "sequence": 42,
  "schemaVersion": 1,
  "situation": {
    "location": { "id": "village-square", "name": "Village Square" },
    "perceivedFacts": [],
    "knownFacts": [],
    "availableActionTypes": ["OBSERVE", "MOVE", "ASK", "WAIT"]
  }
}
```

Observation은 설명 가능한 최소 식별자와 actor가 인지할 수 있는 내용만 가진다. `availableActionTypes`는 편의 정보이며 성공 보장이 아니다. 이동 중 세계가 바뀌는 등 제출 시점 검증이 다시 필요하다.

```json
{
  "actionRequestId": "act-000042",
  "runId": "run-alderwick-001",
  "actorId": "actor-stranger",
  "basedOnObservationId": "obs-000042",
  "actionType": "MOVE",
  "schemaVersion": 1,
  "payload": { "route_id": "square-to-east-road" }
}
```

엔진은 run/actor/observation 연계, schema, action availability와 도메인 precondition을 검증한다. Controller가 보낸 actor ID나 simulation time을 권위 값으로 신뢰하지 않는다. ActionRequest는 Actor/Controller 의도에만 사용하며 system event 입력을 허용하지 않는다.

### `Game.submit(actionRequest) -> ActionResult`

```json
{
  "actionResultId": "result-000042",
  "actionRequestId": "act-000042",
  "status": "SUCCEEDED",
  "reasonCode": null,
  "resolvedAt": "1348-05-12T09:20:00",
  "schemaVersion": 1,
  "outcome": {
    "publicSummary": "You arrive at East Road."
  }
}
```

실패는 안정적인 `reasonCode`를 반환한다. 예: `INVALID_SCHEMA`, `WRONG_ACTOR`, `STALE_OBSERVATION`, `UNKNOWN_ACTION`, `PRECONDITION_FAILED`, `ROUTE_IMPASSABLE`, `INSUFFICIENT_RESOURCES`. `outcome`도 actor에게 공개 가능한 내용만 포함한다.

### 초기 action payload 방향

| Action | 최소 payload | 소유 Module |
|---|---|---|
| `OBSERVE` | 선택적 focus | perception/application |
| `MOVE` | `route_id` (M2 v1 확정) | movement |
| `ASK` | target actor, topic ID | social |
| `INFORM` | target actor, claim reference/structured claim | social + knowledge |
| `REQUEST` | target actor, request kind/payload | social |
| `BUY` | seller, offer/item, quantity | trade |
| `CONSUME` | inventory entry, quantity | survival + inventory |
| `REST` | duration 또는 rest option | survival |
| `WAIT` | `duration` (M2 v1 양의 정수 tick) | core |

MOVE/WAIT의 구현 계약은 아래 M2 절을 따른다. 나머지 구체 schema와 최종 compatibility 정책은 M7에서 contract test와 함께 확정한다. 자연어는 `utterance` 같은 표현 필드로 포함할 수 있지만 action의 canonical 의미는 구조화된 필드가 결정한다.

## 2. Provider interface

Provider는 World Engine API가 아니다.

```text
Provider.generate(versionedPrompt, modelConfig) -> RawModelResponse
LLMController.parse(RawModelResponse) -> ActionRequest | ParseFailure
```

Provider adapter는 재시도·rate limit·원시 응답 metadata를 다루고, LLMController는 허용 action schema로 제한해 parsing한다. parsing 실패는 상태를 변경하지 않으며 기록 후 fallback 또는 재시도 정책을 적용한다.

## 3. System/Event interface

Controller에 노출되지 않는 engine 내부 계약이다.

```text
Scheduler.dispatch(ScheduledEvent) -> SystemEventHandler
SystemEventHandler.validateAndResolve(event, context) -> SystemEventOutcome
```

자연 발생 World Event도 해당 event type의 System/Event Handler로 전달한다. 이 경로는 ActionRegistry와 분리되며 ActionRequest/ActionResult를 만들지 않는다. 검증된 outcome은 actor action과 동일한 deterministic transaction/mutation boundary에서 적용되고 Event Log에는 source event ID/type, handler/version, simulation time, causation/correlation, ordering과 결과를 남긴다.

## 4. Research / Debug interface

권한 있는 offline 분석·테스트 도구용 표면이다. Controller에 주입하거나 같은 credential로 노출하지 않는다.

필요 query/command:

- run metadata, module/schema versions, seed와 state digest 조회
- 특정 시각의 canonical World Truth snapshot 조회
- actor별 KnowledgeRecord history 조회
- ScheduledEvent, Observation, ActionRequest, ActionResult, Event timeline 조회·내보내기
- causation/correlation chain 조회
- versioned 초기 schedule과 recorded ActionRequest stream으로 replay 실행
- replay의 expected/actual state digest와 최초 divergence 조회
- 테스트 전용 ScheduledEvent 구성·시간 진행(운영 Game API와 분리)

예시 논리 계약:

```text
Research.getRun(runId) -> RunManifest
Research.getTimeline(runId, filters) -> ResearchRecords
Research.getWorldSnapshot(runId, at) -> CanonicalSnapshot
Research.getActorKnowledge(runId, actorId, at) -> KnowledgeHistory
Research.replay(runManifest, initialSnapshot, schedule, actions) -> ReplayReport
ScenarioHarness.advanceTo(simulationTime) -> TransitionResults
```

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
- idempotency/retry가 필요하면 동일 `actionRequestId`는 동일 결과를 반환하고 중복 mutation하지 않는다.
- unknown field 정책과 schema migration은 action type/version별로 명시한다.
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

M1 `ActionRequest`는 run/actor/request identity, 필수 `basedOnObservationId`, 제출 tick, action type/schema version, canonical JSON payload와 correlation을 가진 최소 replay envelope이다. M1은 이 필드를 비어 있지 않은 opaque reference로 보존해 actor intent가 Observation에 기반한다는 계약을 약화하지 않는다. 실제 Observation envelope·perception·저장소는 M3 범위이므로 M1은 가짜 Observation을 만들거나 참조의 존재·actor 권한·staleness를 검증하지 않는다. 구체 action payload, reason code, idempotency와 최종 compatibility 정책은 M7에서 확정한다.

ScheduledEvent는 versioned ScenarioSchedule의 system input이며 `SystemEventRegistry`로만 전달된다. 같은 문자열 type을 ActionRegistry와 SystemEventRegistry에 각각 등록할 수 있지만 두 dispatch path는 교차하지 않는다. 두 handler는 validation과 resolution 동안 canonical state를 직접 받지 않고 복사본과 transactional RNG만 받으며, 성공한 TransitionPlan만 공통 mutation 경계에서 적용된다.

## 8. M2 구현 계약

위 Game/Controller JSON 예시는 0.1 논리 방향이며 완성된 transport API가 아니다. M2는 Python in-process API로 다음만 확정한다.

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

HTTP API 없이 Python in-process port를 제공한다. run당 하나의 `create_application(kernel, initial_knowledge=(), pipeline=None)`이 `(SimulationApplication, ResearchView)`를 반환한다. kernel boot/close와 scenario schedule/advance는 trusted 호출자가 관리하며 Controller에는 `application.game_for(actor_id)`의 반환값만 준다. GamePort에 actor 선택, kernel, ResearchView, registry 또는 store를 주입하지 않는다.

| 표면 | 실제 계약 |
|---|---|
| `GamePort.observe()` | 현재 actor/tick의 Observation을 성공적으로 조립한 뒤 history에 append하고 반환. 시간·canonical state·지식은 변경하지 않음 |
| `GamePort.submit(ActionRequest)` | binding의 run/actor/Observation 권한과 `submitted_at == current tick` 검증 후 기존 actor handler 경로로 전달 |
| `ControllerActionResult` | `action_request_id, run_id, actor_id, status, reason_code, started_at, resolved_at, schema_version=1`만 포함 |
| Game에 없는 정보 | kernel ActionResult의 state digest, transition/handler identity, emitted Event IDs, raw diagnostics와 전체 Event log |

기본 Observation의 `content.sections`는 `{module_id, contributor_id, content}` object의 ordered list다. bootstrap은 `(0, core, self)`, `(10, movement, position)`, `(20, knowledge, records)`를 등록한다. 기본 section content는 각각 `{self: {entity_id, entity_type}}`, `{position: {location_id}}`, `{records: [actor-owned KnowledgeRecord JSON...]}`다. position이 없으면 빈 object이며 route/다른 actor 위치는 제공하지 않는다. 사용자 지정 pipeline은 기본 pipeline을 대체하며 신뢰된 composition에서만 등록한다.

PerceptionContext의 확정된 필드는 `run_id: str`, `actor_id: str`, `simulation_time: int`, `perceived: JsonObject`, `known: JsonObject`다. perceived와 known은 perception 이후의 safe JSON이며 raw World Truth나 query capability가 아니다. contributor의 입력과 출력은 각각 canonical 검증 후 분리한다. 순서는 `(priority, module_id, contributor_id)`이며 동일 key를 거부한다. invalid/noncanonical 출력 또는 callback 오류에서는 partial Observation이나 sequence gap을 만들지 않는다.

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

다른 run에서 생성된 ID도 현재 history에 없으므로 `UNKNOWN_OBSERVATION`으로 거부한다. ID prefix나 Controller가 만든 Observation object를 증거로 신뢰하지 않는다. 동일 actor의 과거 Observation 재사용은 허용하며 expiry/exact freshness/idempotency/compatibility 정책은 M7로 남긴다. 현재 제출 tick 검사는 Controller가 임의 미래 시간을 지정하는 권한을 막는 것으로 Observation 만료 정책과 별개다. kernel은 기존처럼 현재 제출 tick의 due system event를 action validation 전에 처리할 수 있다.

M2의 문서화된 actor reason code만 receipt에 전달한다. 다른 handler reason은 `ACTION_REJECTED`/`ACTION_FAILED` 같은 일반 status code로 제한한다. engine 결함은 `GameSubmissionError("ENGINE_ERROR")`로 감싸며 원래 문자열/handler/Event 정보를 Game 오류 메시지에 넣지 않는다. Research trace에는 error type과 존재하는 kernel result를 보존한다. commit 후 Event delivery 오류라면 성공 result가 남을 수 있고, commit 전 오류라면 result가 없다. action 완료 전 system Event delivery가 실패하면 system commit만 남고 해당 action result는 없다. 등록된 handler 내부의 registry 조회 실패도 engine 오류이며 `UNKNOWN_ACTION`으로 바꾸지 않는다. Game 생성 실패는 `OBSERVATION_UNAVAILABLE`, 미부팅/종료/재진입은 `GAME_UNAVAILABLE`이다.

ActionRequest가 아닌 객체는 TypeError다. live Game은 request/run/actor/Observation identity와 action type이 비어 있지 않은 내장 문자열인지, correlation이 None 또는 내장 문자열인지 검사한다. 잘못된 envelope이나 noncanonical payload는 kernel 진입 및 trace 저장 전에 `INVALID_REQUEST`로 거부한다. 순환 payload 등 JSON 정규화 중 recursion 실패도 같은 입력 오류로 처리한다. 정규화 불가능한 요청은 attempt sequence를 소비하지 않는다. 이 검사는 기존 trusted kernel/replay envelope schema를 변경하지 않는다.

ResearchView의 read API는 `world_snapshot`, `manifest`, `simulation_time`, `state_digest`, `rng_snapshot`, `observations`, `knowledge_history(actor_id)`, `action_traces`, `action_results`, `events`, `pending_scheduled_events`, `system_event_outcomes`다. 반환된 mutable JSON은 저장 상태와 분리되어 있다. 과거 World snapshot query, scenario editing, replay 실행 method와 외부 transport는 추가하지 않았다.

ActionTrace는 `attempt_sequence, request, result, controller_result, boundary_reason, error_type`를 보존한다. `request.based_on_observation_id`로 Research Observation과 연결하고 result는 해당 시도의 정확한 kernel ActionResult다. 같은 request ID가 다시 제출돼도 attempt sequence로 구분하며 idempotency를 구현한 것으로 보지 않는다. authority/registry 거부에는 kernel result가 없고 receipt와 boundary reason이 있다. ScheduledEvent는 trace를 만들지 않는다. 직접 kernel/replay 경로의 요청 원본은 기존처럼 호출자가 보관하며 ReplayInput/Report는 변경하지 않았다.
