# Phase 2 — 소규모 자율 생활 순환

별도 `life/life-loop-1` 시나리오다. Phase 1의 미커밋 concurrent-v1 구현을 이어 사용하며
기존 동기 API/E03/E04의 시간·정보·replay 의미를 바꾸지 않는다. Core는 생활 도메인을 소유하지 않는다.

## 시간과 초기 조건

1 tick=가상 1시간, 하루=24tick, 기본 horizon=168tick(7일). 인물은 고용주 1명과
노동자 Ada/Bert/Cora/Dara 4명이다. 장소는 공동 주거지, 광장, 작업장 겸 판매소, 우회로다.
고용주는 작업장 위 거처에서 생활하며 자기 식량/욕구/휴식 정책이 있다. 노동자가 제출한 작업의
생산·급여 규칙은 자동 사업장 처리이며 고용주의 행동으로 위조하지 않는다.

| 초기 값/계약 | 기본값 |
|---|---|
| seed | 42 |
| 지갑 | 고용주 240, 노동자 각 4, 총 256 |
| 재고 | 고용주 flour 160/bread 8, 노동자 둘 다 0 |
| 초기 욕구 | 모두 hunger 20/fatigue 10; 높은 값이 나쁜 기존 방향 유지 |
| 소비 효과 | bread 1개당 hunger -10, 0에서 제한 |
| 작업 | 3시간, 동시에 2자리, 명시적 고용 관계의 노동자만 가능 |
| 변환/임금 | 고용주의 flour 1배치 → bread 8개, 고용주→노동자 화폐 10 |
| 노동 피로 | 완료 시 fatigue +4(최대100), 시간 경과 피로와 별도 |
| 구매 | 기존 BUY 1시간, bread 개당2, 한 번 최대5개를 자기 지갑 범위에서 정책이 선택 |
| 휴식 | 자기 거처에서 기존 REST를 감싸 사용; duration×3 회복 |

flour 1단위는 제빵 원료 배치이며 빵 1개와 같은 단위가 아니다. 초기 재고/원료는 유한하고
추가 조달·자동 보충·보조금·화폐 발행·폐기는 없다. 고용주의 소비도 판매 재고에서 차감한다.

첫 개발 실행의 임금6/출력4/구매최대3은 15시간분 식량으로 이동·노동·휴식을 감당하지 못했고
노동자 배고픔 상한이 관측됐다. 이를 정상으로 집계하지 않고 위 계약(임금10/출력8/구매최대5)으로
명시적으로 조정했다. 초기 원료160/식량8/지갑240+4×4는 유지했다. 초기 실패 요약은
artifacts/phase2/normal-initial에 보존하며 최종 정상/부족 조건을 모두 다시 실행한다.
이는 7일 기능 검증의 실행 가능한 설정이며 장기 경제·영양 균형을 입증하지 않는다.

## 욕구와 동일 tick 순서

survival 모듈의 `needs_schedule(0, horizon)`이 1..168의 유한 SurvivalTick 입력을 만든다.
기존 handler로 모든 인물에게 매 tick hunger +2/fatigue +1(최대100)을 한 번 적용한다.
관찰·정책 호출 수나 actor 수에 따라 같은 인물의 갱신 횟수가 늘지 않는다. 이동/작업/휴식 중에도
동일하게 갱신한다. 기존 Alderwick 1..20/연구 24h schedule은 변경하지 않는다.

같은 tick은 환경 사건(폐쇄 priority0 → 욕구 priority10), 기존 행동 완료(Phase 1 순환 순서),
새 판단/접수 순서다. REST는 최신 피로에서 duration×3만 차감하므로 경과 피로가 중복 제거되지 않는다.
예: 시작 피로50, 3시간 휴식이면 50+3−9=44. WORK도 최신 욕구를 재검증한 뒤 피로4를 추가한다.

hunger≥90 또는 fatigue≥80이면 WORK 불가다. 소비·구매·이동·휴식·대기는 가능하고 정상 정책은
피로55에서 휴식으로 전환한다. 사망/건강 모델은 없다. `risk_ticks`는 완료 처리 후 정수 tick의
상태가 위험한 [tick,tick+1) 구간 수이며 horizon 뒤를 세지 않는다. risk_intervals는 실제 구간을
start/end_exclusive로 표시한다. final/max 욕구와 미완료 행동을 별도 보고하며 위험을 정상 생활로
집계하지 않는다. 환경 사건 내부의 before/after 값은 Event 원문에도 유지된다.

## 작업 접수·완료와 자원 경쟁

`WORK/1 {job_id: nonempty string}`을 LaborModule의 ActionRegistry에 등록한다.
payload 추가 필드는 거부한다. 기존 ConcurrentKernel lifecycle과 _commit_plan을 사용한다.
기존 동기 turn helper의 고정 v1 preflight 목록에 WORK를 소급 추가하지 않는다.

시작 때 고용 관계/현장 위치/욕구/원료/임금을 검증하고 Job 전체 견적과 duration을 timing에 고정한다.
자리 수는 trusted runtime의 generic active_actions로 계산하며 동시 진행 중 WORK는 최대 capacity다.
Core는 job/material/wage를 해석하지 않으며 이 metadata는 Observation에 직접 전달하지 않는다.

원료와 임금은 예약하지 않고 완료 시 경쟁한다. 최신 자원/위치/욕구/고정 계약/자리 조건을 재검증하고,
inventory 변환 + trade 임금 이전 + survival 작업 피로 + WorkCompleted를 한 TransitionPlan에 넣는다.
두 노동자가 마지막 원료/임금 재원을 보고 시작했다면 순환 완료 순서의 한 명만 성공할 수 있다.
다른 사람은 3시간을 소비한 FAILED이며 별도 실패 노동 보상은 없다. 원료만 줄거나 임금만 나가거나
생산물만 생기는 부분 적용은 없다. 작업 중/완료 뒤 exact retry는 접수 receipt를 반환하고
`result`로 완료를 읽으며 생산·임금을 다시 적용하지 않는다.

고용주의 private 자원 진단은 engine에서 EMPLOYER_ 접두어로 Research에 보존한다.
공개 결과는 기존 allowlist 밖이라 ACTION_REJECTED/ACTION_FAILED다. BUY의 자기 자금 부족 진단을
WORK의 고용주 자금에 그대로 공개하지 않는다. local job availability는 게시된 현재 기회 여부만
알리며 고용주의 정확한 지갑/원료를 알려주지 않는다.

## 인지 경계와 생활 정책

초기 지도는 각 actor의 기존 KnowledgeLedger에 INITIAL KnowledgeRecord로 제공한다.
canonical World Truth의 현재 route 상태와 분리하며 초기 topology/cost를 현재 passability로
갱신해 전달하지 않는다. 현재 자기 역할/거처/알려진 일자리·판매 장소는 명시된 초기 지시다.

LifeController에는 Observation만 전달한다. 입력은 자기 inventory/wallet/hunger/fatigue,
actor-owned 초기 지도, 자기 장소의 routes/jobs/offers, 자기 이전 sanitized 결과다. 원격 재고,
타인 지갑, 미래 폐쇄, scheduler/RNG/Research handle을 전달하지 않는다. 로컬 판매 listing의 존재는
재고 보장이 아니다. 현장 방문 후 BUY 실패로 품절을 발견한다. remote job 목록 없음도 일자리 소멸을
뜻하지 않는다. 이동 중에는 local routes/jobs/offers가 비고 현장 노동/휴식이 불가하지만 욕구는 흐른다.

정책은 시각별 정답 배열이 아니라 다음 우선순위를 사용한다.

1. 배고픔30 이상이고 자기 bread가 있으면 최대2개 소비한다.
2. 피로55 이상이면 거처로 이동해 휴식한다.
3. 고용주는 자기 욕구를 처리한 뒤 사업장을 열어두는 WAIT/REST를 선택한다.
4. 노동자는 식량≤1, 지갑≥가격, 필요 조건이면 알려진 판매소로 가서 최대5개를 살 수 있는 만큼 구매한다.
5. 지갑<10 또는 식량0이면 알려진 작업장으로 가서 local availability가 있을 때 WORK한다.
6. 피로가20 이상이면 거처에서 회복하고, 대안이 없으면 2시간 WAIT/재확인한다.

BUY/WORK 실패 뒤 4tick 재시도 지연이 있고, WORK 성공 후 구매 지연을 해제한다.
관찰한 폐쇄와 MOVE/ROUTE_IMPASSABLE 결과만 알려진 지도에 반영해 최소비용 우회로를 고른다.
대안이 없으면 WAIT하고 현장을 재확인한다. 관찰하지 않은 원격 변경은 입력과 판단을 바꾸지 않는다.
각 판단은 실제 action/payload, 정책 reason, 사용한 자기/공개 근거와 initial map record ID를 기록한다.
이는 규칙 정책 기록이며 LLM의 사고나 추론 기록이 아니다.

## 기록과 두 종류의 재현

`LifeSession`은 기존 concurrent engine을 조립·진행하는 application driver다. world 복제/병합이나
독립 엔진이 없다. 하루별 상태/완료 행동/실패, 인물별 임금·작업·구매·소비·휴식, 자원 흐름과
실제 tick의 두 생활 구간을 사람이 읽는 출력과 JSON/JSONL로 제공한다.

- engine replay: schedule/accept/advance 명령으로 domain world/results/Events/journal/pending/RNG를
  재실행한다. Phase 1의 frame capture 기본값은 유지하고 긴 생활 검증만 capture_frames=False로
  마지막 실행 상태를 비교한다. Phase 1의 실제 중간 frame 검사는 계속 유지한다.
- live-input replay: observe/submit/schedule/advance를 같은 권한 경계에서 재실행한다.
  미등록 행동·권한 거부·retry/conflict/busy 등 실행 효과 없는 attempt도 포함하며 실제 Observation
  ID/content digest, 공개 receipt, 전체 attempts/observations/최종 실행 상태를 비교한다.
- Controller 재실행: fresh 정책을 같은 seed/config에서 실행하고 73/168 분할 진행한 경우까지
  정책 판단·환경 Events·최종 실행 상태와 일별 결과를 비교한다. replay 자체는 Controller를 호출하지 않는다.

wall-clock 성능 metadata는 deterministic 비교 대상이 아니다. horizon을 넘는 작업은 pending으로
보고하며 결과를 맞추려고 밖까지 진행하지 않는다. 영속 export는 실행 기록 쓰기이며 save/restore가 아니다.

## 회계와 부족 조건

모든 실행에서 currency_initial=currency_final,
flour_initial−production_input=flour_final,
bread_initial+production−consumption=bread_final을 확인한다. BUY/임금은 이전이고 생산은 변환이며
소비는 sink다. 서로 다른 품목의 개수 합을 보존량으로 사용하지 않는다. resource_flows는 실제
Event prefix를 접어 tick별 지갑/식량/원료 잔액을 보여준다.

별도 case는 no-wages(고용주0, 노동자4), no-materials(flour0), stockout(bread0),
no-money(노동자0, 고용주240), closure(tick2 square→shop 폐쇄)다. 원료 소진/임금 부족의
정체·굶주림·편중을 숨기지 않는다. 정상 조건의 작업7~8회 분포는 이 설정의 관측이며 일반적인
공정성/기아 방지 증명이 아니다. 부족 조건에서 특정 노동자에게 노동·식량이 편중될 수 있다.

## 후속 저장 schema

영속 저장/복원은 미구현이다. Phase 1 checkpoint schema에 Job/recipe/고용 관계/version,
고정 작업 timing과 이미 점유한 작업 자리, 완료 시 경쟁 정책, survival rest_locations/다음 욕구 tick과
전체 유한 schedule/삽입 sequence를 추가해야 한다. 현재 구현에는 원료·임금 예약 ledger가 없으며
예약을 도입하면 owner/item/금액/request ID와 release 규칙도 저장해야 한다.

INITIAL KnowledgeLedger/map provenance, ConcurrentApplication intent/receipt/error/attempt cache,
LifeController blocked routes/seen receipt/buy·work retry 시각, LifeSession activation cursor/
sampled tick/위험 구간/기록 cursor를 복원해야 한다. seed만으로 진행 중 상태를 재개할 수 없다.
기존 중복 방지·RNG·Event/transition sequence·미완료 행동 검증 조건도 유지한다.

## 실행

```powershell
Set-Location 'C:\Users\user\Documents\JourneyMap\.worktrees\phase1'
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$pythonExe = 'C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe'
& $pythonExe -m journeymap.examples.life --verify-replay
& $pythonExe -m journeymap.examples.life --case stockout --output artifacts/phase2/user-stockout
& $pythonExe -m journeymap.examples.life --case closure
& $pythonExe -m pytest tests/test_life.py tests/test_concurrent.py -q --basetemp=artifacts/phase2/user-tests
```

UI/생산 조달/직업 확장/사회 관계/LLM/기억 선택/영속 복원은 추가하지 않았다.
