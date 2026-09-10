# AGENTS.md

JourneyMap은 Verdandi, URDR, Norn을 계승하지 않는 새 프로젝트다. 레거시 코드를 복사하거나 그 구조를 전제로 삼지 않는다.

## 먼저 읽을 문서

1. `docs/JOURNEYMAP_0.1_SPEC.md`
2. `docs/ARCHITECTURE.md`
3. 작업 영역에 따라 `docs/API.md`, `docs/ERD.md`, `docs/TEST_STRATEGY.md`
4. 순서와 범위는 `docs/ROADMAP.md`

## 절대 규칙

- World Truth와 Agent Knowledge를 분리하고, Observation에는 인물이 인지 가능한 정보만 포함한다.
- Controller와 LLM Provider는 canonical 상태를 직접 변경하거나 Debug/World Truth API에 접근할 수 없다.
- `ActionRequest`는 Actor/Controller의 행동 의도에만 사용한다. ScheduledEvent나 자연 발생 World Event로 대신 사용하지 않는다.
- actor action과 system event는 서로 다른 handler 경로를 사용하되, 모든 canonical mutation은 검증된 handler와 공통 deterministic transaction/mutation boundary를 거친다.
- 같은 초기 상태, seed, Action stream은 같은 결과와 Event 순서를 생성해야 한다.
- 미래 사건을 현재 World Truth의 사실로 저장하지 않는다.
- Core에 hunger, money, weather, combat 같은 도메인 개념을 넣지 않는다. 기능은 Module이 소유한다.
- Action은 `ActionRegistry`, Observation은 perception 이후의 contributor pipeline으로 확장한다. 중앙 거대 분기문을 만들지 않는다.
- 0.x EventBus는 in-process, synchronous, deterministic으로 유지한다.
- 일반 NPC는 우선 scripted/schedule/utility 방식으로 구현한다.
- 현재 milestone 밖의 기능, 불필요한 추상화·의존성·마이크로서비스를 추가하지 않는다.
- M0 기준은 Python 3.12, pytest, Ruff, mypy와 표준 라이브러리 우선이다. FastAPI·LLM SDK·ORM은 필요가 구체화되기 전에 추가하지 않는다.
- 변경 후 관련 테스트와 문서를 함께 갱신한다. 커밋 전 `docs/TEST_STRATEGY.md`의 연구 핵심 불변식을 확인한다.
