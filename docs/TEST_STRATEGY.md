# Test Strategy

M8 현재 offline gate: **770 passed**. M0–M7의 기존 656개와 최초 M8 87개 테스트 목적을 유지하고, 공식 trial 설계 재검토의 신규 27개를 추가했다. 실제 network/API는 자동 테스트에서 호출하지 않는다. 아래 M8 절과 [실험 프로토콜](M8_EXPERIMENT.md)을 함께 따른다.

## 1. 목적

JourneyMap의 최우선 품질은 기능 수가 아니라 정보·권한 경계, 원자성, 결정론과 연구 추적성이다. 테스트 pyramid는 Python 3.12와 pytest의 빠른 unit/property test를 기반으로 module contract와 작은 통합 시나리오를 더하며 Ruff와 mypy를 M0 품질 gate로 사용한다.

## M8 adapter/first-trial gate

`test_m8_llm.py`는 8종 exact payload, missing/extra/bool/version/authority injection, malformed/duplicate/nonfinite/invalid UTF-8 JSON, provider/parser 예외, memory 출처/ordering, prompt leak, response duplication와 application exact retry, credential non-persistence를 검증한다. HTTP는 MagicMock으로 고정 destination/body, schema/medium config, refusal/incomplete/error/timeout을 검사하며 실제 API를 호출하지 않는다.

`test_m8_experiment.py`는 full fake 24h, recorded engine inputs만의 replay, 반복 engine artifacts, export byte determinism/integrity/쓰기 실패, immutable records, call/decision/wall/연속 실패 bounds, horizon no-clipping, Observation overflow/no-truncation을 검증한다. 기존 20-tick assertion만 새 24h protocol에 맞췄고 실패/불변식 검사를 삭제하지 않았다.

`test_m8_revision.py`의 27개는 모든 action의 trusted binding, REQUEST arbitrary JSON codec와 malformed subobject 거부, OpenAI strict schema subset, 별도 M8 schedule/M6 보존, M7 public receipt만의 actor scope, 새 response의 동일 intent, refusal/incomplete no-submit, requested alias/returned identity 분리를 검증한다.

Ruff check/format, strict mypy, 전체 pytest 및 git diff --check를 실행한다. 이번 Windows 환경의 `.venv` Python 링크가 끊어져 Python 3.12.14 번들 런타임과 기존 dev packages를 사용했다. 오래된 temp/cache ACL 때문에 pytest 임시 파일은 새 `trials/pytest-*` 경로와 `cache_dir=trials/pytest-cache`를 사용한다. 테스트 실패를 skip/xfail로 숨기지 않는다. 정상 개발 환경은 README의 Python 3.12 venv 명령을 사용한다.

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

- route 불통, 자원 부족, 과거 Observation의 belief와 현재 truth 불일치, 잘못된 payload 등 각 실패 지점에서 action을 실행한다. 과거 Observation 자체를 만료로 거부하지 않는다.
- 시작 거부는 제출 tick까지의 독립 system 진행 이후를 기준으로 action mutation이 없음을 검사한다. 완료 FAILED는 elapsed time/system commit을 유지하고 action 성공 mutation은 없어야 한다. Controller preflight 실패는 kernel 진입 자체가 없음을 검사한다.

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

**M5 extension — 구현 완료:** 목격자의 INFORM 뒤에만 수신자가 출처 있는 간접 지식을 얻는다. 별도 `social=True` composition으로 ASK→NPC INFORM→다음 Observation/행동, REQUEST 전달, conflicting history, 다단계 provenance, engine replay와 fresh-run determinism을 검증한다. M4 기본 fixture와 모든 기존 테스트는 그대로 유지한다.

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
- **M5:** social v1 schema/range/time, live claim authority, target-scoped perception, NPC ASK→INFORM, source chain/conflict, failure recovery, Event-prefix reconstruction과 replay
- **M6:** 생존·inventory·trade와 자원 보존/cross-module 원자성
- **M7:** strict v1 contract/conformance, live idempotency/error retry, Observation 65,536-byte budget/order/redaction, malformed output no-mutation, Provider/Memory dependency boundary
- **M8:** 엔진 gate 전부 + LLM 기록 완전성 + 24시간 smoke experiment

M2 자동화는 `tests/test_movement.py`, `tests/test_timed_actions.py`, `tests/test_architecture.py`에 있다. 폐쇄는 test-only system handler로 표현하며 Alderwick/bridge 시나리오는 만들지 않는다. 원자성은 제출 tick까지 독립적인 세계 진행 이후 action 시작 상태를 기준으로 검사한다. 완료 tick에서 폐쇄/재개, 유효한 비용 변경, endpoint 변경, actor/position/location 변경도 검증한다.

M2 감사 테스트는 제출 tick 이후 거부 상태를 독립적으로 진행한 control kernel과 비교한다. start validation, timing 계산, completion validation, resolve, pre-commit state/Event 직렬화에 예외를 주입하여 state/digest/clock/RNG/ID/scheduler/Event 보존과 이후 중복 소비가 없음을 검사한다. non-timed/timed action의 commit 후 subscriber 실패도 비교한다. 완료 tick의 여러 system event가 포함된 mixed replay는 ReplayReport뿐 아니라 kernel의 전체 RngSnapshot도 비교한다. self-route와 임의 entity_type은 현재 계약의 허용 사례로 검사한다.

M3 자동화는 `tests/test_observations.py`, `tests/test_knowledge.py`, `tests/test_game_and_research.py`와 확장한 architecture test에 있다. fixture는 hidden entity/route, legitimate self/location/position record 내부 secret, private knowledge, future ScheduledEvent와 debug seed/RNG를 의도적으로 포함한다. 허용된 전체 content와 context shape를 비교하고 hidden truth/다른 actor 지식/schedule 변경에도 자기 Observation이 동일함을 검사한다. contributor를 모든 등록 순열로 실행하고 context/출력/전달 record의 nested mutation, invalid canonical output, 재진입과 sequence 소비를 검증한다.

Authority gate는 GamePort의 공개 API가 observe/submit뿐인지, 다른 actor selector나 kernel/Research/system capability가 없는지 검사한다. unknown/타 actor/타 run Observation, 위조 actor/run, 임의 미래 제출 tick을 kernel 진입 전에 거부하고 trace를 보존한다. system 형태 payload와 임의 set_values는 actor schema 밖에서 mutation 권한을 얻지 못한다. engine defect와 post-commit delivery 실패는 Controller에 debug 문자열을 노출하지 않고 연구 trace에 실제 result 유무를 보존한다. 이는 trusted Python capability 테스트이며 악성 Python sandbox 검증이 아니다.

M3 통합 replay는 live Observation에 기반한 timed MOVE 실패와 WAIT 요청을 기록하고, application/Observation stream 없이 기존 ReplayHarness로 동일 result/Event/order/time/digest를 얻는다. 기존 148개 M0/M1/M2 테스트의 입력·기대 의미를 유지한다. 지식 runtime update, 목격 projection, Social 전파와 Alderwick integration은 이후 milestone의 gate이며 M3 테스트 통과로 구현되었다고 보지 않는다.

M3 adversarial audit는 mutable request identity/correlation의 trace 및 transition alias 오염과 등록된 handler 내부 registry 오류의 UNKNOWN_ACTION 오분류를 회귀 테스트로 차단한다. non-finite 값과 순환 payload는 INVALID_REQUEST로 거부한다. 첫 contributor 성공 후 다음 contributor 예외, 비정상 entity/position/location, actor 교차 관찰 sequence, due-now observation의 무진행, boundary 거부 후 sequence, 과거 Observation 재사용과 미래 제출 tick 권한을 검사한다. system publication 실패는 이전 동일 request ID의 성공 result를 현재 시도에 잘못 연결하지 않아야 하며 이미 commit된 system Event를 재실행하지 않아야 한다. 최종 milestone 판정은 전체 pytest/Ruff/format/mypy 및 diff gate 이후에만 내린다.

M4 자동화는 `tests/test_alderwick.py`(8개 integration 사례), `tests/test_alderwick_failures.py`(18개), `tests/test_knowledge_projection.py`(18개) 및 architecture 추가 2개다. 기존 225개 테스트의 의미를 유지하여 전체 271개다. failure injection은 bridge/route/cost/endpoint/witness/payload/resolve/Event 직렬화 실패에서 clock/RNG/sequence/schedule/world/Event를 비교한다. 첫 route candidate를 만든 뒤 두 번째 route 검증 실패도 포함한다. BridgeCollapsed 및 ActorMoved delivery 실패 후 Research와 다음 Observation에 committed Knowledge가 남는지 확인한다.

projection 테스트는 초기 상충 기록 보존, deterministic ID/source, 동일 history 재구성, 재방문 dedup, 입력·출력 alias 분리, event prefix의 비소급성, 정렬된 당시 witness, 잘못된 run/order/gap/duplicate/provenance/version을 검사한다. projection 예외 뒤에도 world는 유지되고 다음 정상 observe가 첫 성공 sequence를 사용한다. M4는 intact→collapsed의 한 번 전이를 검증하며 수리/재붕괴, social transfer나 영속 resume를 검증한 것으로 보지 않는다.

## 7. M5 자동화와 감사 범위

- `tests/test_social.py`: ASK/INFORM/REQUEST 각각의 정확한 v1 schema, target/self/entity/position/location/range, 추가 field와 wrong version, 고정 1 tick, 완료 시 actor/target/location 변화. 실패 전후 state/digest/time/RNG/sequence/schedule/Event를 비교한다. kernel-only INFORM은 trusted raw claim reference를 사용하며 live ownership 검증을 주장하지 않는다.
- `tests/test_social_information.py`: live unknown/foreign/다른 actor/target/future/unobserved record 참조 거부, 실제 Observation 내용 변조·누락 반례, reply topic/양쪽 actor/Observation scope, ASK/REQUEST의 비자동 실행, unrelated actor 비노출, Hugh→Thomas→Marta 전달과 stale claim 재전달, 상충 보존, malformed social provenance와 Event-prefix ordering, delivery 실패 복구.
- `tests/test_social_boundaries.py`: 같은 tick의 observe 이후 acquisition 거부, live 시작·완료 실패의 receiver 무획득과 mixed replay, raw Event log를 제거한 contributor 입력, projection/claim 읽기 실패 후 trace와 Observation sequence, detached 출력, ordered rule 입력 격리, NPC import와 ReplayInput schema 경계, 비활성 social composition의 거부.

실제 비LLM loop는 Hugh MOVE/Thomas ASK/Hugh INFORM/Thomas WAIT/Hugh WAIT를 실행한다. tick 3 목격 기록, tick 6 질문, tick 7 전달, Thomas의 초기 intact와 간접 collapsed 기록, 미응답→응답 완료 전이와 다음 행동을 확인한다. 두 fresh live run의 Observation/ActionTrace/Events/Knowledge를 비교하고 ReplayHarness는 recorded requests만으로 별도 두 번 실행한다. ActionResults/system outcomes/order/final world/digest/time/RNG와 replay 후 동일 초기 지식에서 재구성한 history를 비교한다.

권한 거부는 kernel input에 포함되지 않는 ActionTrace다. kernel canonical REJECTED/FAILED는 replay stream에 포함한다. claim ownership/Observation 검증은 live application의 책임이며 replay kernel의 privileged path를 hostile input validator로 간주하지 않는다. replay 후 source-prefix 검증은 Observation 검증의 대체가 아니다.

M5 gate: 기존 271개를 삭제·완화하지 않은 전체 **383 passed**. Python 3.12.10, Ruff check/format, strict mypy, diff 검사를 함께 실행한다. 이 count는 M5 종료 시점 기준이며 M6+ 개발 시 갱신한다. projection에서 발생한 ActionValidationError도 actor payload 거부로 오분류하지 않고 authority read 실패로 추적한다. 자동 belief winner·confidence·trust·LLM·일반 행동 framework·영속 resume·M7 retry/idempotency는 검사하거나 구현한 것으로 보지 않는다.

## 8. M6 resource gate와 adversarial audit

- `tests/test_resources.py`: ItemDefinition/quantity, SurvivalState, Wallet/Offer의 엄격한 type/range/identity/중복 검증. 없는 entry=0/없는 owner 오류, detached transfer와 currency 보존, multi-quantity/zero-price BUY, 소비 sink와 clamp, REST 실제 duration, system-only pressure 진행을 검사한다. BUY/CONSUME/REST의 시작·완료 entity/position/state/stock/funds/offer 오류와 정확한 payload를 parameterize한다.
- `tests/test_resource_failures.py`: inventory candidate 후 trade 실패, buyer 후보 이후 invalid seller wallet, invalid seller stock, transfer 후보 후 예외, decrement 이후 survival 실패, survival 후보 이후 예외, resolve 및 state/Event serialization 실패를 주입한다. 같은 tick의 SurvivalTick 및 RNG를 소비하는 unrelated system commit을 완료한 control kernel과 state/digest/time/전체 RngSnapshot/transition·Event sequence/pending schedule/outcomes를 비교한다. 다음 정상 action의 결과와 ID까지 비교해 sequence/RNG의 부분 소비를 검출한다. system tick 실패의 schedule/time rollback과 반복 시도, live post-commit Event delivery 오류의 양쪽 module/성공 trace 보존도 검사한다.
- `tests/test_resource_integration.py`: 실제 actor-bound GamePort에서 초기 자원 확인→WAIT→Bakery 이동→local offer 확인→BUY 2→CONSUME 1→REST 3을 실행한다. tick 10의 Stranger는 wallet 6/bread 1/hunger 30/fatigue 11이며 Edwin은 wallet 4/bread 3이다. ItemConsumed의 source request/수량/최신 hunger, ActorRested의 최신 fatigue, tick 3 bridge collapse와 Hugh direct Knowledge를 함께 확인한다. Observation 반복 읽기는 canonical mutation이 없다.
- 같은 integration suite는 변경된 private seller wallet/stock, 타 actor survival, hidden 효과/metadata와 malformed remote offer가 자기 Observation에 들어오지 않는 counterfactual leak test를 포함한다. echo contributor로 필터링 시점을 확인하고, remote/inactive listing 제외와 active out-of-stock listing 공개 정책을 검사한다. generic/M4/M5의 resource 비노출, finite schedule horizon과 version mismatch, resource+social composition의 기존 INFORM provenance도 검사한다.
- recorded ActionRequest-only ReplayHarness로 ActionResults, system outcomes, Events/order, final world/digest/time/RNG draw count를 비교한다. live 성공 run을 두 번 만들어 Observation/ActionTrace/Knowledge까지 비교하고 두 replay report도 비교한다. live REJECTED/elapsed-time FAILED와 성공을 섞은 요청 stream도 engine replay한다. Knowledge/Observation/resource output을 ReplayInput에 추가하지 않는다.
- `tests/test_resource_architecture.py`: inventory의 역방향 의존 금지, survival/trade의 좁은 inventory helper 의존, trade→movement position query, module dependency 누락, Core resource-domain state 금지 및 safe contributor import를 검증한다. 기존 architecture suite가 Core/kernel, movement와 Controller capability 경계를 계속 검사한다.

BUY는 item/currency 양쪽 합계 보존을 검사한다. CONSUME는 item total이 감소하는 의도된 sink이며 성공 Event quantity와의 일치가 gate다. REST/SurvivalTick은 inventory/currency를 바꾸지 않는다. 시스템이 action 완료 tick에 먼저 commit되므로 CONSUME/REST는 증가한 최신 pressure에 적용되어야 한다. 시작 snapshot을 덮어쓰면 이 테스트가 실패한다.

검증 실패는 REJECTED/FAILED로, resolve/직렬화 defect는 기존 M1/M2 예외 경로로 구분한다. 후자의 kernel ActionResult 부재를 예상된 행동 거부로 위장하지 않는다. 이미 성공한 독립 system commit과 elapsed time을 action rollback 대상에 넣지 않는다. commit 뒤 subscriber 오류도 성공 상태를 rollback하지 않는다.

M0–M5의 기존 383개 테스트는 삭제·완화 없이 유지한다. `.venv`의 Python version, Ruff check, Ruff format --check, strict mypy, 전체 pytest 및 git diff --check가 모두 통과해야 M6 exit를 확정한다. 이 gate는 finite 20-tick scenario와 minimum resource contract를 검증하며 장기 simulation, generic economy/effects, persistence resume 또는 M7 최종 idempotency/compatibility를 구현한 것으로 보지 않는다.

M6 종료 gate: **543 passed**(기존 383 + resource 127/integration 9/failures 19/architecture 5), Python 3.12.10, Ruff `All checks passed!`, format `95 files already formatted`, strict mypy `Success: no issues found in 86 source files`, diff 검사 성공. 기존 테스트 파일은 변경하지 않았다.

## 9. M7 contract gate와 pre-M7 policy 전환

기존 543개 테스트는 삭제하지 않았다. 연구/안전 invariant 보존은 기존 assertion을 무조건 복제하는 의미가 아니다. M7이 명시적으로 대체하는 repeated-ID 실행 정책과 불완전한 Observation fixture만 아래처럼 최소 수정하고 별도 adversarial tests로 새 계약을 강화했다.

| 기존 테스트 | 유지한 invariant | 최소 전환 |
|---|---|---|
| test_trace_request_payloads_are_detached_and_observation_reuse_is_not_expiry_policy | request/trace payload 격리, 과거 Observation 재사용, attempt sequence | 두 번째 실제 행동은 새 ID; repeated-ID 재실행 comment 제거 |
| test_system_delivery_failure_does_not_attach_a_prior_action_result (이전 이름에 same_id 포함) | interrupted attempt에 이전 결과 오연결 금지, system commit 보존, 다음 행동 복구 | 두 번째/세 번째 실제 행동에 서로 다른 ID, 이름·ID 비교만 변경 |
| test_late_contributor_failure_preserves_world_history_and_interleaved_sequences | Observation 오류 원자성, actor별 성공 sequence, 권한 거부, due-now 무진행 | denial 이후 정상 행동은 새 ID |
| test_live_future_submission_cannot_advance_time_even_when_domain_would_reject | future tick 권한 거부, trusted kernel의 system-first 의미, 과거 Observation 정상 재사용 | 정상 current-tick 후속 행동은 새 ID |
| test_controller_has_no_hidden_capability_and_uses_observation_content | Controller capability 제한, 보이는 bridge만으로 결정, 위조 actor/system 권한 거부 | 기존 bridge section 내용만 변경해 v1 identity 유지; 별개 system 위조에 새 ID |
| test_all_ordering_key_parts_are_stable_across_registration_permutations | 정렬 key 세 요소 및 24개 등록 순열의 동일 결과 | priority만 다른 중복 visible identity 대신 고유 contributor ID 사용 |

supersede된 정책은 동일 ID를 서로 다른 정상 실행에 재사용하던 pre-M7 live 정책, 서로 다른 priority로 같은 visible contributor identity를 등록하던 정책, Controller가 identity 없는 수제 section을 해석하던 fixture다. past Observation/current submitted_at, M2 REJECTED/FAILED, M4 bridge Knowledge, M5 INFORM provenance/projection 읽기 복구, M6 원자성·자원 보존은 그대로다. BUY public diagnostic masking은 표시 경계 변경이며 kernel reason/도메인 판정 순서를 변경하지 않는다.

신규 자동화:

- `tests/test_m7_idempotency.py`: SUCCEEDED/REJECTED/FAILED exact retry, 모든 normalized envelope 필드 변경 conflict, 다른 actor port·독립 run scope, canonical key order와 payload alias, 확정 boundary denial, action/system delivery 오류 및 시작/resolve 예외. clock/state/digest/전체 RNG/transition sequence/Events/pending scheduler/system outcomes/action_results를 비교한다. BUY/CONSUME/REST delivery retry도 정상 control kernel의 양쪽 module commit과 일치해야 한다. mixed resolved action stream에서 retries/conflicts를 제외하고 두 번 engine replay한다.
- `tests/test_m7_contracts.py`: 8종 exact payload/optional reply/nested REQUEST fixtures, missing/extra/type/bool/unknown-version, mutated NaN/Infinity/cycle/non-JSON/invalid UTF-8, ActionRequest와 receipt golden. Scripted/Human/SocialNpc를 같은 reusable suite로 binding/schema/입력 불변성/결정성/capability를 검사한다. None/dict/object/exception/잘못된 ActionRequest는 due-now system event가 있어도 kernel에 들어가지 않는다. Observation failure와 post-commit submission failure의 turn record도 구분한다.
- `tests/test_m7_observations.py`: v1 공개 envelope/content/ordering/digest, exact UTF-8 boundary·1-byte 초과·multibyte·item 초과, 기본 65,536-byte 상한, duplicate visible identity, reader fail-closed, overflow 후 sequence 복구. 40/250-record knowledge와 40건의 큰 social REQUEST history에서 결정적 결과·overflow·전체 Research 보존을 검증한다.
- `tests/test_m7_boundaries.py`: Core/Module에서 adapters/LLM SDK import 금지, Human/Provider/Memory의 좁은 의존성, fake Provider protocol/data validation, NoMemory와 same-actor ordered prior scope, private seller wallet/stock의 missing/invalid 상태가 동일 actor-visible fallback으로 처리됨을 검사한다.

M0–M6의 기존 counterfactual leak tests는 raw World Truth/hidden schedule/debug/다른 actor Knowledge와 자원 변경이 actor Observation을 바꾸지 않아야 함을 계속 검증한다. M7의 byte cap은 이 positive projection을 대체하지 않는다. 임의 문자열 denylist나 Knowledge/social 원본 삭제로 테스트를 통과시키지 않는다.

최종 gate는 프로젝트 `.venv` Python 3.12의 `python --version`, `ruff check .`, `ruff format --check .`, `mypy`, `pytest`, `git diff --check`, `git status`다. 2026-09-12 최종 M7 gate 통과: **656 passed in 2.71s (기존 543 + 신규 113)**. Python 3.12.10, Ruff `All checks passed!`, format `105 files already formatted`, mypy `Success: no issues found in 96 source files`, working diff 및 M6 base 대비 전체 diff 검사 통과. git HEAD는 사용자 WIP checkpoint 99a9ec1이며 최종 변경은 commit/push하지 않았다. kernel/ReplayInput schema 변경, 실제 LLM·24h 실행, crash-safe retry/resume, bounded Research memory를 완료했다고 주장하지 않는다.

### M7 전체 변경 파일 (M6 base 대비)

| 구분 | 파일 |
|---|---|
| 구현 | [src/journeymap/adapters/human.py](../src/journeymap/adapters/human.py)<br>[src/journeymap/adapters/memory.py](../src/journeymap/adapters/memory.py)<br>[src/journeymap/adapters/provider.py](../src/journeymap/adapters/provider.py)<br>[src/journeymap/adapters/scripted.py](../src/journeymap/adapters/scripted.py)<br>[src/journeymap/adapters/social_npc.py](../src/journeymap/adapters/social_npc.py)<br>[src/journeymap/application/contracts.py](../src/journeymap/application/contracts.py)<br>[src/journeymap/application/observations.py](../src/journeymap/application/observations.py)<br>[src/journeymap/application/reasons.py](../src/journeymap/application/reasons.py)<br>[src/journeymap/application/session.py](../src/journeymap/application/session.py)<br>[src/journeymap/application/turns.py](../src/journeymap/application/turns.py)<br>[src/journeymap/core/actions.py](../src/journeymap/core/actions.py)<br>[src/journeymap/core/observations.py](../src/journeymap/core/observations.py)<br>[src/journeymap/modules/movement/handlers.py](../src/journeymap/modules/movement/handlers.py)<br>[src/journeymap/modules/survival/handlers.py](../src/journeymap/modules/survival/handlers.py)<br>[src/journeymap/modules/trade/handlers.py](../src/journeymap/modules/trade/handlers.py) |
| 테스트 | [tests/test_alderwick.py](../tests/test_alderwick.py)<br>[tests/test_game_and_research.py](../tests/test_game_and_research.py)<br>[tests/test_m7_boundaries.py](../tests/test_m7_boundaries.py)<br>[tests/test_m7_contracts.py](../tests/test_m7_contracts.py)<br>[tests/test_m7_idempotency.py](../tests/test_m7_idempotency.py)<br>[tests/test_m7_observations.py](../tests/test_m7_observations.py)<br>[tests/test_observations.py](../tests/test_observations.py) |
| 문서 | [README.md](../README.md)<br>[docs/API.md](../docs/API.md)<br>[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md)<br>[docs/ERD.md](../docs/ERD.md)<br>[docs/JOURNEYMAP_0.1_SPEC.md](../docs/JOURNEYMAP_0.1_SPEC.md)<br>[docs/ROADMAP.md](../docs/ROADMAP.md)<br>[docs/TEST_STRATEGY.md](../docs/TEST_STRATEGY.md) |
