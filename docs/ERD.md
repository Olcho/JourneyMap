# ERD and Persistence Model

이 문서는 기술별 DDL이 아니라 0.1의 논리 데이터 모델과 소유권을 정의한다. 관계형 저장소를 가정한 표기는 예시이며 구현 기술이 바뀌어도 연구 핵심 기록의 정체성과 관계는 유지한다.

## 1. 분류 원칙

### Core persistence

실행 identity, 시간, entity identity, scheduler와 module version처럼 엔진 수명주기를 지탱하는 데이터다.

### Research-critical first-class records

`Event`, `Observation`, `ActionRequest`, `ActionResult`, `KnowledgeRecord`는 임의 component JSON이나 단일 log blob 안에 묻지 않는다. 각각 안정적 ID, run/time/sequence, schema version과 추적 관계를 가진 독립 레코드로 저장한다. 확장 payload는 JSON일 수 있지만 envelope과 핵심 index field는 명시적 column이다.

### Module-owned state/tables

Location/Route, actor position, inventory 등 domain canonical state는 소유 Module만 변경한다. 범용 Entity row에 모든 상태를 넣지 않는다.

## 2. 논리 ERD

```mermaid
erDiagram
    SIMULATION_RUN ||--o{ ENTITY : contains
    SIMULATION_RUN ||--o{ SCHEDULED_EVENT : schedules
    SIMULATION_RUN ||--o{ EVENT : records
    SIMULATION_RUN ||--o{ OBSERVATION : emits
    SIMULATION_RUN ||--o{ ACTION_REQUEST : receives
    ACTION_REQUEST ||--|| ACTION_RESULT : resolves_to
    OBSERVATION ||--o| ACTION_REQUEST : prompts
    ACTION_REQUEST ||--o{ EVENT : causes
    SCHEDULED_EVENT ||--o{ EVENT : causes
    EVENT ||--o{ KNOWLEDGE_RECORD : informs
    ENTITY ||--o{ OBSERVATION : receives
    ENTITY ||--o{ ACTION_REQUEST : requests
    ENTITY ||--o{ KNOWLEDGE_RECORD : knows

    ENTITY ||--o| ACTOR_POSITION : has
    LOCATION ||--o{ ACTOR_POSITION : hosts
    LOCATION ||--o{ ROUTE : origin
    LOCATION ||--o{ ROUTE : destination
    ENTITY ||--o{ INVENTORY_ENTRY : owns
    ITEM_DEFINITION ||--o{ INVENTORY_ENTRY : classifies
    ENTITY ||--o| SURVIVAL_STATE : has
    ENTITY ||--o| WALLET : holds

    SIMULATION_RUN {
      string run_id PK
      string scenario_id
      string scenario_version
      string engine_version
      string seed
      datetime simulation_time
      string initial_state_digest
      string status
    }
    ENTITY {
      string entity_id PK
      string run_id FK
      string entity_type
      string display_name
    }
    SCHEDULED_EVENT {
      string scheduled_event_id PK
      string run_id FK
      datetime due_time
      int priority
      int insertion_sequence
      string event_type
      int schema_version
      string handler_id
      json event_payload
      string status
      datetime processed_at
      string result_code
      string state_digest_after
    }
    EVENT {
      string event_id PK
      string run_id FK
      int event_sequence
      datetime occurred_at
      string event_type
      int schema_version
      string source_kind
      string source_ref
      string causation_id
      string correlation_id
      json payload
    }
    OBSERVATION {
      string observation_id PK
      string run_id FK
      string actor_id FK
      int observation_sequence
      datetime observed_at
      int schema_version
      json perceived_content
      string content_digest
    }
    ACTION_REQUEST {
      string action_request_id PK
      string run_id FK
      string actor_id FK
      string observation_id FK
      int action_sequence
      datetime requested_at
      string action_type
      int schema_version
      json payload
      string controller_trace_id
    }
    ACTION_RESULT {
      string action_result_id PK
      string action_request_id FK
      datetime resolved_at
      string status
      string reason_code
      int schema_version
      json outcome
      string state_digest_after
    }
    KNOWLEDGE_RECORD {
      string knowledge_record_id PK
      string run_id FK
      string actor_id FK
      string subject_ref
      string predicate
      json object_value
      string source_kind
      string source_ref
      datetime learned_at
      float confidence
      string supersedes_id
    }
```

## 3. Core persistence 상세

- **SimulationRun:** scenario/engine version, seed, simulation time, initial state digest와 상태를 보존한다.
- **Entity:** run 안의 최소 identity다. 도메인 속성 저장소가 아니다.
- **ScheduledEvent:** 아직 실행되지 않은 system/world event input을 보존한다. 숨겨진 scheduler 상태이며 Observation·Knowledge·World Truth query에는 노출되지 않는다. due time에는 System/Event Handler가 직접 처리하며 ActionRequest로 materialize하지 않는다. event type/schema, handler, 처리 결과와 state digest를 보존한다.
- **ModuleVersion/RunModule**(도식 생략): run에 활성화된 module ID, version, configuration digest를 기록한다.

## 4. 연구 핵심 레코드 상세

- **Event:** `(run_id, event_sequence)` unique. `source_kind/source_ref`로 ActionRequest, ScheduledEvent 또는 자연 발생 trigger를 구분한다. 발생한 사실과 transition 결과만 기록하며 미래 예약을 Event로 가장하지 않는다.
- **Observation:** Controller에 실제 전달된 immutable envelope을 그대로 보존한다. actor, simulation time, ordering, schema와 digest를 포함한다.
- **ActionRequest:** Actor/Controller가 제출한 정규화된 행동 의도만 보존한다. actor와 Observation 관계는 필수이며 ScheduledEvent나 자연 발생 World Event를 넣지 않는다. 거부된 요청도 삭제하지 않는다.
- **ActionResult:** 모든 요청과 1:1이며 `SUCCEEDED`, `REJECTED`, `FAILED` 같은 status와 안정적 reason code를 가진다. 실패 시 mutation 여부는 반드시 none이다.
- **KnowledgeRecord:** actor별 주장/사실 인식의 출처, 획득 시각, 확신도와 정정 계보를 가진다. 동일 subject에 대한 상충 지식을 허용할 수 있으며 World Truth와 FK로 동일시하지 않는다.

LLM trial 분석에는 별도 **ControllerInvocation** 레코드를 둘 수 있다. `controller_trace_id`, model/provider, prompt version, parameters, memory policy/version, Observation ID, raw response 참조, parse status와 ActionRequest ID를 보존한다. provider 비밀값이나 인증정보는 저장하지 않는다.

## 5. Module-owned state 예시

- **movement:** `Location`, `Route`, `ActorPosition`. Route는 endpoints, traversal cost와 현재 passability를 가진다.
- **knowledge:** `KnowledgeRecord`와 선택적 정규화 index. 연구 핵심 envelope 소유자이기도 하다.
- **inventory:** `ItemDefinition`, `InventoryEntry`.
- **survival:** `SurvivalState`의 최소 hunger/fatigue 등. 실제 필드는 해당 milestone에서 확정한다.
- **trade:** `Wallet`, `Offer` 또는 최소 price listing. 통화 의미는 trade module이 소유한다.
- **social:** 대화/전달 Event를 사용하며 별도 상태가 필요할 때만 table을 추가한다.

## 6. 원자성·무결성

- actor action과 system event는 서로 다른 handler를 사용하지만, 각 engine transition은 validation 이후 canonical mutation, 결과 provenance, Event append와 직접 유발된 Knowledge update를 공통 transaction/mutation boundary에서 commit한다.
- invalid Action은 ActionRequest와 REJECTED ActionResult만 기록하고 domain state/Event를 부분 생성하지 않는다. 감사 Event가 필요하면 domain Event와 분리된 명시적 기록 정책을 사용한다.
- FK만으로 정보 권한을 표현하지 않는다. Observation 생성 query 자체가 actor scope를 강제한다.
- replay 비교용 state digest는 연구 log와 비결정적 metadata를 제외한 canonical state의 정규화 직렬화로 계산한다.
