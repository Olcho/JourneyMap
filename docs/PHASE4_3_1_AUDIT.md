# Phase 4.3.1 · 저장 보고서 감사 완전성

2026-10-15. Phase4.3 독립 검토의 삭제·회계 변조 반례를 수정한다.
실제 승인 파일 생성, key 조회, API 호출, 유료 실행은 포함하지 않는다.
HTTP 목적지/schema/plan, 생활 엔진, 기본 RULE/offline/UI와 기존 승인은 변경하지 않는다.

## 보고서와 snapshot 선언

새 export는 `report_schema=life-paid-report-2`를 요구한다. controller.audit_snapshot은
entry_count/tail_digest/run_status/call_count/worker_in_flight/owner_finished를 담는다.
RunJournal.snapshot은 같은 lock에서 hash 검증된 rows와 DB status를 읽는다.
종료 marker와 DB status 갱신도 같은 lock으로 묶어 그 사이의 snapshot을 만들지 않는다.
signature나 외부 진본 증명은 아니며, 선언과 내부 기록의 일관성을 검증하는 경계다.

RUN_CLAIMED와 binding/approval ID, 연속 ordinal/hash chain, 선언한 count/tail을 검사한다.
CLAIMED snapshot은 종료 행이 없어야 하고 FINISHED/UNCERTAIN은 대응하는 종료 행 하나가
필요하다. RUN_UNCERTAIN 이후 worker의 late response/usage와 owner packet 회수는 허용한다.
RUN_FINISHED를 항상 마지막 행이라고 가정하거나 모든 call에 raw가 있다고 가정하지 않는다.
종료 뒤 새 decision/분류/행동 접수·완료는 허용하지 않는다.

`audit_verified=true`와 `journal_checked=true`는 선언한 snapshot의 전체 대조가 성공했다는 뜻이다.
snapshot_state를 CLAIMED/FINISHED/UNCERTAIN으로 구분한다. outcome_certain은 FINISHED이고
미회수 worker와 예약이 없을 때만 true다. 이는 실제 청구서 인증이나 행동 성과 성공을 뜻하지 않는다.
일관된 OPEN/UNCERTAIN snapshot은 검증할 수 있지만 깨끗하게 종료된 실행으로 승격하지 않는다.
불완전/모순 보고서는 ValueError, CLI nonzero이며 engine_equal만으로 audit 성공을 표시하지 않는다.

## 양방향 연결과 lifecycle

PaidDecisionService는 인물의 decision을 worker에 넘기기 전에 DECISION_STARTED를 남긴다.
sequence/Observation ID/request digest를 기록하며 credential이나 body 원문을 추가하지 않는다.
Call 목록과 replay 엔진이 재생한 모든 Ada Observation의 순서·집합이 같아야 한다.
Ada submit과 call.action도 양방향으로 같아야 한다. calls 배열이 비어 있는 초기 상태는
Ada Observation/submit도 0일 때만 허용한다. 실제 12회 실행에서 calls를 지우면 거부한다.

호출별 DECISION/PREPARED/ATTEMPTED/envelope/raw/usage/price/delivery/owner packet/
classification/receipt/completion의 필요 단계·선후·중복·여분과 ID 결합을 확인한다.
원문은 envelope byte 수/hash 및 call.raw와 대조한다. candidate와 trusted actor binding,
engine receipt와 모든 실제 terminal completion을 재검증한다. 완료 기록을 call과 journal에서
함께 지워도 실제 엔진 결과와 맞지 않으면 실패한다. horizon의 진행 중 행동은 completion을 요구하지 않는다.

READY는 received/owner packet을 갖지만 classification/접수가 없는 상태로 검증한다.
NOT_SENT, HTTP/usage/identity 오류, 미회수 worker, owner timeout과 late 폐기를 구분한다.
late 호출에 action/candidate/receipt/completion을 추가할 수 없다.
부족한 reservation 때문에 전송 전에 중단된 경우 RESERVATION_BLOCKED를 남겨,
call dispatch 수와 실제 reservation/attempt 수가 다른 합법적인 한도 정지도 감사할 수 있다.

## 회계 재계산

새 paid_audit는 transport를 만들지 않고 journal을 순서대로 읽어 별도 accounting 상태를 만든다.
초기값은 0/NO_USAGE다. PREPARED에서 같은 고정 plan의 입력 context/출력 예약과 누적
한도를 확인하고, reported usage를 정수·합·부분집합 검사 뒤 별도로 누적한다.
model/tier가 맞고 예약 안에 있는 usage만 단가를 적용해 정산한다. cached input을 분리하고,
output에 포함된 reasoning을 다시 더하지 않는다.

가격을 확정할 수 없는 model/tier/usage 이상은 known 금액으로 승격하지 않고 예약과
UNCERTAIN을 유지한다. 확실한 NOT_SENT는 예약을 해제하되 소비한 run ID를 재사용하지 않는다.
중간 prepared/priced/error/blocked accounting과 최종 controller.usage를 canonical JSON으로
대조한다. bool과 int를 같다고 취급하지 않는다. 호출/요청 수 요약도 별도로 대조한다.
이 검사는 조건부 rate-card 계산이며 외부 과금서 인증은 아니다.

## memory와 파일 replay

PaidLifeSession.export_report가 CLI와 memory replay에 같은 export 계약을 제공한다.
memory replay는 저장 보고서와 같은 replay_paid_report/audit_controller 경로를 사용한다.
원래 engine inputs/events/results/execution과 raw·candidate 검사를 유지하고 감사 대조를 추가했다.
Provider/key/socket/DNS가 없어도 실행한다. malformed 보고서는 안전한 ValueError로 거부한다.
전송 중 서로 다른 시점의 회계/rows를 혼합해 일관성을 잃은 export도 성공으로 표시하지 않는다.
안정된 새 snapshot을 확보해야 하며 누락된 journal을 추정해 복구하지 않는다.

## 현재 제안과 명령

이전 Phase4.3 source용 제안과 run ID를 현재 승인 대상으로 재사용하지 않는다.
모델/high/default/seed42/Ada/24h/32 attempts/output4096/input500000/output131072/
USD1.00/120초/무재시도 계획은 그대로이며 현재 source 결합만 갱신한다.
공식 모델·단가와 조건부 비용 상한 근거는 [Phase4.2 계획](PHASE4_2_LIVE_PREFLIGHT.md)을 따른다.
실 실행 전 계정 접근·가격·서버 schema는 별도로 재확인해야 한다.

| 항목 | 현재 미승인 제안 |
| --- | --- |
| run ID | 91bb76cd-e684-4926-8090-7d0f37338f8e |
| source digest | cea613a017398e75641f3bff539b54eca525e8494bdbefca4e19066830f19acc |
| plan digest | 89447928f2357efef57f0f9cbc9ce8a53beced16cdb7a5f6136301d34b95380d |
| wire protocol digest | fe36110945c5b323c583268e067fb73e462ad004ffc87f07117d30f4f5d8b322 |
| registry | 기존 worktree artifacts/phase4_3/runs.sqlite3, 위치를 바꿔 한도를 초기화하지 않음 |

```powershell
Set-Location C:\Users\user\Documents\JourneyMap\.worktrees\phase1
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
# proposal만 출력, 전송·key·registry 생성 없음
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_paid --run-id 91bb76cd-e684-4926-8090-7d0f37338f8e
# 저장된 모의 보고서 감사: 유료 호출 없음
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_paid --replay artifacts/phase4_3_1/final/normal.json
# 현재 실행 금지: 독립 재검토 및 별도 승인 후에만 사용
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_paid --execute --run-id 91bb76cd-e684-4926-8090-7d0f37338f8e --approval artifacts/phase4_3_1/approval-human.json
```

approval-human.json은 만들지 않았다. canonical registry도 생성하지 않았다.
valid grant를 digest로 자동 생성하지 않고 기존 인간 승인/단일 소비 경계를 유지한다.
이전 소스/선언 없는 보고서의 자동 migration, 누락 journal 복원, 외부 청구서 인증,
인간 서명이나 악의적인 전체 기록 재작성 방지는 추가하지 않는다.
실행과 검증 범위는 [완료 보고서](PHASE4_3_1_REPORT_2026-10-15.md)를 따른다.
