# Phase 1: concurrent-v1 시간 실행 계약

2026-10-14. 공식 main `97a5b6a24079b1f3bf3afb45bac85d30c1468336`에서 분리한 본체 개발
첫 단계다. E03/E04 원본과 연구 질문을 보존하며 E05 완료를 선행조건으로 삼지 않는다.
겹치는 행동 구간과 최소 규칙 기반 재활성화의 구현이며 자율 생활 전체의 완성을 뜻하지 않는다.

## 실행 경계와 API

기존 `SimulationKernel.submit_action`, `GamePort`, `ControllerActionResult` v1,
`ReplayInput/ReplayHarness`, historical scenario/version은 기존 동기 의미를 유지한다.
새 `ConcurrentKernel`은 `create_kernel(kernel_type=ConcurrentKernel)`로 선택한다.
`submit_action`은 모드 혼용을 예외로 차단한다. 예제의 scenario는 `phase1/concurrent-1`이다.
`bootstrap_phase1`이 조립을, `scenarios.phase1`은 fixture와 환경 handler만 소유한다.

- `accept_action(request) -> Admission`: 현재 tick 요청만 접수한다. 시간 진행, 도메인 mutation,
  최종 성공 결과를 만들지 않는다. 모든 행동은 해당 actor에 대해 독점적이다.
- `advance_to(tick)`: 환경 입력과 행동 완료를 처리한다. 과거 tick/bool/비정수는 거부한다.
- `advance_next()`: 다음 환경 또는 완료 tick으로 진행하고, 없으면 `None`이다.
- `ConcurrentApplication.game_for(actor) -> ConcurrentGamePort`: observe/submit/result만 공개한다.
  Controller.decide에는 Observation 하나만 전달한다. 기존 Observation/claim authority를 재사용한다.
- `ConcurrentReceipt` schema 2: action_request_id, actor_id, status, reason_code, started_at,
  completes_at, resolved_at, schema_version. ACCEPTED의 resolved_at은 null이며 최종 성공과 다르다.
  `result(request_id)`는 해당 actor의 접수/최종 결과만 반환한다. world digest/handler/RNG/
  scheduler/타 actor 결과는 공개하지 않는다.

exact retry는 최초 접수/거부 receipt를 반환한다. 완료 여부는 result로 확인한다. 다른 요청 또는
다른 actor의 ID 재사용은 REQUEST_ID_CONFLICT다. 접수 중 engine 결함으로 결과가 불명확해진 ID는
재실행하지 않는다. application `attempts`는 normalized live denial/error/retry도 보존한다.
kernel command replay는 live authority 재구성이나 application archive replay와 구분한다.

## 같은 tick 순서와 경합

1. 환경 사건: 기존 scheduler `(due_time, priority, insertion_sequence)`와 SystemEventRegistry.
   모든 due 환경 사건이 행동 완료보다 먼저다.
2. 기존 행동 완료: 같은 완료 tick의 actor 집합을 ID 정렬한 뒤 `priority_cursor % cohort_size`
   위치부터 순환한다. batch 생성마다 cursor를 1 증가시키며 중단된 batch의 잔여 순서는 고정한다.
3. 새 판단/접수: trusted caller가 환경과 기존 완료를 drain한 뒤 제출한다. due-now 환경이나
   완료가 남으면 접수를 명시적으로 차단한다. 접수가 숨겨진 시간 진행을 수행하지 않는다.

untimed handler는 duration 0으로 접수하고 명시적 advance_to(current_tick)에서 완료한다.
그 완료를 drain하기 전 다른 새 접수는 차단한다. runner는 영시간 완료를 다음 접수 전에 drain한다.
고정 두 구매자의 연속 6회 동률 경합에서 A/B가 번갈아 이긴다. cohort 구성 변화나 다른 완료
batch가 끼는 모든 경우의 공정성·기아 방지를 증명하지 않았다.

## 이동 중 공간·정보와 완료 실패

InFlight는 원 ActionRequest, timing, start/finish tick, handler ID와 module-owned absence를
보유한다. 요청의 Observation/request/correlation 참조를 유지한다. Core는 MOVE/거래 도메인을
판정하지 않으며 선택적 `AbsenceHandler`의 공간 이탈 정보를 다룬다.

movement의 MoveHandler.absence가 route/origin/destination을 제공한다. 이동 중에는 어느
Location에도 현장 존재하지 않는 보수적 규칙이다. 도메인 position의 origin은 실패 복귀 기준으로
남지만 trusted perception_snapshot은 현재 absence를 더해 공간 query를 필터링한다.
state_snapshot/state_digest는 도메인 상태이며 digest 관계를 유지한다. 미래 schedule은
이 overlay나 Observation에 들어가지 않는다.

- 자기 Observation의 movement.position은 location 대신 transit의 route/origin/destination/
  started_at/completes_at을 표시한다. 진행 중인 자기 의도만 알려준다.
- 이동 중 신규 현장 감지 범위는 비어 있다. 기존 지식과 자기 자원은 유지한다.
- perceive_position은 이동 중 actor에게 빈 공간 존재를 반환한다. 기존 BUY/social 시작·완료
  검증, local offer, bridge visibility/witness가 함께 이 query를 사용한다.
- M8 local actor enumeration은 raw origin 대신 공간 query를 쓴다. 이동 viewer의 location
  없음끼리 일치시켜 다른 이동자를 노출하는 반례도 차단한다.
- busy actor의 추가 행동은 engine ACTOR_BUSY/REJECTED다. 시간·위치·재고를 바꾸지 않는다.
  현재 공개 receipt는 allowlist에 없는 이 진단을 ACTION_REJECTED로 가린다.

완료 때 기존 timed handler의 validate_completion을 최신 상태로 호출한다. 경로 폐쇄/endpoint
변경으로 MOVE가 FAILED이면 origin에 남고 전체 예정 duration을 소비한다. 독립 환경 사건은
유지하고 이동 성공 mutation/Event는 없다. 완료 tick 폐쇄도 환경 우선이므로 FAILED다.
실패 후 즉시 origin의 공간 존재로 복귀한다.

## 자원 경쟁과 mutation 경계

예약 대신 완료 시 경쟁을 선택했다. 기존 BuyHandler의 immutable start quote, 최신 재고/지갑/
위치 검증과 inventory+trade를 묶는 TransitionPlan을 재사용하는 최소 구현이다. 동시 접수는
획득 보장이 아니며 완료 순서상 한 명만 재고 1개를 얻는다. 실패 구매자는 차감하지 않는다.

actor completion과 system input은 별도 dispatch이고 기존 _commit_plan을 공유한다. 접수의
검증된 runtime bookkeeping은 동일 reentrancy guard 안에서 후보를 완전히 구성한 뒤 게시한다.
Controller는 이 runtime에도 직접 write하지 않는다. 예상 검증 실패는 REJECTED/FAILED이며
resolve/serialization 결함은 예외다. 해당 완료는 pending으로 남고 candidate world/RNG는
commit되지 않는다. 내부 결함에서 자동 재시도하지 말고 실행을 중단·조사해야 한다.

commit 뒤 EventBus delivery 실패는 이미 기록된 domain state/Event/result와 완료 제거를
보존한다. 같은 tick을 명시적으로 이어 처리해도 완료를 중복 적용하지 않는다. subscriber 재진입은
기존 ReentrantMutationError와 delivery-error 경계를 따른다.

## 반복 활성화와 기록/replay

AutonomousRunner.run_until(horizon)은 유한 정수 tick을 진행하며 idle actor를
observe→decide→bound submit으로 재활성화한다. actor당 한 tick 최대 한 판단이며 pending actor는
건너뛴다. 실패/거부/영시간/잘못된 Controller output 뒤 같은 tick에서 다시 호출하지 않는다.
horizon tick은 기존 사건/완료만 처리하고 새 판단은 하지 않는다. horizon 밖 pending 행동이
남을 수 있으며 실패/완료로 위조하지 않는다. split run은 readiness/cursor를 유지한다.

ExecutionRecord는 sequence/tick/kind/source_ref/correlation_id/status/reason을 보존한다.
accepted/rejected/completed/environment를 구별하고 completed status는 SUCCEEDED/FAILED를
구분한다. domain Event의 기존 transition/causation/source 계보를 유지한다. 미커밋 내부 결함은
완료 기록을 위조하지 않으며 caller 예외와 pending 상태로 조사한다.

kernel.commands는 schedule 설치, accept, explicit advance를 순서대로 기록한다.
등록되지 않은 action은 live boundary의 UNKNOWN_ACTION/attempt로 남고, 실행 효과가 없으므로
executable kernel command stream에 넣지 않는다. expected start rejection은 명령과 result로 남는다.
replay_concurrent(factory, commands)는 동일 초기 상태/seed/handler의 새 unbooted kernel에서
명령을 재실행하고 각 명령 뒤 execution_snapshot frame을 보존한다. 실제 pending 1명/2명과
tick 1 경로 폐쇄 frame을 검증하며 마지막 상태 복사가 아니다. fresh run, split/whole advance에서
world/pending/results/Events/journal/RNG/cursor가 일치한다. 임의 artifact tamper의 독립 audit
reader는 이번 범위에 없다.

## 저장 계약: 영속 save/restore 미구현

SQLite adapter나 JSON execution_snapshot을 전체 저장/재개 구현으로 부르지 않는다.
restore API와 application/runner checkpoint 직렬화는 미구현이다. 다음은 후속
concurrent-checkpoint-1의 구체적인 필요 schema와 검증 조건이다.

| 필드 | 저장할 값 및 검증 |
|---|---|
| version/identity | checkpoint schema, mode, full RunManifest, module/handler/scenario versions, initial source digest |
| world | canonical domain JSON/digest, 예약 namespace 침범 금지 |
| clock | current integer tick; pending completion/due는 현재 이상, interrupted batch는 현재 tick |
| run sequences | transition_sequence/event_sequence; log 마지막 identity/연속성 대조 |
| scheduler | pending ScheduledEvent 모든 필드, next insertion sequence; ordering key/ID 중복 거부 |
| in_flight | 원 ActionRequest 9필드, ActionTiming(duration/data), start/finish, handler ID, absence; actor 독점 검증 |
| request ledger | full fingerprint, bound actor, 최초 Admission/indeterminate 표식, terminal ActionResult; 완료 재적용 금지 |
| RNG/tie | SplitMix64 algorithm/version/state/draw_count, priority_cursor, frozen completion_batch 잔여 순서 |
| archive | Events/ActionResults/SystemEventOutcomes, journal/next sequence, ordered input commands |
| application | 초기 Knowledge/projection rule version, Observation 원문/history/next sequence, actor binding, receipt/error cache, live attempts |
| runner | controller ID/version/합법적 초기 설정 또는 policy state, next_tick per actor, activation cursor/history, horizon/resume 경계 |

복원할 때 handler.prepare를 다시 실행해 start quote/duration을 바꾸지 않는다. 저장 timing을
검증하고 완료 때 최신 상태를 재검증해야 한다. Knowledge projection은 committed Event prefix로
재구성해 저장 값과 대조한다. 임의 callable/closure/subscriber를 JSON에서 실행·자동 복원하지 않는다.

필수 gate는 tick 1에 두 이동이 pending인 checkpoint를 새 프로세스에 복원해 연속 실행과
Events/results/journal/clock/RNG/pending/tie 순서가 같은지, 완료 뒤 retry/conflict가 재차감하지
않는지, malformed version/queue/timing/sequence/binding을 fail-closed로 거부하는지,
Controller 활성화를 중복하지 않는지 검사하는 것이다. 이 gate는 실행하지 않았고 통과하지 않았다.

## 실행

```powershell
Set-Location 'C:\Users\user\Documents\JourneyMap\.worktrees\phase1'
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$pythonExe = 'C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe'
& $pythonExe -m journeymap.examples.concurrent
& $pythonExe -m pytest tests/test_concurrent.py -q --basetemp=artifacts/phase1/tmp-user-new
& $pythonExe -m pytest -q --basetemp=artifacts/phase1/tmp-user-full
& $pythonExe -m ruff check .
& $pythonExe -m ruff format --check .
& $pythonExe -m mypy
git diff --check
```

예제는 정상 이동, tick 1/2 폐쇄, 재고 경쟁과 autonomous patrol을 JSON으로 출력한다.
오프라인 correctness 증거이며 LLM 행동 효과·연구 construct validity의 증거가 아니다.
