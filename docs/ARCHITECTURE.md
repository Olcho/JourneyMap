# Architecture

## 1. 형태와 설계 기준

JourneyMap 0.x는 단일 배포 단위의 **modular monolith**다. 모듈 간 경계는 코드와 테스트로 강제하지만, 네트워크 서비스·외부 message broker·runtime plugin framework는 도입하지 않는다.

설계 우선순위는 다음과 같다.

1. 정보 경계와 상태 변경 권한
2. 결정론과 추적 가능성
3. 교체 가능한 Controller 및 도메인 Module
4. 최소 복잡성

## 2. 논리 계층

```text
Adapters / Composition Root
  ├─ Controller adapters (scripted, human, later LLM)
  ├─ Provider adapters
  ├─ persistence / CLI / research tooling
  └─ ModuleRegistry composition
                │
Application / Simulation Orchestration
  ├─ run lifecycle and turn loop
  ├─ perception → observation pipeline
  ├─ action dispatch / system-event dispatch
  └─ shared transition → recording
                │
Core Kernel                         Domain Modules
  identity, clock, RNG, scheduler   movement, knowledge, social,
  EventBus/contracts, registries ←→ survival, inventory, trade
  controller/action/observation      (own state and rules)
```

Core와 Module은 외부 API, 데이터베이스 기술, LLM SDK에 의존하지 않는다. adapters가 안쪽 계약을 구현하고 composition root가 조립한다.

## 3. Core 책임

Core에는 여러 도메인에서 안정적으로 공유하는 최소 개념만 둔다.

- `SimulationRun` identity와 lifecycle
- `EntityId`, 최소 Entity identity/type
- simulation clock과 time value
- seed 기반 RNG service 및 RNG draw 추적 규칙
- deterministic scheduler
- synchronous deterministic EventBus와 Event envelope
- `ScheduledEvent` envelope와 System/Event Handler registry
- `ActionRequest`, `ActionResult`, `Observation` envelope
- `ModuleRegistry`, `ActionRegistry`
- `Controller` interface
- transition/causation/correlation identity

Core는 hunger, money, weather, combat, 상품, bridge 같은 도메인 타입을 알지 않는다. Actor/Entity도 기능별 상태를 모두 가진 거대 객체가 아니다.

## 4. Module 책임과 소유권

각 Module은 자신의 상태 schema, command/action handler, event 구독, perception 규칙 또는 observation contributor, invariants와 migration을 소유한다.

0.1 후보:

- **movement:** Location, Route, 위치 component, 이동 가능성·소요 시간
- **knowledge:** actor별 KnowledgeRecord, 출처·획득 시각·정정 계보. M4는 confidence 모델을 추가하지 않는다.
- **social:** ASK/INFORM/REQUEST 규칙과 정보 전달
- **survival:** 최소 자원, REST/CONSUME 효과
- **inventory:** 소유·수량·이전 원자성
- **trade:** offer/price와 BUY 판정; inventory에 명시적으로 의존

모듈 상태는 모듈 소유 테이블/component에 저장한다. 다른 모듈이 이를 직접 수정하지 않고 공개된 command/query 계약 또는 Event를 사용한다.

### 모듈 의존성

- dependency는 `ModuleRegistry` metadata에 선언하고 시작 시 누락·순환을 검증한다.
- 0.1 기본 방향은 `trade → inventory`, `social → knowledge`, `survival → inventory`다.
- movement에서 나온 위치/이동 event를 knowledge가 구독할 수 있지만, movement는 knowledge를 알 필요가 없다.
- 양방향 호출이 필요해 보이면 더 작은 중립 계약이나 event로 분리한다.

## 5. 상태와 정보 흐름

### 두 입력 경로와 공통 mutation 경계

```text
World Truth
  → actor-scoped Perception
  → PerceivedFact / PerceptionContext
  → ordered ObservationContributors
  → immutable Observation
  → Controller
  → ActionRequest
  → ActionRegistry handler
  → validate (no mutation)
  → resolve with clock/RNG
  → atomic mutation
  → ordered Events + ActionResult
  → knowledge update

ScheduledEvent / naturally triggered World Event
  → System/Event Handler
  → validate and resolve (no ActionRequest)
  → shared deterministic transaction/mutation boundary
  → ordered Events + handler outcome/provenance
```

`ActionRequest`는 Actor/Controller의 행동 의도만 나타내는 연구상 first-class record다. ScheduledEvent나 자연 발생 World Event를 ActionRequest로 표현하지 않는다. 두 입력 경로의 registry와 handler 계약은 분리하지만 canonical state write, transaction, RNG, ordering과 logging 기반은 공유한다. 이로써 system 사건이 agent action 통계를 오염시키지 않는다.

ObservationContributor는 raw persistence handle이나 unrestricted World Truth를 받지 않는다. actor-scoped `PerceptionContext`만 받으며 contributor 순서는 `(priority, module_id, contributor_id)`와 같이 안정적으로 정렬한다.

### Action 확장

각 Module은 actor action type과 버전별 handler를 `ActionRegistry`에 등록한다. system event type은 별도의 System/Event Handler registry에 등록한다. 두 handler 종류 모두 payload validation과 domain validation을 구분하고, mutation 전에 전체 실패 조건을 확인한다. 어느 경로에도 중앙 `if/elif` resolver를 만들지 않는다.

### Event 처리

용어상 `ScheduledEvent`는 아직 발생하지 않은 숨은 trigger이고, 자연 발생 World Event trigger는 현재 조건에서 엔진이 도출한 system 입력이다. `Event`는 mutation 결과로 Event Log에 append되는 이미 발생한 immutable record다. System/Event Handler가 trigger를 처리한 뒤 결과 Event를 기록·배포하며, 미래 trigger를 발생 사실처럼 Event Log에 넣지 않는다.

EventBus는 in-process, synchronous, deterministic이다. subscriber 순서는 `(priority, module_id, subscriber_id)`로 완전 정렬하고 등록 순서나 hash iteration에 기대지 않는다. Event에는 최소한 run, event sequence, simulation time, event type/schema version, source, causation, correlation과 actor/entity references가 있다.

0.1은 full event sourcing이 아니다. canonical snapshot/state가 권위이고 Event log는 감사·연구·재현 검증 기록이다. actor 경로는 ActionRequest/ActionResult를, system 경로는 ScheduledEvent 또는 발생 trigger와 handler outcome을 provenance로 남긴다. replay 입력의 권위는 기록된 ActionRequest stream과 versioned 초기 시나리오/schedule이며 두 경로의 Event ordering과 state digest를 함께 검증한다.

## 6. Controller, Provider, Memory

```text
Controller: Observation → ActionRequest
Provider: versioned prompt/model parameters → raw model output
MemoryPolicy: prior allowed records → controller memory context
```

`ScriptedController`, `UtilityController`, `HumanController`, `LLMController`는 같은 Controller 계약을 구현한다. `LLMController`는 Provider와 parser를 조합하지만 엔진 내부 객체나 Research/Debug API를 받지 않는다.

MemoryPolicy는 NoMemory, recent/episodic, reflection 등 실험 조건을 교체할 수 있는 경계다. 다만 구체적인 고급 memory 구현은 필요해질 때 추가하며 0.1 Core에 선제적으로 넣지 않는다. memory context 역시 actor가 합법적으로 획득한 정보만 사용할 수 있다.

## 7. 결정론 규칙

- simulation 결과에 wall clock, 무작위 UUID, 비정렬 collection iteration을 사용하지 않는다.
- 모든 난수는 run seed에서 파생된 RNG service를 통해 소비한다.
- 동일 시각 작업은 scheduler key로, 파생 Event와 subscriber는 명시적 key로 정렬한다.
- ID가 결과 비교에 포함되면 seed/sequence에서 결정적으로 생성한다.
- persistence commit 경계는 하나의 action/scenario transition 단위다.
- engine/schema/module version과 초기 state digest를 run metadata에 보존한다.

## 8. 권한과 보안 경계

- Game/Controller path는 actor-scoped Observation 전달·ActionRequest 접수만 허용한다.
- Research/Debug path는 World Truth, 전체 Event log, 다른 actor의 Knowledge를 읽을 수 있으므로 별도 capability/port로 격리한다.
- Controller 프로세스 또는 객체 그래프에 debug repository를 주입하지 않는다.
- 자연어 claim은 지식 또는 통신 payload일 뿐 canonical truth mutation command가 아니다.

## 9. M0 기술 기준

- Python 3.12 계열을 사용한다.
- 테스트는 pytest, lint/format은 Ruff, 정적 타입 검사는 mypy를 사용한다.
- 표준 라이브러리를 우선하며 M0에는 FastAPI와 LLM SDK를 도입하지 않는다.
- 외부 transport보다 in-process contract를 우선한다.
- persistence는 simulation kernel이 의존하는 interface 뒤에 둔다. SQLite는 표준 라이브러리 `sqlite3` 기반의 필요 최소 수준부터 사용하되 DB schema나 ORM model이 Core domain을 지배하지 않게 한다.
- SQLAlchemy/Alembic은 실제 persistence 및 migration 요구가 구체화되는 마일스톤에서 도입 여부를 결정한다.

snapshot 주기와 concrete LLM provider 같은 나머지 선택은 해당 마일스톤까지 연기하며 위 경계와 결정론 불변식을 바꾸지 않는다.

## 10. M0 패키지 의존 규칙

M0의 물리 패키지는 다음 단방향 의존을 사용한다.

```text
bootstrap (composition root) → adapters → core contracts
                            ↘ core kernel
```

- `journeymap.core`는 Python 표준 라이브러리와 Core 내부 계약만 import한다. `sqlite3`, infrastructure adapter, composition root, HTTP/LLM/ORM package를 import하지 않는다.
- `journeymap.adapters`는 Core가 정의한 port를 구현하며 구체 기술을 소유한다. M0 SQLite adapter는 연결 수명주기만 구현하고 도메인 schema를 선제 정의하지 않는다.
- `journeymap.bootstrap`만 구체 adapter와 Core를 조립한다.
- 향후 domain module은 자신의 상태와 규칙을 소유하고 metadata에 직접 dependency를 선언한다. registry는 시작 전에 누락, 중복과 순환 dependency를 거부한다.
- architecture test가 위 역방향 import와 금지된 runtime dependency를 검사한다.

코드 변경 시 관련 테스트와 함께 계약·상태 소유권·계층·milestone 범위에 영향을 받는 문서를 같은 변경에서 갱신한다.

## 11. M1 결정적 커널 규칙

- simulation time은 wall clock과 무관한 0 이상의 정수 tick이다. tick의 현실 시간 단위는 scenario가 정하며 Core는 해석하지 않는다.
- RNG는 run seed로 초기화되는 명시적 SplitMix64 구현을 사용하고 draw count를 추적한다. handler resolution은 복제된 RNG를 사용하며 transition commit에 성공할 때만 원본 RNG state에 반영한다.
- scheduler는 `(due_time, priority, insertion_sequence)`로 정렬한다. 같은 tick에 system input과 recorded actor action이 있으면 미리 enqueue된 due system input을 모두 먼저 처리한다. handler/subscriber 실행 중 kernel mutation API 재진입은 거부하므로 system handler가 같은 tick event를 직접 추가할 수 없다. 같은 tick input은 transition 시작 전에 enqueue해야 하며 그 경우에도 위 정렬을 따른다.
- EventBus는 in-process synchronous callback만 제공하며 `(priority, module_id, subscriber_id)` 순서로 호출한다. 각 subscriber에는 저장된 envelope과 payload가 분리된 복사본을 전달하며 subscriber는 kernel mutation API에 재진입할 수 없다. commit 뒤 subscriber가 예외를 내면 이후 subscriber와 남은 Event delivery를 중단하고 event ID와 subscriber ordering key가 있는 `EventDeliveryError`를 호출자에게 전달한다. `advance_to`는 그 commit tick에서 중단되며 이미 commit된 state/Event/handler result와 소비된 ScheduledEvent는 rollback하거나 다음 호출에서 재실행하지 않는다.
- actor action과 system event는 각각 `ActionRegistry`, `SystemEventRegistry`의 정확한 `(type, schema_version)` key로 dispatch한다. M1 `ActionRequest`는 필수 opaque Observation reference를 포함한 최소 actor-intent envelope일 뿐 Observation 자체나 action type별 domain payload 의미를 정의하지 않는다. `ScheduledEvent`는 ActionRequest로 변환하지 않는다.
- validate/resolve context에는 canonical state의 분리된 복사본만 전달한다. 두 handler 경로 모두 `TransitionPlan`을 반환하고 동일한 commit 함수만 top-level canonical JSON state, RNG state, transition sequence와 Event sequence를 변경한다.
- transition이 commit 전에 실패하면 canonical state, logical clock, RNG, Event/transition sequence, handler result/outcome과 scheduler queue/counter를 바꾸지 않는다. 실패한 ScheduledEvent는 queue head에 남고 예외는 즉시 호출자에게 전달되므로 한 번의 `advance_to` 호출에서 tight retry하지 않는다. 다음 명시적 호출은 같은 event를 다시 한 번 시도한다. 이미 성공한 앞선 transition이나 commit 이후 Event delivery 실패는 이 rollback 규칙의 대상이 아니다.
- transition/event ID는 wall clock이나 UUID가 아닌 run-local sequence에서 준비하고 transition commit 시 함께 소비한다. Event envelope은 source, transition, causation과 correlation을 기록한다. state digest는 future schedule, Event log와 연구 metadata를 제외한 canonical JSON state를 UTF-8, key 정렬, compact JSON으로 직렬화한 SHA-256이다. canonical state는 string-key JSON object와 JSON scalar/list/object만 허용하고 non-finite float, set, bytes, tuple, custom object와 non-string mapping key는 거부한다.
- replay는 불변 RunManifest/initial state, versioned ScenarioSchedule, tuple에 기록된 ActionRequest 순서와 종료 tick으로 매번 새 kernel을 구성한다. ScenarioSchedule은 `(scenario_id, scenario_version)`이 manifest와 일치해야 한다. report에는 wall-clock timestamp, filesystem path, object repr나 environment metadata를 넣지 않으며 handler 결과, deterministic identity, Event ordering과 final state digest를 함께 비교한다.

M1의 event/state 기록은 in-memory kernel과 ReplayReport에만 존재한다. 구체 SQLite record schema와 snapshot 정책은 해당 persistence 요구가 명확해지는 후속 milestone까지 추가하지 않는다.

## 12. M2 Entity와 timed action

- `core.entities.Entity(entity_id, entity_type)`는 run-local 최소 identity다. canonical `entities` 값은 ID별 identity record이며 위치나 도메인 속성을 포함하지 않는다.
- `modules.movement`가 `Location`, 단방향 `Route`, `ActorPosition`, 초기 상태 builder, MOVE handler와 `movement` canonical 값을 소유한다. ModuleRegistry에는 `MovementModule`을, ActionRegistry에는 해당 module의 `register_actions`로 MOVE v1을 명시적으로 등록한다. WAIT v1은 도메인 상태가 없는 `core.actions.WaitHandler`를 명시적으로 등록한다. 자동 plugin discovery나 범용 ECS는 없다.
- 기존 `ActionHandler`의 validate/resolve와 `TransitionPlan`을 유지한다. 선택적인 `TimedActionHandler`는 `prepare(request, ValidationContext) -> ActionTiming`과 `validate_completion(request, ActionTiming, ValidationContext)`를 추가한다. prepare는 start validation 뒤에 실행하며 RNG 없이 양의 정수 duration과 복사된 JSON 검증 데이터를 만든다. timing은 파생된 일시적 값이며 canonical state나 scheduler input이 아니다.
- kernel은 M1처럼 제출 tick까지 due system event를 먼저 처리한 뒤 시작 검증을 수행한다. 시작 검증/prepare 실패는 `REJECTED`이고 추가 시간을 소비하지 않는다. 원자성 비교 기준은 제출 tick까지 독립적인 세계 진행이 끝난 시점이다. 미래 제출 tick까지의 진행과 그 이전 system commit은 action의 성공 mutation이 아니다.
- 시작에 성공하면 `started_at + duration`까지 기존 advance 로직을 사용한다. 완료 tick을 포함한 system input을 `(due_time, priority, insertion_sequence)` 순서로 모두 처리한 뒤 완료 검증을 한다. 이후 resolve는 최신 state 복사본과 최신 RNG의 clone으로 TransitionPlan을 만들며 기존 공통 commit 함수가 이를 적용한다. action type별 kernel 분기는 없다.
- 완료 검증 실패는 `FAILED`다. 경과 시간과 독립적으로 commit된 system state/RNG/Event/ID를 유지하고 action 자체의 mutation/Event/transition ID는 만들지 않는다. 실패 결과는 request ID로 연결되므로 별도 ID sequence를 소비하지 않는다. 성공은 `SUCCEEDED`와 기존 transition identity를 기록한다.
- MOVE는 시작 시 actor/position/route/location, origin 일치, 현재 passability와 양의 정수 cost를 확인한다. 완료 시 이를 다시 확인하고 시작 시 origin/destination과도 비교한다. 유효한 cost 변경은 확정된 duration을 바꾸지 않으며, 폐쇄 후 완료 전에 다시 열렸다면 현재 passability를 기준으로 성공할 수 있다. 이동 중간 위치는 없고 완료 성공 시에만 ActorPosition을 변경한다.
- 전체 submit/advance 동안 handler와 subscriber의 kernel mutation API 재진입을 금지한다. 예상된 `ActionValidationError`만 연구 결과로 변환한다. 예상 밖 handler 결함이나 EventDeliveryError는 기존처럼 전파하고, 이미 commit된 세계는 유지한다. 중단된 timed action의 자동 재개/재시도 runtime은 없다.
- 단일 동기 action을 끝낸 뒤 다음 요청을 받는다. 다음 `submitted_at`은 현재 tick 이상이어야 하고 replay 종료 tick은 마지막 완료 tick 이상이어야 한다. duration·completion tick은 입력에서 결정적으로 파생되므로 ReplayInput을 확장하지 않는다.

위 M2 kernel의 `based_on_observation_id`는 계속 opaque reference다. M3 live 권한 검증은 아래 별도 application 계층이 담당한다.

## 13. M3 Perception / Observation / Knowledge 경계

물리 의존 방향은 `bootstrap → application → core + modules`다. `core.observations`는 도메인 중립 PerceptionContext/Observation과 contributor callable 계약, `core.controller`는 GamePort와 제한된 ControllerActionResult를 정의한다. Core는 application이나 knowledge 의미를 import하지 않는다. `application.perception`이 trusted filtering을 조립하며 `modules.movement.perception`은 자기 위치의 allowlist projection을 소유한다. movement는 knowledge를 import하지 않는다.

Perception은 현재 snapshot에서 actor의 존재와 identity를 확인하고 자신의 유효한 `location_id`만 추출한다. Entity/position/location record의 추가 필드, 다른 actor 위치, route graph/passability와 보이지 않는 사실을 복사하지 않는다. Observation을 요청한다고 시간을 진행하거나 due system event를 실행하지 않는다. 현재까지 commit된 truth만 읽는다.

Contributor 입력은 `PerceptionContext(run_id, actor_id, simulation_time, perceived, known)`이다. 기본 `perceived`는 `self: {entity_id, entity_type}`, `movement: {location_id}`이며 position이 없으면 movement는 빈 object다. `known`은 해당 actor의 명시적 기존 KnowledgeRecord JSON 목록인 `{records: [...]}`다. 이 context에는 raw world/module record, store/query handle, scheduler, future event, RNG/seed, Event log, 다른 actor 지식, Research capability가 없다. 신뢰된 초기 지식 작성자는 actor에게 허용된 주장과 source만 제공해야 한다.

`ObservationPipeline.register`는 `(priority, module_id, contributor_id)`를 완전 정렬 key로 사용하고 중복 key를 거부한다. contributor는 매번 분리된 context만 받아 JSON section을 반환한다. 등록 목록 snapshot을 정렬해 순서대로 실행하고, 출력도 즉시 canonical 검증·복사한다. context나 다른 출력의 mutation alias가 없으며 실패하면 record와 sequence를 소비하지 않는다. contributor는 trusted formatter이며 추론 엔진이 아니다. 외부 secret이나 서비스 handle을 closure로 주입하지 않는 것은 composition의 책임이다.

ObservationHistory는 run별 성공한 generation 순서대로 append한다. envelope은 frozen이며 content는 내부 canonical JSON 문자열로 보관하고 매 조회에 새 JSON 값을 반환한다. contributor가 받은 임시 context의 nested container는 수정 가능하지만 이후 contributor/Observation에 영향을 주지 않는다. record/sequence는 kernel canonical state, RNG, scheduler 및 transition/event sequence와 독립적이다.

`modules.knowledge`는 KnowledgeRecord와 별도 in-memory 초기 ledger를 소유한다. 이 ledger는 canonical World Truth에 포함하지 않으며 kernel state digest에도 포함하지 않는다. 명시적 source와 stable ID를 가진 초기 입력만 허용하고, 지식 부재는 UNKNOWN인 빈 조회 결과다. 상충 주장과 supersession 계보를 함께 보존하며 truth를 참조해 기존 주장을 rewrite하지 않는다. M3에는 confidence·추론·runtime append·직접 관찰 projection을 구현하지 않았다. M4는 아래 committed-Event projection으로 확장한다. Knowledge를 canonical state에 넣거나 EventBus subscriber/Observation builder에서 mutation하지 않는다.

SimulationApplication은 kernel을 Controller에 전달하지 않고 actor를 고정한 두 callback으로 GamePort를 구성한다. 별도 ResearchView는 kernel과 application history를 읽으며 GamePort/Observation에는 ResearchView를 넣지 않는다. 이 경계는 신뢰된 Python application에서의 API/capability 분리다. private field·closure·traceback introspection 또는 악성 Python 실행을 막는 sandbox가 아니다.

Live submit은 run/actor/실제 Observation의 소유권과 현재 제출 tick을 검증한다. kernel은 기존 opaque provenance를 유지하며 recorded ActionRequest-only replay에 Observation history를 요구하지 않는다. application ActionTrace는 정규화된 live 요청마다 별도의 attempt sequence와 request/result를 보존한다. 기존 kernel에는 ActionRequest history가 없으므로 직접 trusted kernel/replay 경로의 request는 M1/M2처럼 호출자의 ReplayInput이 소유한다. system input은 action trace를 만들지 않는다.

Core는 `ActionHandlerNotFoundError(HandlerNotFoundError)` 하위 타입을 kernel의 최초 actor handler 조회 실패에만 사용한다. ActionRegistry/SystemEventRegistry 자체는 기존 `HandlerNotFoundError`를 유지한다. 따라서 등록된 handler 내부 조회 오류나 system dispatch 결함이 live Game에서 `UNKNOWN_ACTION`으로 오인되지 않는다. 기존 상위 예외 catch와 메시지는 유지하며 더 큰 예외 계층은 추가하지 않았다. kernel submit 변경은 이 좁은 예외 변환뿐이며 dispatch 순서, 시간 진행, commit, scheduler, RNG, EventBus와 ReplayInput/Report 의미는 그대로다.

## 14. M4 scenario와 runtime Knowledge projection

`bootstrap`이 `scenarios.alderwick`, application, movement/knowledge module을 명시적으로 조립한다. scenario는 fixture, bridge truth/handler, 가시성, bridge acquisition 규칙을 소유한다. movement는 scenario/knowledge를 import하지 않으며 `close_routes(state, route_ids)`로 검증한 detached movement candidate를 제공한다. scenario handler는 이를 bridge candidate와 한 TransitionPlan에 넣는다. Core의 commit/dispatch/replay 코드는 변경하지 않았다. AlderwickModule 0.4.0은 movement와 knowledge에 의존하고 KnowledgeModule은 0.4.0이다.

`KnowledgeProjection(initial, events, projector)`는 전체 committed Event log를 받아 만드는 독립 읽기 snapshot이다. eager materialization, mutable cursor, EventBus subscription이 없다. 호출마다 전체 Event를 순회하고 기록을 검증·정렬하며 저장된 World Truth는 조회하지 않는다. 작은 M4 run에서 복잡한 cache 복구보다 이 방식이 작고 명확하다. 초기 기록과 합친 결과는 기존 ledger의 run/ID/time/supersession 검증을 재사용하며 원래 초기 ledger를 수정하지 않는다.

scenario v1은 초기 bridge가 intact이고 condition 변경이 한 번의 collapse뿐이다. projection은 Event sequence 순서로 `BridgeCollapsed` 발생 여부를 접는다. 해당 Event의 당시 witness만 획득하며, 이후 관찰 위치로 완료된 `ActorMoved`는 나중 직접 발견의 근거다. 같은 tick도 system Event가 먼저 기록되므로 그 뒤 도착자는 witness가 아닌 discovery다. 현재 위치·최종 World snapshot·future schedule·observe 호출 횟수는 과거 획득 판정에 사용하지 않는다. bridge 수리/반복 붕괴는 새 scenario/rule version이 필요한 후속 범위다.

`SimulationApplication.knowledge_snapshot()`은 kernel의 committed Event 복사본과 명시적 초기 Knowledge를 연결한다. `_observe()`가 이 snapshot을 `perceive()`에 전달하고 actor view를 `PerceptionContext.known`에 넣는다. 따라서 runtime Knowledge는 ResearchView뿐 아니라 다음 GamePort Observation에도 반영된다. observe는 기록을 mutation하지 않는다. generic M3 composition은 projector/extension을 생략하므로 기존 동작을 유지한다.

trusted `PerceptionExtension(world_copy, actor_id)`만 raw snapshot을 받을 수 있다. 기존 self/movement scope를 덮어쓸 수 없으며 반환 JSON도 복사한다. Alderwick은 actor 위치를 검사한 뒤 보이는 bridge ID/condition만 제공한다. `scenarios.alderwick.contributors`는 Core의 safe context 계약만 import한다. ScriptedController도 Observation/ActionRequest/JSON 계약만 import하고 객체 필드·callback·scenario/kernel/Research handle이 없다. 신뢰된 Python composition의 경계이며 악성 introspection sandbox는 아니다.

commit 전 system 실패는 기존 state/clock/RNG/Event/sequence/schedule rollback을 따른다. commit 후 EventDeliveryError는 이미 발생한 world/Event/outcome을 보존한다. projection은 delivery와 무관하게 committed log에서 복구한다. projection 예외는 Research 읽기에서 전파하고 Game observe에서는 `OBSERVATION_UNAVAILABLE`로 제한하며 partial snapshot/Observation/sequence를 게시하지 않는다. world commit을 rollback하거나 replay하지 않는다.

## 15. M5 social authority, projection과 NPC

`modules.social`은 정확한 v1 payload, 공통 timed interaction handler, social Event 검증, `project_informed_knowledge`, `perceive_social`과 contributor를 소유한다. SocialModule 0.5.0은 movement/knowledge에 의존하고 KnowledgeModule은 0.5.0이다. Core kernel/handlers/replay schema와 M4 scenario direct projector callable은 변경하지 않았다. canonical social/Knowledge/inbox state 또는 EventBus mutation subscriber는 없다.

live authority는 `application.session → application.social.validate_claim`이다. 기존 run/actor/Observation/current tick 검증 뒤 v1 payload를 확인하고, INFORM에서 현재 `for_actor(sender)` view와 저장된 원본 Observation의 knowledge section을 교차 검증한다. ID뿐 아니라 record JSON 전체가 동일해야 하며 learned_at은 제출 tick 및 Observation tick 이하이어야 한다. 같은 tick에 Observation 뒤 새로 획득한 기록도 Observation membership 검사에서 거부한다. 과거 Observation의 실제 기록은 계속 사용할 수 있다. schema/형태 오류는 `INVALID_PAYLOAD`, 소유권·미획득·미노출은 같은 `INVALID_CLAIM_REFERENCE`로 처리하여 타인 지식 존재 여부를 노출하지 않는다.

reply는 같은 run의 앞선 `ActorAsked`여야 하며 질문자/대상, claim subject/predicate와 현재 sender/target이 대응해야 한다. 그 ASK가 실제 sender Observation에도 있어야 한다. INFORM duration 중 새로 배운 claim은 시작 authority를 대신할 수 없다. 지식은 append-only라 승인된 기존 record는 duration 동안 소멸하지 않는다. handler는 Knowledge capability를 받지 않고 schema, sender/target Entity와 movement의 공개 position projection으로 시작·완료 조건을 검증한다.

`KnowledgeProjection(initial, events, projector=None, event_rules=())`는 기존 full-log direct projector를 지원한다. 그 결과의 committed source를 먼저 검증한 뒤 source Event별로 분배한다. ordered Event마다 direct 기록을 누적하고 명시된 tuple 순서의 `KnowledgeEventRule(event, previous_events, accumulated_ledger)`을 실행한다. rule에는 현재 Event, 이전 Event prefix 및 그 시점까지의 복사된 기록만 준다. 각 rule 결과는 현재 Event ID/tick에 연결되어야 하며 run/unique ID/supersession 검증 후 다음 rule/Event에 전달된다. 어느 rule도 미래 Event나 최종 World snapshot을 받지 않는다. direct 규칙의 collapsed 상태·witness/discovery/dedup 의미와 ID는 scenario가 계속 소유한다.

social rule은 sender의 prefix Knowledge에서 claim을 찾아 새 receiver 기록을 만든다. current Event가 없는 unknown/future/타 actor claim, 잘못된 version/source/reply는 partial snapshot 대신 오류다. receiver의 source는 `INFORMED`/ActorInformed이고 payload의 claim_record_id를 거쳐 여러 전달 단계를 재구성한다. 초기·직접·간접 주장 사이에 자동 winner나 supersession을 만들지 않는다. 전체 log에서 매번 재구성하는 작은 run용 구현이며 cache/cursor, 영속 resume, 범용 rule engine은 없다.

`social=True` application의 trusted observe 경로는 `perceive_social(committed_events, actor)` 결과를 reserved `PerceptionContext.perceived.social`에 넣는다. contributor 호출 전에 전역 이력은 제거되고 자기 incoming interaction의 allowlist JSON과 답변 완료 여부만 남는다. 다른 사람의 대화는 같은 장소의 비참여자에게도 노출하지 않는다. INFORM 수신자는 sender의 private record ID를 social section에서 받지 않으며 자신의 Knowledge source_ref로 전달 Event를 연결한다. canonical inbox나 Controller의 global event capability는 없다.

`SocialNpcController`는 Observation만 읽는 상태 없는 adapter다. 미응답 ASK에 matching claim이 정확히 하나일 때 INFORM하며 복수 matching records에는 응답을 보류한다. 이는 지식의 진위 판정이 아니다. 명시적 초기 return-route/inquiry 지시를 사용하고 정보 수신 후 WAIT한다. `scenarios.alderwick.social.NPC_ACTIVATIONS`는 trusted example/application 호출자의 고정 활성화 순서다. Controller에는 이 순서, kernel, scenario, Research handle을 넣지 않는다. system handler가 actor 행동을 실행하지 않으며 activation마다 observe→decide→GamePort.submit을 사용한다.

engine replay는 이미 live authority를 통과하여 kernel에 전달된 ActionRequest stream을 신뢰한다. kernel-only replay는 Observation 소유권을 재검증하지 않는 기존 privileged 경로다. forged raw replay input을 live 요청으로 취급하지 않는다. boundary-denied attempts는 ActionTrace에 남고 engine input에 넣지 않으며, kernel REJECTED/FAILED 요청은 포함한다. replay 후 동일 초기 지식과 Events로 prefix provenance를 별도 검증한다. Observation/Knowledge/NPC output을 ReplayInput에 추가하거나 policy를 재실행하지 않는다.

## 16. M6 resource ownership와 cross-module atomicity

InventoryModule/SurvivalModule/TradeModule은 각각 0.6.0이다. inventory는 `models.py`의 identity/quantity validator와 `transitions.py`의 detached decrement/transfer helper를 제공한다. inventory가 survival/trade를 import하지 않는다. survival은 inventory의 models/transitions에, trade는 같은 inventory contract와 movement의 `perceive_position` query에만 의존한다. metadata에도 survival→inventory, trade→inventory/movement를 명시한다. Core, movement, knowledge와 social 구현은 M6 때문에 변경하지 않는다.

BUY handler는 inventory transfer candidate를 먼저 구성하고 trade-owned payment candidate를 만든 뒤 `{inventory, trade}`와 ItemPurchased를 하나의 TransitionPlan으로 반환한다. payment helper가 buyer 후보를 구성한 다음 seller 검증에서 실패해도 반환 전의 detached 값일 뿐 canonical write는 없다. CONSUME는 inventory decrement 후 survival-owned candidate와 ItemConsumed를 `{inventory, survival}` plan에 모은다. helper에는 kernel/transaction/persistence capability가 없고 다른 module의 raw quantity schema를 handler에서 복제하지 않는다. REST와 SurvivalTick은 survival key만 변경한다. generic transaction coordinator, module write registry나 effect engine은 없다.

기존 `SimulationKernel._commit_plan`이 모든 candidate key와 Event를 직렬화·검증한 뒤 state/RNG/sequence/Event를 함께 적용한다. 시작·완료 검증의 ActionValidationError는 REJECTED/FAILED이며, resolve/직렬화 결함은 기존 M2처럼 예외이고 가짜 ActionResult를 만들지 않는다. timed duration의 독립 system commits와 시간은 보존한다. commit 후 delivery 실패에서도 두 module의 성공 state/Event/result가 남는다. 새로운 rollback 로직은 없다.

SurvivalTick v1 handler는 빈 payload의 system input을 받아 모든 survival owner를 검증하고 ID 정렬 순서로 hunger +2/fatigue +1 및 SurvivalAdvanced를 한 system transition에서 커밋한다. 미래 tick은 canonical state가 아닌 versioned scenario schedule에만 존재한다. Alderwick은 tick 1–20을 명시적으로 구성하며 recurring scheduler framework는 없다. 동률에서 priority 0의 bridge collapse, priority 10의 survival input, 마지막 actor completion 순서다. REST/CONSUME resolve는 최신 survival state를 사용하며 시작 상태를 저장하지 않는다.

trusted scenario `perceive_resources`는 각 resource module의 scoped query와 기존 bridge perception을 조합한다. inventory/survival/trade raw tables나 효과 설정을 contributor에 전달하지 않는다. `resource_contributors.py`는 Core JSON/PerceptionContext만 import하고, safe context를 세 개의 `(40, module_id, resources)` section으로 조립한다. offer는 공개 listing이며 seller의 정확한 stock이나 private wallet을 공개한다는 의미가 아니다. malformed private wallet/stock/effect 설정도 그 필드를 읽지 않는 다른 actor의 Observation을 바꾸지 않는다.

bootstrap의 `resources=True`만 fixture, 세 모듈, handler, perception을 연결한다. manifest/schedule은 `alderwick/resources-1`로 기존 `alderwick/1`과 구분한다. schedule 설치는 호출자 책임이며 ReplayHarness는 등록을 중복하지 않는다. generic create_application은 자동 resource section을 추가하지 않는다. social opt-in을 함께 사용할 수 있으며 M4 direct/M5 informed projection과 Game authority 경계를 유지한다. M6 action reason allowlist만 application.session에 추가한다.
