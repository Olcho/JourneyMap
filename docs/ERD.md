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
    OBSERVATION ||--o{ ACTION_REQUEST : prompts
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
      int schema_version
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
- **ActionResult:** 정상 처리된 요청과 1:1이며 `SUCCEEDED`, `REJECTED`, `FAILED` 같은 status와 안정적 reason code를 가진다. 실패 시 해당 action의 성공 mutation은 없다. duration 동안 독립적으로 commit된 system mutation과 경과 시간은 유지한다. 예상 밖 engine 오류는 결과를 조작하지 않고 예외로 전파한다.
- **KnowledgeRecord:** actor별 주장/사실 인식의 출처, 획득 시각과 정정 계보를 가진다. 동일 subject에 대한 상충 지식을 허용하며 World Truth와 FK로 동일시하지 않는다. M4에는 confidence 필드가 없다.

LLM trial 분석에는 별도 **ControllerInvocation** 레코드를 둘 수 있다. `controller_trace_id`, model/provider, prompt version, parameters, memory policy/version, Observation ID, raw response 참조, parse status와 ActionRequest ID를 보존한다. provider 비밀값이나 인증정보는 저장하지 않는다.

## 5. Module-owned state 예시

- **movement:** `Location`, `Route`, `ActorPosition`. Route는 endpoints, traversal cost와 현재 passability를 가진다.
- **knowledge:** `KnowledgeRecord`와 선택적 정규화 index. 연구 핵심 envelope 소유자이기도 하다.
- **inventory:** `ItemDefinition`과 actor-owned quantity map. M6의 실제 schema는 아래 12절을 따른다.
- **survival:** `SurvivalState(hunger, fatigue)`와 consumable hunger recovery. 값은 0–100 정수다.
- **trade:** `Wallet`, `Offer`. 단일 정수 통화와 가격은 trade module이 소유한다.
- **social:** 대화/전달 Event를 사용하며 별도 상태가 필요할 때만 table을 추가한다.

## 6. 원자성·무결성

- actor action과 system event는 서로 다른 handler를 사용하지만, 각 engine transition은 validation 이후 canonical mutation, 결과 provenance와 Event append를 공통 transaction/mutation boundary에서 commit한다. M4 runtime Knowledge는 그 committed Event와 초기 Knowledge에서 재구성하는 별도 first-class research projection이다. Knowledge canonical write를 같은 transaction에 추가하지 않는다.
- invalid Action은 ActionRequest와 REJECTED ActionResult만 기록하고 domain state/Event를 부분 생성하지 않는다. 감사 Event가 필요하면 domain Event와 분리된 명시적 기록 정책을 사용한다.
- M2 시작 거부의 비교 기준은 제출 tick까지 due system event를 처리한 뒤의 상태다. 완료 조건 실패는 FAILED 결과만 추가하고 이미 경과한 시간·독립적인 system commit을 rollback하지 않는다.

- FK만으로 정보 권한을 표현하지 않는다. Observation 생성 query 자체가 actor scope를 강제한다.
- replay 비교용 state digest는 연구 log와 비결정적 metadata를 제외한 canonical state의 정규화 직렬화로 계산한다.

## 7. M1 구현 범위

M1은 위 논리 모델 중 RunManifest, ScheduledEvent, Event, 최소 ActionRequest/ActionResult와 system handler outcome에 대응하는 in-process envelope만 구현한다. 아직 concrete SQLite table이나 domain state table을 만들지 않는다.

- canonical state는 module state를 수용할 수 있는 JSON object이나 M1에는 domain key 의미가 없다.
- handler에는 canonical object 자체가 아니라 분리된 snapshot을 전달하고, 성공한 TransitionPlan만 공통 mutation 경계에서 반영한다.
- Event와 handler 결과는 run-local deterministic sequence 및 source/causation/correlation으로 연결된다.
- 최소 ActionRequest도 필수 `based_on_observation_id`를 보존한다. Observation record와 FK 검증은 M3 이전에 임시 구현하지 않으며 M1에서는 opaque identifier 계약만 강제한다.
- M1 state digest는 canonical state만 포함하고 future schedule, Event log, RNG bookkeeping과 run metadata는 포함하지 않는다. replay report는 Event ordering, handler results, logical time과 RNG draw count를 별도로 비교할 수 있다.
- 구체 persistence schema, first-class research record 저장과 transaction projection은 해당 record 수명주기 요구가 구체화되는 milestone에서 추가한다.

## 8. M2 실제 in-memory 상태

아래 값은 기존 canonical JSON state에 저장한다. 위 논리 ERD를 SQLite schema로 구현한 것은 아니다.

| 소유자 / canonical 경로 | record |
|---|---|
| Core `entities[entity_id]` | `{entity_id, entity_type}` |
| movement `movement.locations[location_id]` | `{location_id}` |
| movement `movement.routes[route_id]` | `{route_id, origin, destination, traversal_cost, passable}` |
| movement `movement.positions[actor_id]` | `{actor_id, location_id}` |

identity는 비어 있지 않은 문자열이며 run 안에서 안정적이다. initial state builder는 중복 ID와 잘못된 location 참조를 거부한다. Route는 단방향이고 traversal_cost는 양의 정수 tick, passable은 bool이다. MOVE handler는 raw canonical record도 시작과 완료에 다시 검증한다. Entity에는 위치를 넣지 않으며, runtime 위치 변경은 movement가 만든 TransitionPlan을 공통 mutation boundary에서 적용한다.

ActionTiming의 duration과 검증 데이터는 canonical state에 저장하지 않는 파생 값이다. ActionResult는 `started_at`, `resolved_at`, `status`, `reason_code`를 추가하고 실패일 때 `transition=None`으로 기록한다. 실패는 domain Event나 deterministic ID를 소비하지 않는다. state digest에는 Entity와 movement 상태가 포함되며 timing·clock·scheduler·Event·결과 log는 포함되지 않는다. 이 값들은 replay에서 별도로 비교한다.

## 9. M3 실제 in-memory first-class 기록

M3는 SQLite table을 추가하지 않는다. World Truth는 계속 kernel canonical snapshot이며 다음 기록은 독립된 in-memory envelope/ledger다.

| 기록 / 소유자 | 실제 필드와 수명주기 |
|---|---|
| Observation / Core envelope, application history | `observation_id, run_id, actor_id, observation_sequence, simulation_time, schema_version, content_digest`; immutable canonical content. 성공 generation 때만 run-local sequence를 소비하고 append |
| KnowledgeRecord / knowledge module | `knowledge_record_id, run_id, actor_id, subject_ref, predicate, value, source_kind, source_ref, learned_at, supersedes_id?, schema_version` |
| ActionTrace / application | `attempt_sequence, request: ActionRequest, result: ActionResult?, controller_result?, boundary_reason?, error_type?`. live 요청 시도와 대응 결과를 연결 |

KnowledgeLedger 생성에는 run ID, manifest start tick과 **명시적인 초기 기록**만 제공한다. stable record ID와 source kind/ref는 시나리오 작성자가 제공하는 문자열이며 UUID/자동 truth seed를 만들지 않는다. learned_at은 시작 tick 이하이고 source는 빈 문자열일 수 없다. subject_ref/value는 World Truth FK나 pointer가 아니며 존재하지 않는 대상에 관한 주장도 표현할 수 있다. 별도 confidence 필드나 자동 확신도 조정은 없다.

record ID는 run 내 유일하며 조회 순서는 `(learned_at, knowledge_record_id)`다. supersedes는 같은 run/actor/subject/predicate의 현재 ledger에 있는 이전 또는 동일 tick 기록을 가리켜야 한다. unknown reference, 미래 predecessor, cycle과 actor 간 계보를 거부한다. 원본을 삭제·rewrite하지 않고 상충 기록과 정정 계보를 모두 history/query에 반환한다. 자동 winner 선택은 없다. `for_actor(actor_id)`는 자기 기록만 복사한 ActorKnowledgeView를 만들며 그 view에는 actor selector나 ledger handle이 없다. 빈 query는 UNKNOWN을 뜻하고 전 세계 unknown row를 만들지 않는다.

Observation과 Knowledge는 서로 다른 store다. observe/MOVE/WAIT/system event가 초기 Knowledge를 자동 수정하거나 append하지 않는다. Controller와 Research가 읽은 nested JSON을 수정해도 ledger와 저장된 trace는 바뀌지 않는다. M3에는 runtime knowledge writer가 없다. M4는 아래 별도 projection을 추가하며 EventBus subscriber의 kernel 재진입이나 Observation builder의 mutation을 사용하지 않는다.

초기 Knowledge와 Observation/ActionTrace는 기존 kernel state digest 및 engine replay input에 포함되지 않는다. 연구자가 live Observation stream까지 재현하려면 동일한 명시적 초기 지식, pipeline 설정과 actor별 observe 호출 순서/tick도 보관해야 한다. M1/M2 engine replay는 기록된 ActionRequest만으로 충분하다. 이 milestone은 별도 영속 저장·복구 또는 resume 기능을 제공하지 않는다.

## 10. M4 실제 상태와 projection provenance

| 소유자 / 경로 | 값 |
|---|---|
| Alderwick `alderwick.east_bridge` | `{bridge_id: "east-bridge", condition: "intact" 또는 "collapsed"}` |
| movement의 기존 route 두 개 | `east-road-to-east-bridge`, `east-bridge-to-east-road`의 passable |
| knowledge `KnowledgeProjection` | 초기 기록 + committed Event로 재구성한 독립 history/view. World Truth/digest 밖에 존재 |

KnowledgeRecord 필드는 M3와 같다. runtime direct 기록의 ID는 `<source_event_id>:alderwick-direct-v1:<projection_index:08d>`다. index는 Event 안의 정렬된 witness 순번 또는 단일 arrival의 1이며, 중복 획득을 건너뛰어도 나머지 index를 다시 매기지 않는다. `source_kind="DIRECT_OBSERVATION"`, `source_ref=<실제 획득 trigger Event ID>`, `learned_at=<그 Event tick>`, `schema_version=1`, `supersedes_id=None`이다. subject는 `east-bridge`, predicate는 `condition`, value는 `collapsed`다.

witness의 source_ref는 BridgeCollapsed다. later discovery의 source_ref는 ActorMoved이며 이 Event가 도착·획득 시점을 고정한다. collapsed value의 근거는 같은 run에서 **그 ActorMoved보다 앞선 event_sequence의 BridgeCollapsed**이고, 그 payload의 bridge_id/condition과 ActorMoved destination의 가시성을 scenario v1 규칙으로 결합한다. 같은 tick이어도 sequence가 근거다. source_ref가 이동 Event라는 이유로 그 payload만이 value의 모든 근거라는 의미는 아니다. 이 관계는 전체 Event 이력에서 조회하며 KnowledgeRecord에 별도 causal graph 필드를 추가하지 않았다.

runtime 직접 획득은 actor별 같은 bridge/condition에 한 번만 만든다. 재방문·반복 읽기·동일 log 재구성은 추가 기록을 만들지 않는다. 초기 주장과 runtime 사실은 자동 병합하거나 supersede하지 않으며 기존 상충 history를 보존한다. 전체 log는 단일 run, 1부터 연속 sequence, 유일 Event ID와 비역행 tick이어야 한다. 중복/누락/뒤집힌 log 또는 provenance가 잘못된 projection 결과는 partial view 대신 오류다. 동일 정상 log를 여러 번 재구성하는 것은 허용한다.

Event/Knowledge는 계속 in-memory다. EventBus delivery 실패 후의 재구성은 살아 있는 committed log를 사용하는 복구이며 프로세스 재시작용 영속 저장이나 resume 기능은 아니다.

## 11. M5 간접 지식과 interaction provenance

canonical state/table과 KnowledgeRecord envelope는 추가·변경하지 않는다. social interaction은 공통 transaction에서 commit한 Event이고 social inbox는 저장하지 않는다. 직접 기록은 M4와 동일한 `DIRECT_OBSERVATION` source이며, 전달 기록은 `INFORMED`다.

| 간접 KnowledgeRecord field | 값 |
|---|---|
| knowledge_record_id | `<ActorInformed.event_id>:social-informed-v1:00000001` |
| run_id / actor_id | Event run / target_actor_id |
| subject_ref / predicate / value | 그 Event prefix의 sender-owned claim record에서 복사 |
| source_kind / source_ref | `INFORMED` / ActorInformed Event ID |
| learned_at / schema_version | Event completion tick / 1 |
| supersedes_id | None; 자동 정정/병합하지 않음 |

ActorInformed payload의 `sender_actor_id`, `target_actor_id`, `claim_record_id`, `location_id`, optional `reply_to_event_id`로 source와 interaction을 복원한다. ASK/REQUEST와 공통 envelope 필드는 [API M5 계약](API.md#11-m5-구현-계약)을 따른다.

출처 chain은 `receiver Knowledge.source_ref → ActorInformed → sender claim_record_id → sender Knowledge.source_ref → BridgeCollapsed / ActorMoved / 이전 ActorInformed`다. social Event의 source_ref는 INFORM ActionRequest이며 ActionTrace를 통해 실제 sender Observation과 연결된다. reply reference는 ActorAsked를 가리킨다. 이 탐색에 raw World Truth는 필요하지 않다. record ID를 receiver에게 복사하지 않고 새로운 ID를 발급하므로 여러 전달 단계와 반복 전달이 각각 남는다.

projection은 초기 지식과 전체 ordered committed Event를 사용한다. direct records를 source Event에 맞춰 누적하고 social rule에서 그 시점까지의 sender records를 참조한다. unknown/future/타 owner source는 오류이며 malformed log/output에서는 partial history를 게시하지 않는다. initial record source는 여전히 explicit author input이고 runtime source는 실제 committed Event다.

상충 intact(INITIAL)와 collapsed(INFORMED)는 `(learned_at, knowledge_record_id)` 순서의 history/query에 모두 남는다. stale 정보를 나중에 INFORM해도 World Truth와 비교하여 삭제·수정하지 않는다. `supersedes`는 기존 명시적 정정 계약만 유지한다. 어떤 claim을 믿는지, trust/confidence, relationship, 영속 inbox/DB migration은 M5에서 도입하지 않는다.

## 12. M6 실제 canonical resource schema

M6는 in-memory canonical JSON과 기존 Event/ActionResult를 확장하며 SQLite table/migration이나 persistence redesign은 추가하지 않는다. 세 top-level key는 각각의 module이 소유한다.

```json
{
  "inventory": {
    "items": {"bread": {"item_id": "bread"}},
    "owners": {"stranger": {"bread": 0}, "edwin": {"bread": 5}}
  },
  "survival": {
    "actors": {"stranger": {"hunger": 20, "fatigue": 10}, "edwin": {"hunger": 0, "fatigue": 0}},
    "consumables": {"bread": 10}
  },
  "trade": {
    "wallets": {"stranger": 10, "edwin": 0},
    "offers": {
      "edwin-bread": {"offer_id": "edwin-bread", "seller_id": "edwin", "item_id": "bread", "unit_price": 2, "active": true}
    }
  }
}
```

위 예시는 두 actor만 발췌했다. 실제 Alderwick resource fixture는 기존 5명의 actor 모두에게 inventory/survival/wallet을 제공한다. Stranger 외의 초기 hunger/fatigue/wallet은 0이고 Edwin만 bread 5개를 소유한다. Entity와 movement에 resource field를 추가하지 않는다.

ItemDefinition은 stable item_id만 가진다. quantity와 wallet/price는 bool을 제외한 0 이상의 정수다. owner record가 있고 등록된 item의 entry가 없으면 수량 0이며 수신 시 entry를 만든다. 소모로 0이 된 entry는 유지한다. 없는 owner/Entity나 item definition은 명시적 검증 오류다. self transfer/self purchase는 거부한다. helpers는 관련 row를 검증한 detached candidate를 반환하고 unrelated module fields/records를 유지한다. 정보 공개 query는 record 전체 복사 대신 allowlist를 재구성한다.

SurvivalState는 hunger/fatigue 0–100 정수이며 높은 값이 나쁘다. consumables의 값은 survival-owned positive integer hunger recovery다. inventory item에 survival effect를 넣지 않는다. future tick table, schedule cursor나 read-time derived pressure는 canonical schema에 없다. pressure는 성공한 system/action transition에서만 변경된다.

Wallet dataclass의 actor_id는 canonical wallets map key이고 Offer의 offer_id는 map key와 record identity가 일치해야 한다. offer는 seller/item 참조와 unit_price/active만 가지며 별도 stock을 중복 저장하지 않는다. 초기 builder는 offer seller의 wallet 참조를 확인하고 BUY는 실제 Entity/position/inventory/item/wallet을 재검증한다. wallet과 inventory는 KnowledgeRecord가 아니다.

BUY는 `{inventory, trade}` 후보와 ItemPurchased를, CONSUME는 `{inventory, survival}` 후보와 ItemConsumed를 각각 한 transition에 커밋한다. BUY의 수량/통화는 양쪽 합계를 보존한다. CONSUME의 world item total 감소는 성공 Event quantity와 같아야 한다. REST/SurvivalTick은 item/currency의 source/sink가 아니다. Event payload와 reason/time 계약은 [API M6 절](API.md#12-m6-구현-계약)에 있다.

ActionTrace의 request → based_on_observation_id와 ActionResult → transition → emitted Event ID로 actor provenance를 연결한다. SurvivalAdvanced는 ActionRequest 없이 ScheduledEvent source_ref로 연결한다. Event log와 future schedule은 resource state digest에 포함되지 않고 ReplayReport에서 별도로 비교한다. replay는 초기 resource world와 `alderwick/resources-1` schedule 및 기존 ActionRequest stream만 요구한다. M4/M5 Knowledge는 기존 별도 projection으로 유지한다.
