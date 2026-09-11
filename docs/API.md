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

reply_to_event_id는 앞서 받은 ActorAsked ID다. 원본 Observation의 social section에 해당 interaction이 있어야 하며 질문자=INFORM target, 질문 대상=INFORM sender, subject/predicate=claim topic이어야 한다. 실패는 `INVALID_REPLY_REFERENCE`. 답변 1회만 허용하는 idempotency 제약은 없고 NPC가 최신 Observation의 answered를 보고 중복 답변을 피한다.

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

시작 검증 실패는 REJECTED이며 제출 tick까지의 독립 세계 진행 이후 추가 시간을 소비하지 않는다. 완료 검증 실패는 FAILED로 시간·system commits를 유지하고 action 성공 mutation/Event를 남기지 않는다. resolve/serialization 예외는 기존 engine defect 경로이며 kernel ActionResult를 만들지 않고 live Game은 ENGINE_ERROR와 result 없는 trace를 남긴다. commit 뒤 delivery 예외는 두 module state/Event/성공 result와 연결된 trace를 보존한다. 자동 retry/idempotency는 없다.

Observation의 `(40, inventory, resources)` content는 `{inventory: {item_id: quantity}}`, `(40, survival, resources)`는 `{survival: {hunger, fatigue}}`, `(40, trade, resources)`는 `{trade: {wallet: int, offers: [{offer_id, seller_id, item_id, unit_price, active}]}}`다. item ID와 offer ID 순서로 안정 정렬하며 같은 priority의 module 순서는 기존 pipeline 규칙이다. active local 타인 offer만 공개하고 자신의 seller listing은 제외한다. 다른 actor의 전체 inventory/wallet/survival, remote offer, effect configuration, future schedule, raw tables와 Research log는 contributor에 도달하지 않는다. 반복 observe는 canonical world/time/RNG/schedule을 변경하지 않는다.

ReplayInput/ReplayHarness/ReplayReport와 Core kernel schema는 변경하지 않았다. kernel에 전달된 성공/REJECTED/FAILED ActionRequest와 versioned schedule로 결과, system outcomes, Event/order, world/digest, time/RNG draw count를 비교한다. live Observation과 Knowledge는 추가 replay 입력이 아니며 Knowledge는 기존 M4/M5 committed Event projection으로 별도 검증한다. 예제는 trusted caller가 고정 resource path를 GamePort로 제출하는 최소 실행이며 새 NPC policy/LLM controller는 없다. M7의 최종 schema compatibility, idempotency, budget/redaction/conformance 안정화는 남아 있다.
