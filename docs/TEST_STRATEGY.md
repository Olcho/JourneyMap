# Test Strategy

## 1. 목적

JourneyMap의 최우선 품질은 기능 수가 아니라 정보·권한 경계, 원자성, 결정론과 연구 추적성이다. 테스트 pyramid는 Python 3.12와 pytest의 빠른 unit/property test를 기반으로 module contract와 작은 통합 시나리오를 더하며 Ruff와 mypy를 M0 품질 gate로 사용한다.

## 2. Release-gating 핵심 테스트

### P0 — Knowledge Leak Test

- 같은 World Truth에서 위치·시야·지식이 다른 actor들의 Observation을 생성한다.
- 각 Observation에 허용된 perceived/known fact만 존재함을 확인한다.
- East Bridge 붕괴 예약, 다른 actor의 Knowledge, 숨은 entity/component와 debug field가 노출되지 않음을 검사한다.
- contributor를 새로 등록해도 raw World Truth 접근 없이 동작하는지 contract test로 강제한다.

### P0 — Authority Test

- LLM/Scripted Controller의 자연어, 위조 actor ID, system event 형태의 payload, 임의 world mutation field, 존재하지 않는 action type을 제출한다.
- ActionRequest는 등록된 Action Handler만, ScheduledEvent/World Event는 별도 System/Event Handler만 받을 수 있으며 두 경로 모두 공통 mutation 경계 밖에서 canonical state를 변경할 수 없음을 확인한다.
- Controller가 scheduler나 System/Event Handler를 호출할 수 없음을 확인한다.
- system event 실행 전후로 agent ActionRequest 수와 행동 통계가 증가하지 않음을 확인한다.
- Controller object graph/credential로 Research/Debug interface에 접근할 수 없음을 architecture/integration test로 검증한다.

### P0 — Replay Test

- initial canonical snapshot, versioned scenario schedule, run manifest, seed와 기록된 ActionRequest stream을 두 번 실행한다.
- 각 step의 ActionResult, Event envelope ordering, canonical state digest와 최종 snapshot이 동일해야 한다.
- LLM 호출은 replay하지 않고 기록된 ActionRequest를 입력으로 사용한다.

### P0 — Deterministic Event Ordering Test

- 동일 simulation time/priority에 여러 scheduled item, 파생 Event와 subscriber를 구성한다.
- 프로세스 재시작, 등록 순서 변화와 반복 실행에서도 정의된 ordering key에 따른 같은 순서를 확인한다.
- hash-map iteration이나 wall clock이 결과에 섞이면 실패하게 한다.

### P0 — Invalid Action Atomicity

- route 불통, 자원 부족, stale Observation, 잘못된 payload 등 각 실패 지점에서 action을 실행한다.
- ActionRequest와 REJECTED ActionResult 외 canonical domain state와 domain Event가 전혀 변하지 않음을 transaction 전후 digest로 검사한다.

### P0 — Module Dependency Validation

- 누락 dependency, 중복 module/action registration, 순환 dependency, 호환되지 않는 version으로 부팅을 시도한다.
- simulation 시작 전에 명확하고 결정적인 오류로 거부되어야 한다.

### P0 — Alderwick Bridge Integration Test

1. 붕괴 예약은 World Truth/Observation/Knowledge에 나타나지 않는다.
2. 통제 시각까지 진행하면 ScheduledEvent가 별도 System/Event Handler에서 처리되고 bridge/route 상태와 `BridgeCollapsed` Event가 원자적으로 바뀐다. ActionRequest는 생성되지 않는다.
3. 현장 목격자만 직접 KnowledgeRecord를 얻고 원격 actor는 얻지 않는다.
4. 아직 모르는 actor가 관찰 가능한 위치에 도착하면 ActorMoved를 trigger로 직접 지식을 얻는다. 앞선 BridgeCollapsed와 Event 순서로 value의 근거를 추적한다.
5. 목격·나중 발견의 runtime Knowledge가 다음 GamePort.observe()의 actor-owned known section에 반영된다. trusted perception을 통과한 현재 bridge 정보와 Knowledge는 별도 record다.
6. 붕괴 route MOVE는 기존 ROUTE_IMPASSABLE로 거부되고 부분 mutation이 없다. 이동 중/동일 완료 tick 붕괴는 M2 FAILED semantics를 따른다.
7. 실제 GamePort.observe → ScriptedController.decide → submit 루프를 실행한다. Controller에는 Observation 외 world/research/scheduler capability가 없다.
8. 기존 ActionRequest-only ReplayHarness로 결과/Event/order/time/world/digest가 같고, Event-only Knowledge 재구성도 같다. fresh live run의 Observation/요청/지식 순서도 비교한다.
9. commit 전 실패의 원자성, commit 후 delivery 실패에서의 재구성, repeated read/rebuild와 duplicate acquisition 방지를 검증한다.

**M5 extension:** 목격자가 `INFORM`하면 수신자가 출처를 가진 간접 지식을 얻는다. ASK/REQUEST/social transfer 및 NPC 자율 schedule/utility 행동도 M5 범위이며 M4 exit에 요구하지 않는다.

## 3. 계층별 테스트

### Unit/property

- seeded RNG sequence, clock arithmetic와 scheduler ordering
- action schema parsing과 domain validation
- perception predicates와 Knowledge supersession/source rules
- canonical serialization 및 state digest
- movement/trade/inventory/survival invariants

Property 예: 음수 inventory 불가, 실패한 transfer의 양쪽 balance 불변, 임의 insertion order에서도 event order 불변, 어떤 actor Observation에도 권한 밖 fact가 없음.

### Module contract

모든 Module에 공통 fixture를 적용한다.

- metadata와 dependency 선언 가능
- action type/version과 system event type/version 충돌 없음
- handler validation 단계에 mutation 없음
- Event subscriber와 ObservationContributor에 stable ordering key 존재
- module-owned state를 다른 module이 직접 쓰지 않음

### Integration

- simulation loop 전체와 persistence transaction
- movement → perception → knowledge
- social `INFORM` → source-aware knowledge
- BUY/CONSUME의 cross-module 원자성
- Controller parsing failure/fallback과 no-mutation

### Research record validation

- actor 경로의 Observation → ActionRequest → ActionResult → Event chain과 system 경로의 ScheduledEvent/World Event → handler outcome → Event chain을 각각 ID로 탐색 가능
- 거부된 action도 보존
- schema/engine/module/prompt/model metadata 완전성
- secret/credential과 허용되지 않은 World Truth가 ControllerInvocation에 없음

## 4. 결정론 test harness

- 테스트에서 wall clock과 OS randomness를 금지하고 clock/RNG를 주입한다.
- snapshot fixture, scenario schedule과 ActionRequest stream을 version control한다.
- expected Event sequence와 canonical digest를 golden artifact로 사용할 때 schema/version을 함께 저장한다.
- 순서 민감성을 찾기 위해 module 등록 순서와 내부 입력 순서를 교란하되 기대 결과는 동일하게 유지한다.
- 실패 보고서는 최초 divergence의 action sequence, event sequence와 state path를 보여준다.

## 5. LLM 평가 경계

엔진 CI는 외부 LLM에 의존하지 않는다. ScriptedController fixture로 모든 engine invariant를 검증한다. LLM 실험은 model/provider/prompt/parameters/Observation/raw response/ActionRequest를 기록하고, 성공률·행동 분포·정보 활용·규칙 위반률 등을 반복 trial에서 비교한다. 모델 응답의 byte equality를 테스트 기준으로 삼지 않는다.

## 6. Milestone별 gate

- **M0:** 빈 module registry로 in-process kernel boot/close, SQLite adapter 수명주기, module dependency 검증, Core dependency rule, pytest/Ruff/mypy 실행
- **M1:** logical clock 역행 거부, SplitMix64 고정 sequence/draw count/64-bit seed normalization, canonical serialization/digest와 unsupported value 거부, scheduler `(due_time, priority, insertion_sequence)`, EventBus stable subscriber ordering/synchronous dispatch/commit 후 실패 의미, action/system handler 분리와 필수 opaque Observation reference, 실패 resolution의 state/clock/RNG/Event/identity/scheduler 무변경, versioned schedule + recorded ActionRequest replay의 동일 handler result/deterministic ID/Event ordering/final digest
- **M2:** 최소 Entity identity와 movement 소유권, Location/단방향 Route/ActorPosition, MOVE/WAIT v1 payload, 존재·연결·통행·양의 정수 비용 검증, 시작 거부 시 state digest/clock/RNG/ID/scheduler/domain Event 불변, duration 중/동일 완료 tick system ordering, 완료 조건 실패 시 경과 시간/system commit 유지와 action 성공 mutation/Event 미생성, 성공·REJECTED·FAILED를 섞은 replay 결과/ID/Event/digest 동일, timed context 격리·재진입 금지·중간 system 오류의 M1 의미 유지
- **M3:** actor별 allowlist perception, hidden truth/다른 actor knowledge/future schedule의 비노출과 반사실적 변경에 대한 Observation 불변성, contributor 전체 key 정렬/입출력 격리/invalid 출력 원자성, immutable Observation ID/digest/history, 명시적 source/상충/정정 계보/UNKNOWN knowledge, bound Game capability/위조 권한 차단/제한된 receipt, Research trace와 ActionRequest-only replay 동등성
- **M4:** 위 Alderwick Bridge Integration의 직접 관찰/Controller/engine replay/Knowledge reconstruction 항목 전체. M5 INFORM extension 제외
- **M5–M6:** 정보 전달과 cross-module 원자성
- **M7:** versioned contract/conformance suite
- **M8:** 엔진 gate 전부 + LLM 기록 완전성 + 24시간 smoke experiment

M2 자동화는 `tests/test_movement.py`, `tests/test_timed_actions.py`, `tests/test_architecture.py`에 있다. 폐쇄는 test-only system handler로 표현하며 Alderwick/bridge 시나리오는 만들지 않는다. 원자성은 제출 tick까지 독립적인 세계 진행 이후 action 시작 상태를 기준으로 검사한다. 완료 tick에서 폐쇄/재개, 유효한 비용 변경, endpoint 변경, actor/position/location 변경도 검증한다.

M2 감사 테스트는 제출 tick 이후 거부 상태를 독립적으로 진행한 control kernel과 비교한다. start validation, timing 계산, completion validation, resolve, pre-commit state/Event 직렬화에 예외를 주입하여 state/digest/clock/RNG/ID/scheduler/Event 보존과 이후 중복 소비가 없음을 검사한다. non-timed/timed action의 commit 후 subscriber 실패도 비교한다. 완료 tick의 여러 system event가 포함된 mixed replay는 ReplayReport뿐 아니라 kernel의 전체 RngSnapshot도 비교한다. self-route와 임의 entity_type은 현재 계약의 허용 사례로 검사한다.

M3 자동화는 `tests/test_observations.py`, `tests/test_knowledge.py`, `tests/test_game_and_research.py`와 확장한 architecture test에 있다. fixture는 hidden entity/route, legitimate self/location/position record 내부 secret, private knowledge, future ScheduledEvent와 debug seed/RNG를 의도적으로 포함한다. 허용된 전체 content와 context shape를 비교하고 hidden truth/다른 actor 지식/schedule 변경에도 자기 Observation이 동일함을 검사한다. contributor를 모든 등록 순열로 실행하고 context/출력/전달 record의 nested mutation, invalid canonical output, 재진입과 sequence 소비를 검증한다.

Authority gate는 GamePort의 공개 API가 observe/submit뿐인지, 다른 actor selector나 kernel/Research/system capability가 없는지 검사한다. unknown/타 actor/타 run Observation, 위조 actor/run, 임의 미래 제출 tick을 kernel 진입 전에 거부하고 trace를 보존한다. system 형태 payload와 임의 set_values는 actor schema 밖에서 mutation 권한을 얻지 못한다. engine defect와 post-commit delivery 실패는 Controller에 debug 문자열을 노출하지 않고 연구 trace에 실제 result 유무를 보존한다. 이는 trusted Python capability 테스트이며 악성 Python sandbox 검증이 아니다.

M3 통합 replay는 live Observation에 기반한 timed MOVE 실패와 WAIT 요청을 기록하고, application/Observation stream 없이 기존 ReplayHarness로 동일 result/Event/order/time/digest를 얻는다. 기존 148개 M0/M1/M2 테스트의 입력·기대 의미를 유지한다. 지식 runtime update, 목격 projection, Social 전파와 Alderwick integration은 이후 milestone의 gate이며 M3 테스트 통과로 구현되었다고 보지 않는다.

M3 adversarial audit는 mutable request identity/correlation의 trace 및 transition alias 오염과 등록된 handler 내부 registry 오류의 UNKNOWN_ACTION 오분류를 회귀 테스트로 차단한다. non-finite 값과 순환 payload는 INVALID_REQUEST로 거부한다. 첫 contributor 성공 후 다음 contributor 예외, 비정상 entity/position/location, actor 교차 관찰 sequence, due-now observation의 무진행, boundary 거부 후 sequence, 과거 Observation 재사용과 미래 제출 tick 권한을 검사한다. system publication 실패는 이전 동일 request ID의 성공 result를 현재 시도에 잘못 연결하지 않아야 하며 이미 commit된 system Event를 재실행하지 않아야 한다. 최종 milestone 판정은 전체 pytest/Ruff/format/mypy 및 diff gate 이후에만 내린다.

M4 자동화는 `tests/test_alderwick.py`(8개 integration 사례), `tests/test_alderwick_failures.py`(18개), `tests/test_knowledge_projection.py`(18개) 및 architecture 추가 2개다. 기존 225개 테스트의 의미를 유지하여 전체 271개다. failure injection은 bridge/route/cost/endpoint/witness/payload/resolve/Event 직렬화 실패에서 clock/RNG/sequence/schedule/world/Event를 비교한다. 첫 route candidate를 만든 뒤 두 번째 route 검증 실패도 포함한다. BridgeCollapsed 및 ActorMoved delivery 실패 후 Research와 다음 Observation에 committed Knowledge가 남는지 확인한다.

projection 테스트는 초기 상충 기록 보존, deterministic ID/source, 동일 history 재구성, 재방문 dedup, 입력·출력 alias 분리, event prefix의 비소급성, 정렬된 당시 witness, 잘못된 run/order/gap/duplicate/provenance/version을 검사한다. projection 예외 뒤에도 world는 유지되고 다음 정상 observe가 첫 성공 sequence를 사용한다. M4는 intact→collapsed의 한 번 전이를 검증하며 수리/재붕괴, social transfer나 영속 resume를 검증한 것으로 보지 않는다.
