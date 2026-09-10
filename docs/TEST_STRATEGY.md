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
4. 목격자가 `INFORM`하면 수신자가 출처를 가진 간접 지식을 얻는다.
5. 아직 모르는 actor가 현장에 도착하면 직접 관찰로 알게 된다.
6. 붕괴 route MOVE는 전체 실패하며 state가 부분 변경되지 않는다.
7. 전 과정을 replay하면 결과와 순서가 동일하다.

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
- **M1:** RNG, clock, scheduler, EventBus ordering과 replay kernel
- **M2:** movement invariant와 invalid MOVE atomicity
- **M3:** Knowledge Leak/Authority test
- **M4:** Alderwick Bridge Integration 전체
- **M5–M6:** 정보 전달과 cross-module 원자성
- **M7:** versioned contract/conformance suite
- **M8:** 엔진 gate 전부 + LLM 기록 완전성 + 24시간 smoke experiment
