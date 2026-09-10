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
  "payload": { "destinationLocationId": "east-road" }
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
| `MOVE` | destination 또는 route ID | movement |
| `ASK` | target actor, topic ID | social |
| `INFORM` | target actor, claim reference/structured claim | social + knowledge |
| `REQUEST` | target actor, request kind/payload | social |
| `BUY` | seller, offer/item, quantity | trade |
| `CONSUME` | inventory entry, quantity | survival + inventory |
| `REST` | duration 또는 rest option | survival |
| `WAIT` | duration | core/application |

구체 schema는 M7에서 contract test와 함께 확정한다. 자연어는 `utterance` 같은 표현 필드로 포함할 수 있지만 action의 canonical 의미는 구조화된 필드가 결정한다.

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
