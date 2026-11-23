# JourneyMap

이 저장소는 E03·E04 연구 엔진과 별도 [Actual 런타임](actual/README.md)을 제공합니다.
[오프라인 재현과 소스 계보](docs/RESEARCH_SOURCE_SYNC_2026-11-24.md)에서 설치, 검증, 동결 입력의 출처를 확인할 수 있습니다.
[MemoryReplica v1 결과](docs/MEMORYREPLICA_RESULTS_2026-11-23.md)는 2026년 11월 23일 완료된
120세계·5정책·600실행의 기록입니다.

수령처 안내 [연속 행동 진단](docs/EXPERIMENT03_PICKUP_SEQUENTIAL.md)은 별도 프로토콜로
첫 실제 도착 점수와 6회 이내 최종 도착·회복·추가 행동/tick 비용을 구분합니다.
이번 확장은 오프라인 구현 검증이며 기존 one-shot 및 실제 40회 결과는 그대로 보존합니다.

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

[E04](docs/EXPERIMENT04_OFFLINE.md)는 공식 경로 변경, 출처 기반 정보 획득, 변경 후 후속 행동을 통제 시나리오에서 구분한다.

```text
A. 필요한 새 정보를 획득하지 못함
B. 새 정보를 획득했지만 행동에 사용하지 못함
C. 새 정보를 사용했지만 판단 또는 계획이 잘못됨
D. 적절하게 계획을 수정했으나 Engine 결과가 달랐음
```

## 구현 및 검증 상태

Experiment 03의 추가 오프라인 범위로 직전 장소 기반 코드 기준선과 수령처 안내 진단
(2 target × 4 cue distance × 5 memory condition)을 구현했다. 별도 진단 평가,
Provider-free replay/입력 재구성/audit를 제공하며 기존 방문 지표와 live 계획은 유지한다.
정확한 계약과 실행법은 [기준선·진단 문서](docs/EXPERIMENT03_BASELINE_DIAGNOSTIC.md),
실행 증거는 [2026-10-04 검증 기록](docs/EXPERIMENT03_OFFLINE_REPORT_2026-10-04.md)을 따른다.
fixture 결과는 실제 LLM 효과의 증거가 아니다.

수령처 진단 전용 실제 LLM 연결 준비 경로를 추가했습니다. 계획 동결, 명시적 live gate,
HTTP body 기록, 중단 journal과 Provider 없는 audit를 오프라인으로 검증합니다.
[연결 준비 계약·실행 명령](docs/EXPERIMENT03_LIVE_PREPARATION.md)과
[2026-10-05 완료 보고](docs/EXPERIMENT03_LIVE_PREPARATION_REPORT_2026-10-05.md)를 참고해 주세요.
실제 API 실행과 최종 모델·예산 확정은 아직 하지 않았습니다.

검토 F1의 응답 없는 mock 실패 기록 재분류 문제를 수정했습니다. 계획 v2는 실행 mode를 digest에
묶고 runner/journal/export를 대조합니다. v1 원본은 읽기 전용으로 보존하며 v2 mode 보증으로
승격하지 않습니다. [수정 검증 보고](docs/EXPERIMENT03_LIVE_MODE_FIX_REPORT_2026-10-05.md)를 참고해 주세요.

M0부터 M8까지 deterministic research baseline을 구축한 뒤 Event Memory 연구 계층을 추가했다.

| 단계 | 상태 | 의미 |
| --- | --- | --- |
| M0-M8 baseline | 완료 | deterministic kernel, movement, perception/knowledge, social, survival/trade, action contracts, LLM adapter, 첫 공식 24h trial |
| Baseline Validation & Hardening | 완료 | provenance, inclusion, replay, provider identity, historical compatibility 검증 |
| Experiment 01 Event Memory Foundation | 완료 | Event Trace v1, No Event Memory, Recency `k=1` correctness |
| Experiment 02 Recency Window | 완료 | Recency `k=1/2/3` retrieval correctness와 backward compatibility |
| Experiment 03 Memory Horizon v2 | 완료 | semantic Decision Context로 progress/provenance cue를 model-visible input에서 제거 |
| Live Pilot Preflight | 완료 | 별도 live protocol/audit, 12-allocation cohort, Horizon wire schema, fresh runtime ownership, recording mock transport, journal/audit 경계 |
| Distinct Places 본 평가 | 별도 연구 gate | 수령처 one-shot 및 연속 행동 진단은 별도 과제 |
| E04 Information Change / Replanning | 구현 및 제한 검증 | fake·recording mock, 저장 결과 감사, 승인된 제한 smoke·pilot 기록 |
| Actual | Phase 4.4 및 후속 | 다중 에이전트 생활 시뮬레이션, 정산·재생·continuation |
| MemoryReplica v1 | 완료 | 동결된 두 평가와 오프라인 실행·채점 코드 |

중요하게, **기술적 preflight 완료와 behavioral effect 입증은 다른 상태**다.

기존 Memory Horizon Live Pilot Preflight는 recording mock만 사용하는 기술 검증이다. 해당 경로에는 실제 network/API 호출, real OpenAI Provider, live-capable CLI, automatic resume는 포함하지 않는다. 자세한 범위는 [Memory Horizon Live Pilot Preflight](docs/MEMORY_HORIZON_LIVE_PREFLIGHT.md)를 따른다.

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

과거 보고서는 해당 시점의 구현과 실행 프로토콜을 기록한다. 최신 설치 및 검증 범위는 [오프라인 재현과 소스 계보](docs/RESEARCH_SOURCE_SYNC_2026-11-24.md)를 따른다.

## 개발 환경

검증 기준은 Python 3.12다.

```text
python -m pip install -e ".[dev]"
python --version
ruff check src tests
ruff format --check src tests
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

## Experiment 04 오프라인 검토

공식 경로 변경 정보 실험의 독립 시나리오·첫 이동 기준선·전체 경험 실행기와 무호출 재구성 audit를 추가했다. [구현 계약](docs/EXPERIMENT04_OFFLINE.md)과 [완료 보고서](docs/EXPERIMENT04_OFFLINE_REPORT_2026-10-08.md)를 참조한다. fake correctness 단계이며 기존 03 결과와 합산하지 않는다.

## Experiment 04 목표 계약 보완 (2026-10-09)

후속 tick 3 이후에는 받은 정보에 따른 남은 이동 시간 최소화를 명시하는 v2 profile/protocol을 적용한다. 초기 선택은 기존 도착 목표를 유지한다. 실제 세계의 시간 최적 첫 이동과 최종 도착은 별도 지표다. v1 원본의 감사 가능성은 유지한다. [수정·검증 보고](docs/EXPERIMENT04_GOAL_REVIEW_REPORT_2026-10-09.md).

## Experiment 04 live 준비 gate (2026-10-09)

[E04 live 준비 계약](docs/EXPERIMENT04_LIVE_PREPARATION.md)은 v2와 기존 원본을 유지하면서
별도 E04 Responses wire, 6prefix 균형 순서와 사전 입력 경계 검사, 비용 예약, 원문 재생 audit를 제공한다.
이번 단계는 오프라인 mock 구현 검증이다. 실제 API 0회, 키 조회 없음. USD10은 PROPOSED다.
모델별 tokenizer 및 schema overhead의 신뢰 가능한 사전 상한 근거가 없어 live는 DRAFT이며
수동 READY 변경/승인 JSON으로 우회할 수 없다. 기존 offline 실행기와 감사 계약은 유지한다.


E04 live 준비 후속 수정(2026-10-09): count 전용 protocol v2와 tick-3 두 코드 기준선을 오프라인 검증한다. 기본 live 후보는 count 요금 UNKNOWN / 서비스 UNVERIFIED / DRAFT·UNAUTHORIZED다. [후속 계약](docs/EXPERIMENT04_LIVE_PREPARATION.md) 및 [완료 보고서](docs/EXPERIMENT04_LIVE_FINDINGS_REPORT_2026-10-09.md)를 참조한다.


E04 제한된 smoke 준비(2026-10-10): 명시적 `--smoke-count-fee-unknown` 옵션은 v3 `SMOKE_COUNT_FEE_UNKNOWN_V1` 계획을 만든다. 계수 1·생성 1·HTTP 2의 별도 smoke에 한하며, 계수 요금 UNKNOWN과 총액 상한 미확정은 유지한다. 준비된 후보와 PENDING 승인안은 실제 발송 승인이 아니다. 기본 v2/pilot의 확정 계수 단가 요구는 유지한다. [계약](docs/EXPERIMENT04_LIVE_PREPARATION.md) 및 [완료 보고서](docs/EXPERIMENT04_LIMITED_SMOKE_REPORT_2026-10-10.md).

E04 별도 제한 pilot v4 준비(2026-10-10): `PILOT_COUNT_FEE_UNKNOWN_V1`은 새 초기6회와 생성114/count114/network228에 한정한다. 실제 v3 smoke와 전체 source·입력·후속 의미 호환성을 검증하고 서비스 증거/가격UNKNOWN/최종승인PENDING을 분리한다. 생성 예약 USD8.03472는 총액 상한이 아니다. 준비는 실제 호출 승인이 아니다. [계약](docs/EXPERIMENT04_LIMITED_PILOT_PREPARATION.md).
## MemoryReplica stable B offline integration (2026-10-30)

현재 E04 runtime에 별도 MemoryReplica 입력·재구성/행동 runner와 안정 환경 B 과제를 추가했습니다.
초기 지도는 모든 기억 조건에서 제외하며, 준비 세계·경험을 고정하고 none/recent3/full만 비교합니다.
[계약과 실행](docs/MEMORYREPLICA_OFFLINE.md), [검증 결과](docs/MEMORYREPLICA_REPORT_2026-10-30.md)를 따릅니다.
이는 입력 근거와 scripted Engine correctness 검증이며 실제 LLM 행동 효과의 증거가 아닙니다.
