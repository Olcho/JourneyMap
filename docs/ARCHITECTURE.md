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
- **knowledge:** actor별 KnowledgeRecord, 출처·획득 시각·확신도
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
