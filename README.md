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

현재는 설계 문서만 존재한다. 애플리케이션 구현과 의존성 설치는 아직 시작하지 않았다. 다음 단계는 [로드맵의 M0](docs/ROADMAP.md#m0-repositoryfoundation)이며, 확정된 Python 3.12 기반 기술 기준으로 저장소 규약과 최소 골격을 만든다.
