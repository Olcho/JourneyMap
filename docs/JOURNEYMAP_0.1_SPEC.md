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

초기 action type 후보는 `OBSERVE`, `MOVE`, `ASK`, `INFORM`, `REQUEST`, `BUY`, `CONSUME`, `REST`, `WAIT`다. action type과 payload는 버전이 있는 schema로 정의한다. 자유 형식 `TALK`를 canonical 의미로 임의 해석하지 않는다.

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

M6 생존·inventory·trade와 M7 최종 idempotency/compatibility/Observation budget은 구현 범위 밖이다.

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
