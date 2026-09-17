# Event Memory Phase 1

Phase 1은 Event Trace v1 + Recency-based Event Memory k=1의 offline correctness 단계다.
성능 비교, scoring, embedding, reflection, RL, admission/eviction/decay는 포함하지 않는다.
기존 M8 protocol `alderwick-24h-2`와 official artifact의 의미는 유지한다.

후속 k=2/3 검증은 [별도 recency-window protocol](EVENT_MEMORY_RECENCY_WINDOW.md)을 따른다.
기존 Phase 1의 k=1 의미와 identity는 유지한다.

## 세 종류의 기록

- Research records: 모든 성공한 Observation, Provider/parser attempt, failure,
  valid request, raw engine result, incomplete turn을 기존 파일에 보존한다.
- Event Trace archive: engine에 실제 제출된 valid ActionRequest에 대해 actor-visible
  receipt가 반환된 완결된 경험만 기록한다. raw World Truth로 receipt를 추측하지 않는다.
- Agent Event Memory: 현재 decision에 policy가 선택한 archive의 부분집합이다.
  NoMemory도 archive를 남기지만 Provider에는 항상 빈 `event_memory`만 전달한다.

Current Observation에 포함된 Agent Knowledge와 `application/last_receipt`는 기존
perception 입력이다. No Event Memory가 이것을 삭제하거나 숨긴다는 의미는 아니다.

## Event Trace v1 schema

`adapters.event_memory.EventTrace.to_json()`이 정확한 wire 형식이다.

| Field | 의미 |
|---|---|
| `schema_version` | 1 |
| `event_trace_id` | `<run>:<actor>:event-trace:<8-digit sequence>` |
| `sequence` | actor/run archive의 1부터 증가하는 closure 순서 |
| `run_id`, `actor_id` | 고정 session scope |
| `decision_opportunity_id` | `<run>:<actor>:opportunity:<8-digit sequence>` |
| `opportunity_sequence`, `attempt` | opportunity 순서와 그 안의 Provider/parser 시도 번호 |
| `observation` | 제출을 이끈 actor-visible Observation v1 immutable snapshot |
| `action_request` | 그 Observation에 binding된 자신의 valid ActionRequest v1 |
| `actor_visible_receipt` | 기존 ControllerActionResult의 8개 공개 필드만 |
| `opened_at`, `closed_at` | Observation simulation tick / receipt.resolved_at |
| `memory_eligible` | true; archive에는 CLOSED 기록만 존재 |

Python record는 frozen이며 request를 canonical JSON으로 저장하고 접근 때 복원한다.
Observation의 JSON 접근도 복사본이다. mutable payload를 바꿔 archive를 오염시킬 수 없다.
World digest, raw ActionResult/transition, scheduler queue, NPC private state,
Provider metadata는 Event Trace에 넣지 않는다. 이는 trusted Python capability 계약이다.

## Opening, closure, retrieval

`LLMController(event_memory=...)`는 decision 시작에 actor/run archive에서 opportunity를
연다. 완료되지 않은 상태에서 같은 tick/content로 재시도하면 같은 opportunity ID와
증가한 attempt를 사용한다. Observation ID는 각 실제 observe에 따라 달라도 된다.
tick 또는 인지 내용이 바뀌면 새 opportunity다. 실제 receipt로 닫힌 경험 뒤의 decision도
새 opportunity다. digest는 변화 감지에만 사용하며 identity/dedup key가 아니다.

`run_controller_turn()`과 Core Controller/ControllerActionResult는 변경하지 않는다.
`run_trial()`이 반환 turn과 이번 호출의 `ActionTrace.engine_submitted`를 확인한 뒤
`controller.close_event_trace(observation, request, receipt, engine_submitted=True)`를 호출한다. 이 ingestion
API를 직접 사용하는 trusted caller도 실제 engine 제출과 공개 receipt 출처를 보장해야 한다.
`engine_submitted`는 생략할 수 없는 keyword이며 정확한 bool true만 허용한다. receipt의
ID/tick/version/status/reason을 검증하여 mutable metadata, 미등록 private diagnostic,
status/reason 모순을 거부한다. 명시적 확인은 caller 실수를 차단하는 계약이며 악성 Python의
거짓 확인을 인증하는 장치는 아니다. raw ActionTrace/World result를 Controller로 넘기지 않는다.
Controller/Provider는 ResearchView, GamePort, kernel 또는 raw result를 받지 않는다.

SUCCEEDED, REJECTED, engine-level FAILED 모두 위 조건을 만족하면 닫힌다. Provider transport/
identity failure, parser failure, refusal/incomplete, invalid Controller output은 닫지 않는다.
horizon/wall/timing precheck로 제출하지 않은 request와 공개 receipt 없는 submission error도
닫지 않는다. post-commit delivery failure의 hidden 성공 result를 경험으로 자동 승격하지 않는다.
동일 request와 receipt의 exact ingestion retry는 기존 trace를 반환하며 conflict는 거부한다.

Recency k=1은 현재 opportunity 이전에 닫힌 동일 actor/run trace를 closure sequence로
검증한 뒤 마지막 하나를 반환한다. 같은 tick의 이전 closure도 허용한다. 현재 Observation
sequence와 opportunity보다 이전이어야 하므로 자기 decision의 trace는 검색할 수 없다.
한 Controller/archive는 한 actor/run session만 처리하며 scope 변경과 역행은 실패한다.

## Compatibility and provenance

기존 `memory=MemoryPolicy`는 historical Observation-history 확장 경로로 격리한다.
`event_memory=NoMemory()` 또는 `event_memory=RecencyEventMemory()`가 새 opt-in이다.
두 인자를 동시에 사용할 수 없다. 기존 NoMemory는 local Observation history를 쌓지 않아도
같은 prompt를 만들며 모든 Research Observation은 그대로 남는다.

- Protocol: `alderwick-event-memory-phase1-1` (기존 runner에 명시적으로 전달).
- Prompt: `alderwick-event-memory-decision-1`, INPUT_JSON은 `observation`, `event_memory`.
- Policy: `No Event Memory` / `no-event-memory-1`, 또는
  `Recency-based Event Memory` / `recency-event-memory-k1-1`.
- Export schema 3: 기존 research 파일 + `event_traces.jsonl`. kernel/replay/state schema는 그대로다.
- Decision provenance: opportunity/attempt, policy identity/version, retrieved IDs/count,
  `serialized_event_memory`, canonical UTF-8 `event_memory_bytes`, 전체 prompt SHA-256,
  `closed_event_trace_id`. `current_observation_content_bytes`는 기존 Observation content
  budget과 같은 범위이며 Event Memory bytes와 별개다. 빈 배열도 직렬화상 2 bytes다.

`assess_event_memory()`는 Provider 호출 없이 decision/Observation/live attempt에서 archive,
lineage, retrieval, prompt/byte/hash를 재구성한다. `audit_export()`는 파일 inventory와 seal,
그 재구성, 기존 engine replay, stored 판정을 확인한다. schema 3의 INCLUDED는
`event-memory-phase1-correctness-1`에 따른 기록 정확성이다. 실패/중단 trial도 정확히
기록되면 INCLUDED일 수 있으며 성능 비교나 M8 hardening admission을 뜻하지 않는다.
schema 1/2 reader와 hardening `assess_inclusion()` 의미는 유지한다.

## Offline 실행

새 entry point에는 `--live` 옵션이 없다. 두 명령 모두 deterministic fixture만 사용한다.
기존 디렉터리는 덮어쓰지 않으므로 재실행할 때 새 output/trial ID를 지정한다.

```powershell
python -m journeymap.examples.event_memory --memory no-memory --trial-id phase1-none --output trials/phase1-none
python -m journeymap.examples.event_memory --memory recency-k1 --trial-id phase1-recency --output trials/phase1-recency
```

## 범위와 한계

단일 동기 actor session이며 persistence resume/동시 decision은 지원하지 않는다.
archive는 전체 경험을 보존하고 retrieval만 k=1로 제한한다. 큰 archive용 indexing이나
별도 memory token budget/optimizer는 구현하지 않는다. direct `decide()`만 호출해서는
경험이 닫히지 않으며 trusted caller의 receipt ingestion이 필요하다. CLI는 fake만 사용한다.
실제 모델의 행동 개선, 비용, Selective Memory의 타당성은 이번 검증에서 결론내리지 않는다.

## 2026-09-14 검증 결과

기준 main `dcbc0efe6f1ae24e00e2aa1147c240cee16bbb74`에서 clean 상태를 확인하고
`event-memory-phase1` branch를 생성했다. commit/push/PR과 실제 OpenAI API 호출은 하지 않았다.

- 전체 pytest: 879 passed in 47.86s (기존 823 + 신규 56).
- Ruff check/format: 통과, 125 files. strict mypy: 113 source files 통과.
- No Event Memory: 12 decisions/12 closed traces, retrieval count는 전부 0.
- Recency k=1: 12 decisions/12 closed traces, retrieval count는 0 이후 전부 1.
  첫 세 retrieval은 `[]`, `[E1]`, `[E2]`이며 현재 trace를 포함하지 않는다.
- 두 조건 모두 tick 24 COMPLETED, Provider-free engine replay equality와 schema 3
  export audit INCLUDED. 최종 로컬 출력은 `trials/phase1-none-final-20260914`와
  `trials/phase1-recency-final-20260914`다.
- 기존 `pre-experiment-hardening-20260914`: schema 2 replay equality, audit INCLUDED.
- 기존 `m8-first-official-sol`: schema 1 replay equality. 기존 audit 정책의
  NEW_PROVENANCE_REQUIRED는 그대로다. 두 historical 디렉터리의 각 17개 파일 hash가 불변이다.

이 결과는 다음 단계의 설계를 시작할 Phase 1 correctness 근거로 충분하다.
실제 모델 성능이나 후속 memory policy의 타당성을 검증한 결과는 아니다.

## 2026-09-15 최종 review

direct receipt ingestion의 제출 확인을 필수 keyword로 바꾸고 공개 receipt의 타입,
허용된 reason vocabulary, status/reason 일관성을 검증했다. 거부된 ingestion은 archive를
바꾸지 않는다. schema 3 audit의 malformed request 정규화 오류도 EXCLUDED로 처리한다.
회귀 15개를 추가하여 전체 **894 passed in 47.66s**, Ruff check/format(125 files),
mypy(113 source files), diff check를 통과했다. 기존 879개 테스트는 모두 유지한다.
`phase1-review-none-20260915`, `phase1-review-recency-20260915` offline export는 각각
12 traces/tick 24와 replay/audit 통과를 확인했다. historical 두 trial의 각 17개 파일은 불변이다.
