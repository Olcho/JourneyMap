# JourneyMap

2026-10-16 본체 Phase 4.4는 high를 유지하고 요청당 출력 상한을 8,192로 늘린다.
생활 전용 wire와 paid/preflight v2를 사용하고 기존 연구 4,096 상한 및 동결 소스의 과거 replay를 보존한다.
[변경 계약](docs/PHASE4_4_OUTPUT_BUDGET.md), [검증 보고서](docs/PHASE4_4_REPORT_2026-10-16.md),
[노트북 시작](ACTUAL_START_HERE.md)을 따른다.
추가 실제 실행은 별도 승인 대상이다.

2026-10-15 Phase4.3.2는 성공 전달·owner 회수에서 call.raw를 역방향으로 요구하고,
READY 이후 상태를 journal의 분류·접수·완료와 대조한다. 실제 실행은 재검토·별도 승인 대상이다.
[현재 응답 감사 계약](docs/PHASE4_3_2_RESPONSE_AUDIT.md),
[보고서](docs/PHASE4_3_2_REPORT_2026-10-15.md)를 따른다.

2026-10-15 Phase4.3.1은 저장 replay의 호출/journal 삭제 및 회계 summary 변조를 거부하도록
snapshot 완전성·양방향 기록·회계를 공통 검증한다. 실제 실행은 재검토·별도 승인 대상이다.
[감사 계약](docs/PHASE4_3_1_AUDIT.md), [보고서](docs/PHASE4_3_1_REPORT_2026-10-15.md)를 따른다.

2026-10-15 Phase 4.3은 생활 전용 HTTPS, 승인/run/source 결합, 영속 journal과 late 회계 회수,
data-only replay를 구현하고 가짜 connection으로 검증한다. 실제 키/API 실행은 별도 승인 대상이다.
[계약·미실행 명령](docs/PHASE4_3_PAID_PATH.md), [완료 보고서](docs/PHASE4_3_REPORT_2026-10-15.md)를 따른다.

2026-10-15 Phase 4.2는 생활 Responses wire·응답·usage·한도를 recording transport로 검증하고
실제 호출 전 [고정 실행 제안](docs/PHASE4_2_LIVE_PREFLIGHT.md)을 제공한다.
[검증 보고서](docs/PHASE4_2_REPORT_2026-10-15.md)를 따른다. 실제 연결/키 조회/실행 승인은 포함하지 않는다.

2026-10-15 Phase 4.1은 독립 검토의 깊은 JSON 오류 상태와 poll 순서에 의존하던 timeout을 보완한다.
[오류·완료 시각 계약](docs/PHASE4_1_ERROR_BOUNDARIES.md),
[검증 및 완료 보고서](docs/PHASE4_1_REPORT_2026-10-15.md)를 따른다. 기본 실행과 생활 기능은 유지한다.

2026-10-15 본체 Phase 4는 기본 전원 RULE을 유지하면서 Ada만 모의 Provider adapter에
연결하는 opt-in 생활 실행을 제공한다. 방향별 지도 표시, 세계 시간 동결 대기,
오류/한도 정지와 혼합 checkpoint/replay를 검증한다. 실제 LLM/API 실행은 포함하지 않는다.
[계약·실행법·실제 실행 전 계획](docs/PHASE4_OFFLINE_LLM.md),
[완료 보고서](docs/PHASE4_REPORT_2026-10-15.md)를 따른다.

2026-10-15 본체 Phase 3은 기존 미커밋 Phase 1+2에 로컬 실시간 관찰 UI와
LifeSession 직접 체크포인트 저장/복원을 추가한다. 시작/정지/단계/배속과
연구자·인물 보기, 새 프로세스 재개 및 168시간 종료를 지원한다.
[실행법과 저장 계약](docs/PHASE3_LIVE_CHECKPOINT.md)을 따른다.
[완료 보고서와 검증](docs/PHASE3_REPORT_2026-10-15.md)을 함께 확인한다.

2026-10-14 본체 Phase 2는 같은 미커밋 개발 작업 트리에 5명/7일의 headless 생활 순환을 추가한다.
[노동·생산·임금·욕구·정보 계약과 실행법](docs/PHASE2_LIFE_LOOP.md),
[실제 실행·검사·검토 ZIP 보고서](docs/PHASE2_REPORT_2026-10-14.md)를 따른다.
기존 연구 시나리오는 보존하며 UI/영속 저장/LLM 실행은 이번 범위에 없다.

2026-10-14 본체 통합 개발 Phase 1은 별도 opt-in concurrent-v1에서 다수 actor의
겹치는 행동 구간, 명시적 시간 진행과 최소 규칙 기반 재활성화를 제공한다.
[시간·정보·저장 계약과 실행법](docs/PHASE1_CONCURRENT_RUNTIME.md),
[완료 보고서](docs/PHASE1_REPORT_2026-10-14.md)를 참고한다. 기존 연구 gate와 E03/E04
원본은 보존하며, 이번 사용자 결정으로 E05 완료를 개발의 선행조건으로 삼지 않는다.

JourneyMap은 지속형 가상환경에서 단일 LLM Agent가 제한된 관찰, 지식, 과거 경험을 바탕으로 어떻게 판단하고 행동하며, 환경 변화 뒤 계획을 어떻게 수정하는지 통제된 조건에서 관찰하기 위한 deterministic simulation research platform이다.

현재 실험 세계는 작은 중세 정착지 **Alderwick**를 사용한다. 세계의 실제 상태와 Agent가 아는 정보는 분리되며, LLM은 세계 관리자나 판정자가 아니라 한 Actor의 교체 가능한 Controller다. LLM은 자신에게 허용된 `Decision Context`를 보고 구조화된 `ActionRequest`를 제안하고, 실제 canonical 상태 변화는 Engine이 검증하고 처리한다.

이 프로젝트가 관찰하는 대상은 숨겨진 chain-of-thought가 아니라 다음과 같은 외부에서 확인 가능한 기록이다.

- Agent에게 실제로 제공된 `Observation`, `Knowledge`, `Retrieved Event Memory`
- 모델이 제출한 구조화된 `ActionRequest`
- Engine이 판정한 행동 결과
- canonical world state 변화
- 이후 생성된 Event Trace와 재현 가능한 연구 기록

## 핵심 연구 루프

```text
Ground Truth
    ↓
Perception
    ↓
Current Observation ──────────────┐
                                  │
Agent Knowledge ──────────────────┼→ Decision Context → Controller / LLM
                                  │                         ↓
Event Trace Archive               │                    ActionRequest
    ↓                             │                         ↓
Memory Policy                     │                    Engine Validation
    ↓                             │                         ↓
Retrieved Event Memory ───────────┘                    World Mutation
                                                            ↓
                                                       Actor-visible Result
                                                            ↓
                                                       new Event Trace
```

핵심 원칙은 다음과 같다.

1. `Ground Truth`, `Observation`, `Agent Knowledge`, `Event Memory`를 섞지 않는다.
2. LLM은 canonical world state를 직접 수정하지 않는다.
3. Actor Action과 Scheduled/System Event는 서로 다른 입력 경로를 사용한다.
4. 모든 canonical mutation은 검증된 handler와 deterministic mutation boundary를 통과한다.
5. 같은 초기 상태, seed, recorded ActionRequest stream은 같은 Engine 결과와 Event 순서를 재현해야 한다.
6. Research Archive와 Agent가 실제로 접근할 수 있는 정보 범위를 분리한다.

## 현재 연구 질문

현재 연구는 크게 두 단계로 진행한다.

### 1. 과거 경험 접근 범위와 행동 연속성

과거 Event Trace를 제공하지 않는 조건과 최근 경험을 `k=1/2/3` 범위로 제공하는 조건을 비교한다.

현재 구현된 **Memory Horizon / Distinct Places** 시나리오에서는 Agent가 `Inn`, `Bakery`, `Well`을 각각 한 번 방문하고 매 방문 뒤 `Village Square`로 돌아오도록 한다. Engine은 방문 완료 목록이나 다음 목적지를 요약해서 알려주지 않는다.

이 실험의 기술적 목적은 최근 과거 경험 접근 범위만 달라졌다고 말할 수 있는 통제 환경을 만드는 것이다. 다만 현재는 이 과제가 실제로 넓은 의미의 기억 활용을 측정하는지, 아니면 최근 방문 기록에서 이미 간 장소를 제외하는 단순 전략으로 해결되는지 **construct validity를 재검토 중**이다.

따라서 아직 다음과 같은 결론은 주장하지 않는다.

- Event Memory가 행동 성능을 향상한다.
- `k=3`이 `k=1` 또는 `k=2`보다 낫다.
- Distinct Places가 장기기억 구조 전체를 대표한다.
- mock 또는 scripted provider 결과가 실제 LLM behavioral evidence다.

### 2. 정보 변화와 재계획

두 번째 연구 축은 Agent가 기존에 알고 있던 정보와 실제 환경이 달라졌을 때, 새로운 정보를 획득하고 이후 행동에 반영해 기존 계획을 수정하는지 관찰하는 것이다.

이 실험은 현재 설계 단계이며 아직 구현 완료 상태가 아니다. 단순 obstacle avoidance가 아니라 다음을 구분할 수 있는 통제 시나리오를 목표로 한다.

```text
A. 필요한 새 정보를 획득하지 못함
B. 새 정보를 획득했지만 행동에 사용하지 못함
C. 새 정보를 사용했지만 판단 또는 계획이 잘못됨
D. 적절하게 계획을 수정했으나 Engine 결과가 달랐음
```

## 구현 및 검증 상태

M0부터 M8까지 deterministic research baseline을 구축한 뒤 Event Memory 연구 계층을 추가했다.

| 단계 | 상태 | 의미 |
| --- | --- | --- |
| M0-M8 baseline | 완료 | deterministic kernel, movement, perception/knowledge, social, survival/trade, action contracts, LLM adapter, 첫 공식 24h trial |
| Baseline Validation & Hardening | 완료 | provenance, inclusion, replay, provider identity, historical compatibility 검증 |
| Experiment 01 Event Memory Foundation | 완료 | Event Trace v1, No Event Memory, Recency `k=1` correctness |
| Experiment 02 Recency Window | 완료 | Recency `k=1/2/3` retrieval correctness와 backward compatibility |
| Experiment 03 Memory Horizon v2 | 완료 | semantic Decision Context로 progress/provenance cue를 model-visible input에서 제거 |
| Live Pilot Preflight | 완료 | 별도 live protocol/audit, 12-allocation cohort, Horizon wire schema, fresh runtime ownership, recording mock transport, journal/audit 경계 |
| Actual Memory Horizon behavioral pilot | 미실행 | 연구 타당성과 최종 study 설정 검토 후 사용자 명시 승인 필요 |
| Information change / replanning experiment | 설계 중 | 두 번째 핵심 연구 축, 아직 구현 완료 아님 |

중요하게, **기술적 preflight 완료와 behavioral effect 입증은 다른 상태**다.

현재 Live Pilot Preflight는 recording mock만 사용하는 기술 검증이다. 실제 network/API 호출, real OpenAI Provider, live-capable CLI, automatic resume는 포함하지 않는다. 자세한 범위는 [Memory Horizon Live Pilot Preflight](docs/MEMORY_HORIZON_LIVE_PREFLIGHT.md)를 따른다.

과거 M8 공식 trial은 별도 historical evidence로 보존한다. 당시 raw artifact와 historical protocol을 현재 실험 의미에 맞춰 소급 변경하지 않는다.

## 주요 문서

처음 읽는 사람은 다음 순서를 권장한다.

- [0.1 명세](docs/JOURNEYMAP_0.1_SPEC.md): 기본 목표, 범위, 시나리오, 수용 기준
- [아키텍처](docs/ARCHITECTURE.md): Core/Module 경계와 의존성
- [API](docs/API.md): Game/Controller 및 Research/Debug 인터페이스
- [ERD](docs/ERD.md): 영속 데이터와 연구 기록
- [테스트 전략](docs/TEST_STRATEGY.md): 결정론, 권한, 정보 누출 검증
- [로드맵](docs/ROADMAP.md): M0-M8과 이후 연구 계보
- [M8 실험 프로토콜](docs/M8_EXPERIMENT.md): 첫 공식 LLM trial의 protocol
- [M8 공식 결과](docs/M8_FIRST_OFFICIAL_TRIAL.md): historical live trial 결과와 한계
- [Event Memory Phase 1](docs/EVENT_MEMORY_PHASE1.md): Event Trace와 최소 memory correctness
- [Recency Window](docs/EVENT_MEMORY_RECENCY_WINDOW.md): `k=1/2/3` correctness
- [Memory Horizon Offline](docs/MEMORY_HORIZON_OFFLINE.md): Distinct Places offline preparation
- [Memory Horizon v2](docs/MEMORY_HORIZON_V2.md): semantic Decision Context hardening
- [Memory Horizon Readiness Audit](docs/MEMORY_HORIZON_READINESS_AUDIT.md): actual pilot 전 adversarial audit
- [Memory Horizon Live Pilot Preflight](docs/MEMORY_HORIZON_LIVE_PREFLIGHT.md): 현재 technical preflight contract
- [연구 참고 문헌](docs/RESEARCH_REFERENCES.md): 선행 연구와 적용 질문
- [Codex 및 AI 작업 지침](AGENTS.md): repository 작업 전에 지켜야 할 규칙

구현 여부의 최종 기준은 GitHub `main`이다. 연구 질문과 실험 해석 범위는 코드 상태와 분리해 관리하며, 결과가 없는 상태에서 예상 효과를 실제 결과처럼 기록하지 않는다.

## 개발 환경

검증 기준은 Python 3.12다.

```text
python -m pip install -e ".[dev]"
python --version
ruff check .
ruff format --check .
mypy
pytest
git diff --check
git status
```

기능 또는 계약을 변경할 때는 관련 테스트와 문서를 같은 변경에서 갱신한다. milestone 범위를 바꾸는 변경은 `docs/ROADMAP.md`, 논리 계약은 `docs/API.md`, 상태와 저장 소유권은 `docs/ERD.md`, 계층과 의존 방향은 `docs/ARCHITECTURE.md`, 검증 규칙은 `docs/TEST_STRATEGY.md`에 반영한다.

## 실행 예제

기본 Alderwick 예제:

```text
python -m journeymap.examples.alderwick
```

Social 정보 전달 예제:

```text
python -m journeymap.examples.alderwick_social
```

Survival, Inventory, Trade 예제:

```text
python -m journeymap.examples.alderwick_resources
```

M8 offline trial, replay, audit:

```text
python -m journeymap.examples.alderwick_llm --trial-id offline-example --output trials/offline-example
python -m journeymap.examples.alderwick_llm --replay trials/offline-example
python -m journeymap.examples.alderwick_llm --audit trials/offline-example
```

`trials/`는 로컬 연구 산출물이며 Git에서 제외한다. 실제 외부 LLM/API 호출은 실험 조건과 기록 방식을 먼저 동결하고 사용자 명시 승인을 받은 경우에만 수행한다.
