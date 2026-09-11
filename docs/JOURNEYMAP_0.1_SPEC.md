# JourneyMap 0.1 Specification

## 1. 목적

JourneyMap 0.1은 지속되는 소규모 중세 세계에서 한 명의 실험 대상 캐릭터가 제한된 관찰과 지식을 바탕으로 행동하는 과정을 재현 가능하게 기록하는 최소 연구 환경이다.

핵심 연구 질문은 “모델이 무엇을 알고 있었으며, 그 정보로 어떤 결정을 내렸고, 엔진이 그 요청을 어떻게 판정했는가?”다. 따라서 풍부한 세계 생성보다 정보 경계, 권한 경계, 추적 가능성과 결정론을 우선한다.

## 2. 불변 원칙

1. **World Truth ≠ Agent Knowledge.** 실제 세계 상태와 각 인물이 믿거나 아는 내용을 별도로 저장한다.
2. **관찰 가능성 우선.** Observation은 위치, 감각, 시점, 가시성 등 perception 규칙을 통과한 정보로만 구성한다.
3. **최소 권한 Controller.** LLM을 포함한 Controller에는 전역 상태나 디버그 데이터가 제공되지 않는다.
4. **구조화된 actor 의도.** 자연어 출력은 상태를 직접 변경하지 않으며, Actor/Controller의 의도는 ActionRequest로만 엔진에 전달된다. ScheduledEvent나 자연 발생 World Event는 ActionRequest가 아니다.
5. **엔진 판정 권위.** actor action과 system event는 의미가 다른 handler 경로를 사용하지만, 검증된 handler와 공통 deterministic transaction/mutation boundary만 canonical 상태를 변경할 수 있다.
6. **LLM 독립 세계.** 시간, schedule과 비LLM NPC 행동은 LLM 없이 진행 가능해야 한다.
7. **연구 추적성.** Event, Observation, ActionRequest, ActionResult, KnowledgeRecord를 독립 기록으로 보존한다.
8. **결정적 엔진 재현.** 초기 상태와 versioned schedule, seed, ActionRequest stream과 엔진 버전이 같으면 결과와 event ordering도 같아야 한다.
9. **미래와 현재 분리.** 아직 일어나지 않은 사건은 World Truth의 사실이 아니다.
10. **범위 절제.** 실험에 필요한 가장 작은 기능을 우선하며 현재 milestone 밖의 기능을 임의로 넣지 않는다.

## 3. 시스템 역할

- **World Engine:** 시간 진행, action/system event 검증·판정, 공통 mutation 경계와 event 발행을 책임진다.
- **Module:** 이동, 생존, 소지품, 지식, 사회, 거래 같은 도메인 상태와 규칙을 소유한다.
- **Perception:** World Truth와 발생 Event에서 특정 actor가 인지할 수 있는 사실만 산출한다.
- **Observation Pipeline:** perceived fact/context를 Controller 입력 계약으로 조립한다.
- **Controller:** Observation을 하나의 ActionRequest로 변환한다. 0.1은 ScriptedController를 먼저 사용한다.
- **Provider:** LLM 호출과 원시 출력 취득을 담당한다. LLMController와 분리한다.
- **MemoryPolicy:** Controller가 사용할 회상·요약 전략을 교체한다. 0.1 구현의 필수 기능은 아니며 인터페이스 경계만 보존한다.
- **Research/Debug Client:** 실행 재현과 분석을 위해 권한 있는 전체 기록을 읽는다. 게임 Controller와 동일한 자격 증명이나 포트를 사용하지 않는다.

## 4. 0.1 기능 범위

- Location/Route 기반 공간과 route 상태
- simulation clock, seeded RNG, deterministic scheduler와 synchronous EventBus
- 단일 실험 대상 캐릭터와 소수의 비LLM NPC
- actor별 부분 관찰과 Agent Knowledge
- 기본 이동과 대기
- 단순한 생존 자원 및 휴식·소비
- 최소 inventory
- 구조화된 대화·정보 전달
- 단순 구매
- ActionRequest/ActionResult 계약 및 action 등록
- ScheduledEvent와 System/Event Handler 계약
- Observation envelope 및 contributor pipeline
- Event와 연구 데이터 기록
- versioned scenario schedule과 기록된 ActionRequest stream 기반 엔진 replay
- ScriptedController, 이후 LLMController 연결 지점

M7에서 동결한 0.1 action set은 `MOVE`, `WAIT`, `ASK`, `INFORM`, `REQUEST`, `REST`, `CONSUME`, `BUY`의 strict v1이다. exact payload는 [API 1절](API.md#1-game--controller-interface)을 따른다. `OBSERVE`는 GamePort.observe() read operation이며 `TALK` action은 없다.

## 5. 명시적 비범위

행성·대륙·지질·기후의 정교한 생성, 거시경제, 국가 규모 정치·외교·전쟁, 복잡한 전투, 마법, 계보·문장·깃발, 언어 생성, 다중 LLM agent, 일반 NPC의 LLM 제어, three-AI Norn 구조, 그래픽/3D 세계, microservices, Kafka·외부 broker, full event sourcing, full ECS는 0.1에 포함하지 않는다.

## 6. 첫 수직 슬라이스: A Stranger in Alderwick

### 세계

West Gate, Village Square, Inn, Bakery, Well, Smithy, East Road, East Bridge를 route로 연결한 작은 마을이다. M4의 Stranger, Marta(innkeeper), Edwin(baker), Hugh(guard), Thomas(traveler)는 Entity와 위치 fixture다. M5는 Hugh/Thomas의 Observation 기반 비LLM rule 행동과 명시적 application activation 순서를 추가한다.

### 통제 사건

시나리오 scheduler 또는 test fixture가 특정 simulation time에 East Bridge 붕괴를 `ScheduledEvent`로 예약한다. 그 예약은 scenario 실행 설정일 뿐 현재의 World Truth나 어떤 actor의 지식이 아니다. due time이 되면 별도의 System/Event Handler가 검증·판정하며 ActionRequest로 변환하지 않는다. handler가 승인한 변화만 actor action과 공유하는 deterministic transaction/mutation boundary에서 적용된다.

붕괴가 발생하면 한 원자적 engine transition 안에서:

1. scenario-owned bridge condition이 `collapsed`로, movement-owned 관련 route의 `passable`이 false로 변경된다. bridge condition과 route passability는 별개 사실이다.
2. `BridgeCollapsed` Event가 결정적 순서로 기록된다.
3. 발생 당시 East Road/East Bridge에 있는 witness ID를 Event에 정렬해 기록한다.

commit 이후 knowledge module은 초기 Knowledge와 committed Event에서 직접 획득 기록을 결정적으로 재구성한다. 별도 Knowledge canonical write나 EventBus side effect는 없다. trusted perception은 현재 위치에서 보이는 bridge identity/condition만 통과시키며, Observation은 perceived fact와 actor-owned KnowledgeRecord를 별도 section으로 제공한다. 멀리 있는 actor의 지식은 자동으로 바뀌지 않는다.

M4에서는 이후 현장에 도착한 actor가 직접 발견한다. `BridgeCollapsed` 이후의 `ActorMoved` 순서를 사용하며 최종 World snapshot을 과거 이동에 소급 적용하지 않는다. M5에서는 `INFORM`으로 출처가 있는 간접 지식을 전달한다. 전달된 주장은 World Truth로 승격되지 않는다. M4에는 confidence 모델이나 social transfer가 없다.

### 수직 슬라이스 수용 기준

- 붕괴 전 Observation과 KnowledgeRecord에 미래 붕괴 사실이 없다.
- 목격자와 비목격자의 붕괴 직후 지식이 다르다.
- M4 비목격자는 나중에 관찰 가능한 위치에 도착하면 직접 알 수 있다. M5에서는 유효한 정보 전달 후 간접적으로 알 수 있다.
- 붕괴 route를 사용하려는 MOVE는 실패하고 canonical 상태를 부분 변경하지 않는다.
- 모든 관련 ScheduledEvent, Observation, ActionRequest, ActionResult, Event, KnowledgeRecord의 실행·actor·시간·인과관계를 추적할 수 있다.
- 같은 fixture, seed, ScheduledEvent 입력과 ActionRequest stream을 replay하면 state digest, handler 결과와 Event 순서가 같다.
- M4는 실제 `observe → ScriptedController.decide → submit → next observe` 루프와 Event-only Knowledge 재구성까지 검증한다. 동일한 observe 호출 순서를 가진 fresh run의 Observation/요청/결과/Event/Knowledge도 같다.

### M5 사회적 정보 전달 수용 기준 — 구현 완료

- ASK/INFORM/REQUEST v1은 같은 Location의 서로 다른 actor 사이에서 1 tick을 소비하는 행동이다. 시작·완료 시 canonical interaction 조건을 재검증한다.
- ASK는 구조화된 topic을 질문할 뿐 자동 답변이나 target 지식 조회를 하지 않는다. REQUEST는 구조화된 요청 전달이며 내용의 자동 실행·fulfillment가 아니다.
- INFORM은 sender-owned KnowledgeRecord 하나의 reference다. live application은 현재 지식 소유권/run/획득 시점과 실제 원본 Observation에 포함된 record를 확인한다. 임의 structured claim assertion은 허용하지 않는다.
- `ActorInformed` commit 후 target에게 `INFORMED` record가 projection된다. World Truth와 비교·승격·정정하지 않으며 직접 기록은 `DIRECT_OBSERVATION`으로 구분한다.
- receiver record → social Event → sender claim record → 직접 관찰 또는 이전 전달 Event의 chain을 Research에서 탐색한다. stale/상충 record와 모든 전달 단계를 보존하며 confidence·trust·belief inference는 없다.
- trusted perception은 자기에게 온 interaction만 contributor에 전달한다. NPC는 그 Observation으로 별도 INFORM을 제출하며 전체 Event log나 다른 actor의 지식을 읽지 않는다.
- M5 예제는 tick 3 collapse → Hugh MOVE → Thomas ASK → Hugh INFORM → Thomas WAIT를 실증한다. Thomas가 직접 보지 않은 collapsed claim을 tick 7에 처음 전달받고, 초기 intact claim은 남는다.
- 동일 activation 입력의 fresh run 및 recorded NPC ActionRequest-only engine replay에서 결과/Event/order/world/digest/time/지식이 같다. M4 기본 composition은 유지하며 M5는 `social=True`로 활성화한다.

### M6 자원 제약 수용 기준 — 구현 완료

- inventory는 stable item identity와 owner별 정수 수량, survival은 0–100의 hunger/fatigue와 consumable hunger recovery, trade는 단일 정수 통화의 wallet과 offer를 소유한다. Entity/Core에는 도메인 속성을 추가하지 않는다.
- `SurvivalTick` v1은 actor action이 아닌 별도 system input이다. due transition에서 hunger +2/fatigue +1을 100까지 올리고 actor ID 순서로 `SurvivalAdvanced`를 기록한다. Observation 읽기나 wall clock은 상태를 변경하지 않는다.
- REST v1은 양의 정수 duration만큼 실제 시간을 소비하고 완료 시 최신 fatigue에서 duration ×3을 감소시킨다. CONSUME v1은 1 tick 뒤 자기 inventory를 감소시키고 최신 hunger를 bread 1개당 10씩 낮춘다. 값은 0에서 제한한다.
- BUY v1은 동일 Location의 다른 seller와 1 tick 동안 상호작용한다. 시작 견적을 고정하고 완료 때 entity/position/offer/stock/funds를 재검증한다. 가격·seller·item 변경은 새 조건으로 자동 구매하지 않는다.
- BUY의 inventory+wallet과 CONSUME의 inventory+survival은 각각 하나의 TransitionPlan/Event와 함께 커밋된다. BUY는 item/currency 합계를 보존하고 CONSUME의 의도된 item sink는 ItemConsumed quantity와 정확히 대응한다.
- 자기 survival/inventory/wallet과 local active offer만 trusted perception을 통과한다. 다른 actor의 inventory/wallet, 효과 설정, remote offers와 미래 tick schedule은 공개하지 않는다.
- 같은 tick의 due system inputs를 먼저 commit한 뒤 action completion을 판정한다. REJECTED에는 추가 시간 비용이 없고 FAILED에는 경과 시간과 독립 system commits가 남는다. 두 경우 action 성공 mutation/Event는 없다.
- `resources=True`는 별도 `alderwick/resources-1` fixture/schedule이다. 기존 bridge 붕괴를 포함하고 tick 1–20만 survival input을 예약한다. 이후 자동 tick 연장은 없으며 이 fixture로 장기 survival 실행을 주장하지 않는다.
- live WAIT→MOVE→MOVE→BUY→CONSUME→REST, 실패 주입, private resource leak 반례, ActionRequest-only replay와 fresh-run determinism을 검증한다. `social=True, resources=True`에서도 기존 직접/간접 Knowledge provenance를 유지한다.

M6 도메인 의미는 아래 M7에서도 보존한다. crafting/equipment/health/death/복잡한 economy와 실제 LLM은 M7 범위 밖이다.

### M7 Action / Observation contract 수용 기준 — 구현 완료

- run당 하나의 application에서 동일 normalized ActionRequest ID의 SUCCEEDED/REJECTED/FAILED/권한 denial은 같은 receipt로 재전송하고 kernel을 다시 실행하지 않는다. 다른 요청의 ID 재사용은 REQUEST_ID_CONFLICT다. normalization은 9개 envelope 필드 전체의 canonical JSON 비교이고 caller binding도 확인한다.
- ActionRequest envelope·strict v1 payload·정확한 type/version dispatch·actor-visible reason code를 [API](API.md#13-m7-최종-compatibility--retry--observation--controller-계약)와 conformance tests로 동결한다. unknown type/version은 UNKNOWN_ACTION이며 암묵 migration은 없다.
- post-commit Event delivery 오류의 최초 ENGINE_ERROR/Research 성공 result는 유지하고 exact retry에서 확정 receipt를 복구한다. kernel 진입 후 result 없는 오류는 consumed/indeterminate로 재실행하지 않는다. 진입 전 projection/read 실패만 예약을 해제하여 기존 recoverable semantics를 유지한다.
- 과거 Observation 참조와 새 요청의 current tick 검증은 독립이다. stale belief의 현재 truth에 대한 REJECTED/FAILED는 유효한 연구 결과다. exact retry는 이미 확정된 결과를 반환하므로 current tick 검증을 반복하지 않는다.
- Observation v1은 immutable envelope/content, run-local 성공 sequence, canonical content digest와 stable section identity를 가진다. 정렬은 (priority,module_id,contributor_id), visible identity 중복은 거부한다.
- content는 canonical JSON UTF-8 65,536 bytes까지 전체 제공한다. 초과 시 OBSERVATION_UNAVAILABLE이며 partial history/sequence와 world mutation이 없다. 임의 truncation, Knowledge/social/Research history 삭제, token/model dependency는 없다.
- 정보 경계는 trusted actor-scoped perception의 positive projection이다. raw World/Research/scheduler/RNG는 contributor·Controller·Provider·Memory에 전달하지 않는다. BUY의 private seller table diagnostics는 공개 receipt에서 일반 status code로 가리되 kernel/domain 판정과 Research는 보존한다.
- trusted one-turn helper는 잘못된 Controller output/exception을 submit 전에 차단하고 최소 sanitized turn record를 반환한다. Scripted/Human/SocialNpc의 동일 conformance를 검증한다. Human은 injected source만 사용하며 CLI/UI는 없다.
- Provider와 MemoryPolicy는 최소 Protocol, NoMemory는 빈 선택만 제공한다. 실제 LLM SDK/API, memory engine, agent-loop framework는 없다. 의존 방향은 future LLMController → Provider/Memory/parser이며 Core/domain은 이들을 import하지 않는다.
- live attempts와 engine replay inputs를 구분하며 ReplayInput schema·kernel semantics는 그대로다. 기존 543개 테스트의 연구/안전 invariant는 유지하고 M7이 대체한 repeated-ID 실행 정책의 fixture만 최소 전환한다.

Observation 출력 상한은 full-log projection의 처리 비용이나 Research 메모리 상한이 아니다. M8은 24 simulation-hour의 tick 단위, 활동량, 용량과 export를 정하고 실제 Provider 실험을 수행해야 한다.

## 7. 실행과 시간 의미

- 하나의 `SimulationRun`은 불변 seed, scenario/version, engine version과 초기 상태 참조를 가진다.
- engine transition은 단일 스레드의 논리적 순서로 처리한다. 병렬 최적화는 0.1 범위가 아니다.
- scheduler 항목은 `(due_time, priority, insertion_sequence)`처럼 완전 정렬 가능한 key를 가져야 한다.
- 한 transition에서 파생된 Event는 명시된 emission sequence를 갖고 동기적으로 처리된다.
- wall-clock time이나 자료구조 순회 순서에 simulation 결과를 의존시키지 않는다.

## 8. 연구 재현성

### Engine replay

```text
initial state/scenario schedule + seed + engine/schema versions + recorded ActionRequests
→ deterministic ActionResults + Events + final state digest
```

### LLM experimental repeat

LLM 응답의 bit-identical 재현은 가정하지 않는다. trial마다 provider, model, prompt version, parameters, 전달된 Observation, 원시 응답, parsing 결과와 최종 ActionRequest를 기록하고 반복 실험을 통계적으로 비교한다.

## 9. 0.1 완료 조건

- 위 수직 슬라이스와 [테스트 전략](TEST_STRATEGY.md)의 최우선 테스트가 자동화되어 통과한다.
- ScriptedController만으로 세계 진행과 replay가 가능하다.
- Game/Controller 인터페이스와 Research/Debug 인터페이스의 권한이 분리되어 있다.
- 최소 이동·지식·사회·생존·inventory·trade 흐름이 구조화된 계약을 사용한다.
- LLM adapter로 24 simulation-hour 실험을 실행하고 연구 기록을 내보낼 수 있다.
