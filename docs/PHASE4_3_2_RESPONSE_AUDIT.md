# Phase4.3.2 · 응답 보관본과 owner 상태 감사

2026-10-15. Phase4.3.1 독립 검토 F1의 journal→call 역방향 대조를 보완한다.
기존 [snapshot·회계 계약](PHASE4_3_1_AUDIT.md)을 유지하며 이번 변경이 현재 source 결합을 대체한다.
report_schema는 life-paid-report-2 그대로다. 엔진, prompt/schema, HTTP 계획·가격·UI는 변경하지 않는다.

## 응답과 상태의 대조

OWNER_PACKET에서 이미 RESPONSE_DELIVERY가 기록된 호출은 call.raw가 반드시 존재해야 한다.
RESPONSE_RECEIVED의 원문으로 parsed_response와 raw_record를 만들어 그 보관본과 정확히 대조한다.
timeout 뒤 회수되었거나 RUN_UNCERTAIN 이후 late 폐기된 응답도 같은 규칙을 적용한다.
raw가 있을 때만 검사를 시작하지 않으며, 누락된 원문을 복구하거나 추정해서 통과시키지 않는다.

반대로 성공 전달이 없는 OWNER_PACKET은 raw가 없어야 한다. 미전송, HTTP 실패,
usage/identity 검사 실패는 성공 전달과 구분한다. RESPONSE_RECEIVED가 존재한다는 이유만으로
call.raw를 요구하지 않는다. owner가 아직 packet을 회수하지 않은 성공 응답은 journal에
RESPONSE_DELIVERY가 있어도 call.raw 없이 PENDING/worker_in_flight 상태로 남을 수 있다.
이 경우 기존 불확정 snapshot 계약을 따르며 outcome_certain=false다.

OWNER_PACKET=READY는 성공 전달이 있어야 하고 late 폐기 packet일 수 없다.
이 packet 이후 RESPONSE_CLASSIFIED가 없으면 최종 call.status는 READY여야 한다.
분류 행이 있으면 원문과 후보를 재검증하고, 실패 분류는 기록된 오류와 ERROR 상태여야 한다.
성공 분류는 기존 action/receipt/completion 대조에 따라 상태를 확인한다.
PENDING이나 기록에 없는 ERROR로 바꿔서 분류 전후 검사를 피할 수 없다.

memory replay와 파일 replay는 같은 audit_controller를 사용한다. 원문 누락과 상태 모순은
ValueError와 CLI nonzero이며 audit_verified/journal_checked 성공을 반환하지 않는다.
기존 FINISHED/UNCERTAIN, outcome_certain 의미는 바꾸지 않는다. timeout의 확정된 회계와
실패 행동을 구분하며 outcome_certain을 행동 성공이나 실제 청구서 인증으로 해석하지 않는다.

## 현재 미승인 제안

| 항목 | 값 |
| --- | --- |
| run ID | 86212ada-c33b-4813-94a9-2fdf4a32774d |
| source digest | b0cd3e00bc45b27bc7e8ffe63bc3481e4ff23d8eb8c61e48ea46fb02f66d5a06 |
| plan digest | 89447928f2357efef57f0f9cbc9ce8a53beced16cdb7a5f6136301d34b95380d |
| protocol digest | fe36110945c5b323c583268e067fb73e462ad004ffc87f07117d30f4f5d8b322 |
| canonical registry | 기존 worktree artifacts/phase4_3/runs.sqlite3 |

모델/high/default/seed42/Ada/24h/32회/output4096/input500000/output131072/USD1.00/120초/
무재시도 계획은 유지한다. 기존 source용 제안을 현재 승인 대상으로 재사용하지 않는다.
final/execution-proposal.json은 authorized=false, template=NOT_APPROVED이며 실행 승인이 아니다.
실제 approval-human.json 및 canonical registry는 생성하거나 초기화하지 않았다.

```powershell
Set-Location C:\Users\user\Documents\JourneyMap\.worktrees\phase1
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
# 미승인 proposal만 출력: key 조회, 전송, registry 생성 없음
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_paid --run-id 86212ada-c33b-4813-94a9-2fdf4a32774d
# 저장된 새 모의 보고서: 성공
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_paid --replay artifacts/phase4_3_2/final/normal.json
# 원문 삭제 + READY→PENDING 반례: 실패가 정상
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_paid --replay artifacts/phase4_3_2/final/bad-ready-pending.json
```

실제 실행은 후속 독립 재검토와 별도 명시적 승인 이후다. 이번 작업은 승인 생성·키 조회·
API 호출·유료 실행을 포함하지 않는다. 과거 source 보고서 자동 migration, journal 복원,
실제 crash recovery, 외부 청구서/인간 서명 인증이나 전체 기록 재작성 방지는 범위 밖이다.
검증 범위와 보존 결과는 [완료 보고서](PHASE4_3_2_REPORT_2026-10-15.md)를 따른다.
