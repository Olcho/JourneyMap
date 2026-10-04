# Roadmap

2026-10-04 작업 브랜치에서 Experiment 03의 코드 기준선, 수령처 안내 진단 및 Full prefix
정보충분 대조조건을 별도 offline protocol/evaluator로 구현했다.
[범위와 실행법](EXPERIMENT03_BASELINE_DIAGNOSTIC.md),
[검증 기록](EXPERIMENT03_OFFLINE_REPORT_2026-10-04.md)을 따른다.
이후 본실험 설정 동결과 사용자 승인 후 live pilot은 별도 gate이며 Experiment 04는 이 변경에 없다.

## 현재 상태와 문서 역할

이 문서는 JourneyMap의 **개발 계보와 연구 단계가 어떤 순서로 형성되었는지**를 보존한다. 현재 작업 우선순위와 최신 연구 해석은 `README.md`와 각 experiment 문서를 함께 확인한다. 과거 milestone의 당시 테스트 수, 구현 범위, 제한은 역사 기록으로 유지하며 현재 상태처럼 재해석하지 않는다.

현재까지의 큰 흐름은 다음과 같다.

```text
M0-M8 deterministic research baseline
        ↓
Baseline Validation & Hardening
        ↓
Experiment 01 — Event Memory Foundation
        ↓
Experiment 02 — Recency Window k=1/2/3
        ↓
Experiment 03 — Memory Horizon / Distinct Places
        ↓
Semantic Decision Context v2 hardening
        ↓
Live Pilot Preflight
        ↓
[현재] Distinct Places construct validity 재검토
        ↓
actual behavioral pilot 실행 여부와 최종 study 설정 결정
        ↓
Information change / replanning experiment 설계 및 검증
```

- M0-M8 baseline은 완료됐다.
- Baseline Hardening, Event Memory Phase 1, Recency Window correctness는 완료되어 main에 반영됐다.
- Memory Horizon v2의 model-visible progress/provenance cue 제거도 완료됐다.
- 별도 live protocol/audit, 12-allocation cohort, Horizon wire schema, fresh runtime ownership, recording mock transport, journal/audit 경계를 포함한 [Live Pilot Preflight](MEMORY_HORIZON_LIVE_PREFLIGHT.md)도 완료되어 main에 반영됐다.
- 실제 Memory Horizon LLM behavioral pilot은 아직 실행하지 않았다.
- 현재는 Distinct Places가 과거 경험 활용과 행동 연속성을 충분히 측정하는지 construct validity를 검토 중이다.
- 두 번째 핵심 연구 축인 정보 변화와 재계획 실험은 아직 설계 단계다.

마일스톤은 의존성 순서다. 다음 단계의 기능을 앞당겨 넣지 않으며, 각 단계는 자동화된 exit criteria를 만족한 뒤 종료한다. 특히 LLM 통합은 결정적 엔진과 정보 경계 검증 이후에만 진행한다.

## M0 Repository/Foundation

목표: 확정된 최소 기술 기준으로 구현 기반을 구성.

- Python 3.12 계열과 단일 package 구조 설정
- pytest, Ruff, mypy와 CI 기본 설정
- 표준 라이브러리 우선 원칙 적용; FastAPI와 LLM SDK는 도입하지 않음
- simulation kernel과 persistence interface 분리
- 표준 라이브러리 `sqlite3` 기반으로 필요한 최소 SQLite persistence 범위 설정; SQLAlchemy/Alembic은 실제 요구가 구체화될 때까지 보류 가능
- 외부 transport 없이 in-process contract부터 정의
- architecture dependency rule과 문서 갱신 규약 설정
- 최소 composition root와 빈 module registration smoke test

Exit: 깨끗한 Python 3.12 환경에서 pytest, Ruff, mypy가 실행되고 in-process 빈 kernel boot test가 통과한다. DB/ORM, HTTP, LLM 의존성이 Core domain을 침범하지 않으며 도메인 기능은 없다.

## M1 Deterministic Simulation Kernel

목표: LLM 없이 재현 가능한 시간과 실행 기반 구축.

- SimulationRun, clock, seeded RNG
- deterministic scheduler와 synchronous EventBus
- Action Handler와 System/Event Handler의 분리된 dispatch 및 공통 deterministic mutation boundary
- Event envelope, transition identity, state digest
- 기록된 input으로 최소 replay harness

Exit: 같은 fixture/seed/input을 반복해 동일 Event ordering과 digest를 얻는다.

## M2 Entity + Spatial Movement

목표: 최소 entity와 Location/Route 이동.

- 최소 Entity identity와 movement-owned position
- Location/Route/passability, MOVE/WAIT
- ActionRegistry dispatch와 atomic validation/resolution
- 시작 검증 → 확정된 duration 동안 system event 처리 → 완료 검증/commit
- REJECTED와 elapsed-time FAILED 구분, system commit 보존, 동일 tick system 우선 ordering과 replay

Exit: 유효, 무효 이동, 시간 비용, route 폐쇄와 invalid action atomicity가 검증된다.

구현 완료: 최소 Entity와 movement-owned 모델, MOVE/WAIT 및 위 exit criteria를 자동화했다. route 폐쇄는 test fixture로만 구현했다.

## M3 Perception + Knowledge

목표: World Truth와 actor별 관찰, 지식을 분리.

- actor-scoped perception context
- ordered ObservationContributor pipeline/envelope
- source-aware KnowledgeRecord
- Game/Controller와 Research/Debug read boundary

Exit: Knowledge Leak Test와 Authority Test가 통과하고 Observation→Action 추적이 가능하다.

구현 완료: 자기 identity/위치만 통과시키는 perception, ordered contributor pipeline, immutable Observation history, source-aware 초기 KnowledgeLedger, actor 고정 GamePort와 별도 ResearchView, Observation→ActionRequest→ActionResult trace를 구현했다. hidden truth/다른 actor 지식/future schedule 누출 반례, 위조 authority, contributor mutation/ordering, 기존 replay 회귀를 자동화했다. runtime Knowledge mutation은 M3 exit에 필요하지 않아 추가하지 않았다. 초기 지식은 명시적 입력만 허용한다. M4의 직접 관찰 projection은 공통 mutation boundary에서 commit된 Event로 재구성하며, 정보 전달은 M5에 남긴다.

2026-09-11 adversarial audit gate 통과: WIP checkpoint에서 발견한 mutable live request identity의 기록 오염과 handler 내부 registry 오류의 오분류를 수정했다. 회귀 테스트를 포함한 전체 pytest/Ruff/format/mypy와 diff 검사를 통과한 뒤 위 M3 완료 상태를 확정했다. runtime Knowledge writer나 M4+ 기능은 추가하지 않았다.

## M4 Alderwick Bridge Vertical Slice + ScriptedController

목표: 핵심 연구 가설을 하나의 작은 시나리오에서 입증.

- Alderwick 최소 지도, actor fixture
- 숨겨진 ScheduledEvent와 System/Event Handler를 통한 East Bridge 붕괴
- bridge condition과 movement route 폐쇄 및 BridgeCollapsed Event의 원자적 전이
- 발생 당시 witness direct Knowledge와 나중 도착의 direct discovery
- committed Event-only Knowledge projection과 다음 actor Observation 연결
- ScriptedController 기반 실제 GamePort end-to-end run 및 기존 engine replay

Exit: [Alderwick Bridge Integration Test](TEST_STRATEGY.md#p0--alderwick-bridge-integration-test)의 M4 항목과 replay가 통과한다. M5 INFORM extension은 제외한다.

구현 완료: 8개 장소/14개 단방향 route/5명 actor fixture, tick 3의 숨은 collapse, East Road/East Bridge 가시성, deterministic witness/arrival Knowledge와 ScriptedController MOVE/MOVE/WAIT 루프를 연결했다. runtime Knowledge는 canonical state가 아닌 초기 기록 + committed Event의 순수 projection이며 Research와 다음 Observation에서 같은 actor history를 읽는다. 동일 tick ordering, partial mutation 방지, delivery 실패 복구, 반복 재구성, 기존 ReplayHarness와 fresh live run의 동등성을 자동화했다. 전체 gate는 기존 225개를 포함한 271개 테스트와 Ruff/format/mypy/diff 검사다. NPC 자율 행동과 social transfer는 M5 미구현 항목으로 유지한다.

## M5 Non-LLM NPC Behavior + Social Information Transfer

목표: LLM 없이 사회적 정보가 이동하는 세계.

- 소수 NPC의 schedule/utility/scripted 행동
- ASK, INFORM, REQUEST의 최소 구조 계약
- claim 출처, 전달, 상충 지식 처리

Exit: 원격 NPC가 자동으로 진실을 알지 못하며 상호작용 뒤 출처가 있는 지식만 얻는다.

구현 완료: SocialModule의 ASK/INFORM/REQUEST v1, 동일 Location/self-target 금지와 시작, 완료 재검증/1 tick, target-scoped interaction perception, live sender Knowledge/원본 Observation claim 검증을 추가했다. INFORM은 claim reference이며 World Truth 복사가 아니다. 기존 M4 direct projector와 ordered Event-prefix social rule을 조합해 초기, 직접, 간접 Knowledge를 재구성한다. source chain, stale/상충 기록과 다단계 전달을 보존한다.

`social=True` Alderwick 예제에서 Hugh 목격→귀환→Thomas ASK→Hugh NPC INFORM→Thomas 다음 Observation/WAIT의 closed loop를 실제 GamePort로 실행한다. activation은 trusted application/example의 고정 순서이고 NPC는 Observation만 읽는 비LLM 정책이다. ActionRequest-only ReplayHarness를 바꾸지 않고 policy 재실행 없이 engine replay하며 fresh live run도 결정적이다. 271개 M0-M4 baseline을 유지한 383개 테스트와 Ruff/format/mypy/diff gate로 exit criteria를 충족했다. canonical Knowledge/inbox, LLM, 일반 NPC framework와 M6/M7 기능은 추가하지 않았다.

## M6 Survival, Inventory + Trade Minimum

목표: 생존 판단을 유발할 최소 자원 흐름.

- 최소 survival state, REST/CONSUME
- inventory ownership/quantity
- 단순 offer/wallet/BUY
- cross-module transaction 규칙

Exit: 자원 보존, 실패 원자성, 시간 경과 효과와 최소 구매, 소비 시나리오가 통과한다.

구현 완료: InventoryModule/SurvivalModule/TradeModule 0.6.0을 분리하고 item identity/정수 수량, 0-100 hunger/fatigue, 정수 Wallet/active Offer를 구현했다. REST v1은 duration tick과 최신 fatigue recovery, CONSUME/BUY v1은 고정 1 tick과 시작, 완료 재검증을 사용한다. BUY는 시작 견적을 고정하고 완료 시 바뀐 가격/seller/item으로 자동 구매하지 않는다. inventory-owned detached helper와 기존 TransitionPlan의 여러 key commit으로 BUY의 inventory+trade, CONSUME의 inventory+survival을 원자적으로 적용한다.

`resources=True` Alderwick의 `resources-1` schedule은 기존 tick 3 bridge collapse와 finite tick 1-20 SurvivalTick을 함께 구성한다. 자기 자원과 local active offer만 perception을 통과하며 private wallet/stock/effect/future schedule은 숨긴다. live WAIT→MOVE→MOVE→BUY 2→CONSUME 1→REST 3의 tick 10 결과는 Stranger wallet 6/bread 1/hunger 30/fatigue 11이다. BUY 보존, 소비 sink provenance, 같은 tick system 우선/latest state, 후보 구성, resolve, 직렬화 실패 원자성, post-commit delivery 실패, recorded ActionRequest-only mixed replay와 fresh-run determinism을 자동화했다. social과 resource를 함께 구성해 M4/M5 Knowledge 의미도 확인했다.

M6 exit gate 통과: 기존 383개 테스트를 삭제, 완화하지 않은 **543 passed**(신규 160개), Python 3.12.10, Ruff `All checks passed!`, format `95 files already formatted`, strict mypy `Success: no issues found in 86 source files`, `git diff --check` 성공. Core/kernel/replay schema와 runtime dependencies는 변경하지 않았다. M7 계약 안정화, 장기 recurring schedule, 일반 effect/economy framework, persistence redesign과 LLM은 추가하지 않았다. commit/push는 수행하지 않았다.

## M7 Complete Action/Observation Contracts

목표: LLM 연결 전에 외부 계약을 안정화.

- 0.1 action payload와 reason code 확정
- schema versioning, idempotency와 compatibility policy
- Observation 내용 예산, 정렬, redaction 규칙
- Controller/Provider/MemoryPolicy 경계 및 conformance tests

Exit: Scripted/Human test adapter가 같은 계약 suite를 통과하고 잘못된 출력은 no-mutation으로 처리된다.

구현 완료: MOVE/WAIT/ASK/INFORM/REQUEST/REST/CONSUME/BUY strict v1, exact type/version compatibility, actor-visible reason/receipt를 동결했다. observe는 read operation이고 TALK/OBSERVE handler는 없다. application-owned cache가 same-ID exact retries의 receipt를 반환하며 changed request는 REQUEST_ID_CONFLICT로 차단한다. past Observation 허용/current tick 검증은 보존했다. action delivery 오류의 committed result 복구, result 없는 kernel 오류의 indeterminate ID 소진, 확실한 pre-kernel read 실패의 기존 recoverable retry를 구분한다.

Observation v1은 stable section identity, deterministic ordering과 canonical UTF-8 65,536-byte budget을 사용한다. 초과 시 fail-closed하며 sequence/history를 소비하지 않고 Knowledge/social 원본을 삭제, 요약, 절단하지 않는다. 정보 경계는 trusted perception이며 BUY의 private seller diagnostic만 receipt에서 가린다. HumanController, 단일 trusted turn helper, Provider/MemoryPolicy Protocol과 NoMemory, Scripted/Human/SocialNpc 공통 conformance를 추가했다.

M7 최종 gate: 기존 543개 연구/안전 회귀를 유지한 **656 passed (신규 113)**, Python 3.12.10, Ruff `All checks passed!`, format `105 files already formatted`, mypy `Success: no issues found in 96 source files`, working/base diff 검사 통과. pre-M7 repeated-ID fixture의 최소 전환과 보존 invariant는 [테스트 전략](TEST_STRATEGY.md#9-m7-contract-gate와-pre-m7-policy-전환)에 기록했다. kernel/ReplayInput/World schema와 runtime dependencies는 변경하지 않았다. M7 exit criteria를 충족하며 실제 Provider 호출, LLMController, 24h 실험, persistence/resume는 포함하지 않는다.

## M8 LLM Adapter + First 24-hour Experiment

목표: 검증된 엔진에 단일 LLMController를 연결하고 첫 연구 실행.

- 한 Provider adapter와 constrained parsing
- prompt/model/parameters/raw output/ActionRequest 기록
- 우선 NoMemory, 필요 시 최소 recent memory 조건
- 24 simulation-hour protocol, tick-to-hour/activation 정의, metrics와 export
- full-log projection/Research history 메모리와 65,536-byte Observation 상한에서 실제 24h 활동량, overflow 처리 검증

Exit: LLM 없이 engine replay가 가능하고, 복수 LLM trial을 통계적으로 비교할 완전한 기록이 생성된다.

M8 구현과 첫 공식 live trial을 완료했다. OpenAI Responses adapter(표준 라이브러리), `gpt-5.6-sol`/medium, strict DecisionCandidate→trusted ActionRequest binding, NoMemory, M8 전용 tick 0-24/hour schedule, versioned prompt/provenance/JSONL export를 추가했다. 기존 743 테스트 목적을 보존하고 재감사 27개를 더한 770 tests와 fake full 24h/replay equality를 확인했다. M6의 finite 1-20 schedule과 M0-M7 의미는 그대로다. 사용자 승인으로 `m8-first-official-sol`을 한 번 실행해 10 calls, tick 24, COMPLETED/HORIZON, invalid/provider failure 0, replay equality=true를 확인했다. M8 exit criteria를 충족하며 후속 통계 비교 실험은 별도 승인 대상이다. [프로토콜](M8_EXPERIMENT.md)과 [실측 결과 및 한계](M8_FIRST_OFFICIAL_TRIAL.md)를 참고한다.

## M8 이후 연구 계보

### Baseline Validation & Hardening

첫 공식 M8 trial 이후 실제 비교 실험 전에 provenance, replay, inclusion, provider identity, MOVE timing과 historical compatibility를 다시 검증했다. 이 단계의 결과와 계약은 M8 문서와 관련 audit 기록에 보존한다.

### Experiment 01 — Event Memory Foundation

[Event Memory Phase 1](EVENT_MEMORY_PHASE1.md)에서 Event Trace v1과 No Event Memory, Recency `k=1`의 최소 correctness를 구현하고 검증했다. 이 단계의 목적은 memory 효과를 주장하는 것이 아니라 **무엇을 한 경험으로 닫고 어떤 과거 경험을 다시 전달하는지**를 신뢰할 수 있게 만드는 것이었다.

### Experiment 02 — Recency Window k=1/2/3

[Recency Window correctness](EVENT_MEMORY_RECENCY_WINDOW.md)에서 같은 recency policy의 `k=1/2/3`이 의도한 closed Event Trace만 올바른 순서와 범위로 전달하는지 검증했다. historical M8과 Phase 1 의미는 보존했으며 이 단계 자체는 행동 성능 비교가 아니다.

### Experiment 03 — Memory Horizon / Distinct Places

[Memory Horizon Offline](MEMORY_HORIZON_OFFLINE.md)에서 네 조건, No Event Memory와 Recency `k=1/2/3`을 비교할 별도 Distinct Places composition을 만들었다. 이후 [Memory Horizon v2](MEMORY_HORIZON_V2.md)에서 model-visible progress/provenance cue를 semantic projection으로 제거하고 raw research provenance는 보존했다.

[Memory Horizon Readiness Audit](MEMORY_HORIZON_READINESS_AUDIT.md)은 actual pilot 전에 model-visible input, failure semantics, metric contract, trial independence를 공격적으로 점검했다. 그 결과 별도 live execution/audit protocol과 trial별 fresh Provider/Controller/Monitor/Application ownership이 필요하다는 점을 확인했다.

[Live Pilot Preflight](MEMORY_HORIZON_LIVE_PREFLIGHT.md)는 이 요구를 recording mock 기반으로 구현하고 검증했다. 12-allocation cohort, wire schema, full outgoing body capture, append-only journal, crash diagnostics, audit/replacement evidence 경계를 포함한다. **이 완료 상태는 기술적 preflight 완료를 뜻하며 actual LLM behavioral effect 입증을 뜻하지 않는다.**

### 현재 연구 gate

현재 Distinct Places 실험은 실행 준비보다 먼저 construct validity를 다시 검토한다. 특히 다음을 확인해야 한다.

- 최근 Event Trace 범위가 정말 행동 연속성의 차이를 측정하는가?
- 단순히 최근 방문 기록에서 이미 간 장소를 제외하는 전략만 측정하는 것은 아닌가?
- No Event Memory 조건의 성공이나 조건 간 차이 없음이 shortcut, leakage, task design 문제와 구분 가능한가?
- 첫 12-allocation run을 protocol pilot으로 해석할 범위가 충분히 명확한가?

이 검토를 통과하고 model/provider/prompt/settings/repetition/order/inclusion/exclusion/primary outcome을 다시 확인한 뒤에만 사용자의 명시적 승인으로 actual external LLM/API pilot을 실행한다.

### 두 번째 연구 축 — Information Change / Replanning

학교 연구과제의 두 번째 핵심 축은 기존 정보와 실제 환경이 달라진 뒤 Agent가 새 정보를 획득하고 기존 계획을 수정하는 과정이다. 현재는 설계 단계이며, 단순 obstacle avoidance가 아니라 최소한 다음 실패 지점을 구분할 수 있는 통제 시나리오를 목표로 한다.

```text
A. 필요한 새 정보를 획득하지 못함
B. 새 정보를 획득했지만 행동에 사용하지 못함
C. 새 정보를 사용했지만 판단 또는 계획이 잘못됨
D. 적절하게 계획을 수정했으나 Engine 결과가 달랐음
```

### 이후 후보

Selective Memory, importance/scoring, forgetting/decay, reflection, memory graph, reinforcement learning은 현재 첫 두 실험의 선행조건이 아니다. 실제 결과가 단순 recency와 현재 Knowledge 구조의 한계를 보여줄 때 별도 연구 질문으로 승격한다.

weather, combat, economy, employment, law, politics, religion, magic은 현재 연구 결과가 필요성을 입증한 뒤 별도 제안으로 검토한다. microservices, 외부 broker, full ECS/event sourcing도 실제 규모와 병목의 근거 없이는 도입하지 않는다.
