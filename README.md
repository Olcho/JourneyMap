# JourneyMap

JourneyMap은 중세 배경의 지속형 가상 세계에 단일 LLM 인격체를 한 명의 캐릭터 Controller로 투입하고, 제한된 정보와 능력 아래에서 어떻게 판단하고 행동하며 살아가는지 관찰하는 시뮬레이션 연구 프로젝트다.

세계는 LLM 없이도 진행되어야 한다. LLM은 세계 관리자·서술자·판정자가 아니며, `Observation`을 받아 구조화된 `ActionRequest`를 반환하는 교체 가능한 Controller 중 하나다. 모든 canonical 상태 변화는 엔진 검증과 판정을 거친다.

## 핵심 루프

```text
World progresses
→ Perception
→ Observation
→ Controller decision
→ ActionRequest
→ engine validation
→ engine resolution
→ World Truth mutation
→ Event logging
→ Knowledge update
→ next Observation
```

## 0.1 목표

작은 중세 마을 Alderwick에서 단일 실험 대상 캐릭터, 소수의 비LLM NPC, 부분 관찰, 지식 전파, 이동, 최소 생존·소지품·거래, 구조화된 행동, 사건 기록과 결정적 엔진 재현을 검증한다.

첫 수직 슬라이스인 **A Stranger in Alderwick**에서는 예정된 시점에 East Bridge가 붕괴한다. 붕괴 전의 미래 사건은 World Truth에 사실로 존재하지 않으며, 발생 후에도 목격하거나 전달받은 인물만 이를 알 수 있다.

## 문서 안내

- [0.1 명세](docs/JOURNEYMAP_0.1_SPEC.md): 목표, 범위, 시나리오, 수용 기준
- [아키텍처](docs/ARCHITECTURE.md): Core/Module 경계와 의존성
- [ERD](docs/ERD.md): 영속 데이터와 연구 기록
- [API](docs/API.md): Game/Controller 및 Research/Debug 인터페이스
- [테스트 전략](docs/TEST_STRATEGY.md): 결정론·권한·정보 누출 검증
- [로드맵](docs/ROADMAP.md): M0부터 첫 24시간 실험까지
- [연구 참고 문헌](docs/RESEARCH_REFERENCES.md): 조사할 선행 연구와 적용 질문
- [Codex 작업 지침](AGENTS.md): 다음 세션이 지켜야 할 최소 규칙

## 현재 상태

M2 Entity + Spatial Movement까지 구현되어 있다. M1의 결정적 kernel 위에 최소 Entity identity, movement-owned Location/단방향 Route/ActorPosition과 MOVE/WAIT를 추가했다. 시간 비용이 있는 action은 시작 검증 후 완료 tick까지 system event를 처리하고 현재 상태를 재검증한다. 완료 조건 실패는 action의 성공 mutation만 생략하며 경과 시간과 system commit은 유지한다. 다음 구현 범위는 [M3 Perception + Knowledge](docs/ROADMAP.md#m3-perception--knowledge)다. Observation/Knowledge와 Alderwick 실제 시나리오는 아직 없다.

M2 composition은 `MovementModule.register_actions(action_registry)`로 MOVE를, `action_registry.register("WAIT", 1, WaitHandler())`로 WAIT를 명시적으로 등록한다. 초기 canonical state는 `{"entities": entity_state(...), "movement": movement_state(...)}`로 구성하고 module·registry·state를 `create_kernel`에 전달한다. payload와 시간 계약은 [API의 M2 절](docs/API.md#8-m2-구현-계약)을 따른다.

## 개발 환경과 검증

Python 3.12 이상에서 개발용 의존성을 설치하고 M0 품질 gate를 실행한다.

```text
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy
pytest
```

기능 또는 계약을 변경할 때는 같은 변경에서 관련 테스트와 문서를 갱신한다. milestone 범위를 바꾸는 변경은 `docs/ROADMAP.md`, 논리 계약은 `docs/API.md`, 상태·저장 소유권은 `docs/ERD.md`, 계층과 의존 방향은 `docs/ARCHITECTURE.md`, 검증 규칙은 `docs/TEST_STRATEGY.md`에 반영한다.
