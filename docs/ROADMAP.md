# Roadmap

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

목표: LLM 없이 재현 가능한 시간·실행 기반 구축.

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

Exit: 유효·무효 이동, 시간 비용, route 폐쇄와 invalid action atomicity가 검증된다.

## M3 Perception + Knowledge

목표: World Truth와 actor별 관찰·지식을 분리.

- actor-scoped perception context
- ordered ObservationContributor pipeline/envelope
- source-aware KnowledgeRecord
- Game/Controller와 Research/Debug read boundary

Exit: Knowledge Leak Test와 Authority Test가 통과하고 Observation→Action 추적이 가능하다.

## M4 Alderwick Bridge Vertical Slice + ScriptedController

목표: 핵심 연구 가설을 하나의 작은 시나리오에서 입증.

- Alderwick 최소 지도·actor fixture
- 숨겨진 ScheduledEvent와 System/Event Handler를 통한 East Bridge 붕괴
- 목격 기반 지식 갱신과 route 폐쇄
- ScriptedController 기반 end-to-end run/replay

Exit: [Alderwick Bridge Integration Test](TEST_STRATEGY.md#p0--alderwick-bridge-integration-test)와 replay가 통과한다.

## M5 Non-LLM NPC Behavior + Social Information Transfer

목표: LLM 없이 사회적 정보가 이동하는 세계.

- 소수 NPC의 schedule/utility/scripted 행동
- ASK, INFORM, REQUEST의 최소 구조 계약
- claim 출처·전달·상충 지식 처리

Exit: 원격 NPC가 자동으로 진실을 알지 못하며 상호작용 뒤 출처가 있는 지식만 얻는다.

## M6 Survival, Inventory + Trade Minimum

목표: 생존 판단을 유발할 최소 자원 흐름.

- 최소 survival state, REST/CONSUME
- inventory ownership/quantity
- 단순 offer/wallet/BUY
- cross-module transaction 규칙

Exit: 자원 보존, 실패 원자성, 시간 경과 효과와 최소 구매·소비 시나리오가 통과한다.

## M7 Complete Action/Observation Contracts

목표: LLM 연결 전에 외부 계약을 안정화.

- 0.1 action payload와 reason code 확정
- schema versioning, idempotency와 compatibility policy
- Observation 내용 예산·정렬·redaction 규칙
- Controller/Provider/MemoryPolicy 경계 및 conformance tests

Exit: Scripted/Human test adapter가 같은 계약 suite를 통과하고 잘못된 출력은 no-mutation으로 처리된다.

## M8 LLM Adapter + First 24-hour Experiment

목표: 검증된 엔진에 단일 LLMController를 연결하고 첫 연구 실행.

- 한 Provider adapter와 constrained parsing
- prompt/model/parameters/raw output/ActionRequest 기록
- 우선 NoMemory, 필요 시 최소 recent memory 조건
- 24 simulation-hour protocol, metrics와 export

Exit: LLM 없이 engine replay가 가능하고, 복수 LLM trial을 통계적으로 비교할 완전한 기록이 생성된다.

## 이후 후보

weather, combat, economy, employment, law, politics, religion, magic은 0.1 결과가 필요성을 입증한 뒤 별도 제안으로 검토한다. microservices, 외부 broker, full ECS/event sourcing도 실제 규모와 병목의 근거 없이는 도입하지 않는다.
