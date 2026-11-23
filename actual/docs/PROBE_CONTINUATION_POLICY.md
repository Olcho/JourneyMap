# Probe 후속 회계 정책 v1

2026-10-26. 구현·오프라인 검증 대상이다. 독립 검토, 운영 이력 인수와 사용자 실행 승인은
별개이며 이 문서와 비활성 proposal은 실제 호출이나 운영 bootstrap을 승인하지 않는다.

## 제한된 예외와 보존

정책 `probe-uncertain-one-followup-1`, protocol `life-reasoning-probe-execution-3`,
권위 원장 `probe-lineage-authority-1`을 분리한다. 과거 v1/v2 reader와
US$1.00/입력 500,000 계획은 당시 감사용 계약으로 유지한다. 현재 v2 `run`과
`paid_probe.execute`의 HTTP는 비활성화하고 recording만 허용한다. 기존 paid-life는 변경하지 않는다.

원본 UNCERTAIN, 확인 비용 120,455,850 nano-USD, 미확정 예약 336,864,000 nano-USD,
총비용 null을 그대로 둔다. 정책은 해당 run/request/body에 후속 배치 한 번만 연결한다.
위험 수용은 과거 무과금·usage 확정·예약 해제가 아니다. 새 OPEN/UNCERTAIN의 재개·재시도·
추가 batch는 없다. FINISHED여도 두 번째 후속은 없다. 과거 READY 16개를 재사용하지 않는다.

## 이력 인수와 신뢰 경계

`HistorySpec`은 원본 registry 절대 경로/SHA-256, report 전체 목록, 동결 runtime
절대 경로/identity를 지정한다. 원본은 `mode=ro`, `query_only=ON`으로 읽는다.
전체 run 목록 및 모든 journal 행을 report 목록과 대조한다. 추가 run, orphan entry,
누락, count/tail/순서/원문 hash/상태/소비 ID 차이와 SQLite sidecar를 거부한다.
검토된 17회/70항목/단일 UNCERTAIN 이력만 인수한다. 추가 이력이면 후보를 다시 검토해야 한다.

일치하는 동결 runtime의 별도 프로세스 `paid_probe.audit_report`로 과거 report를 재계산한다.
입력 원문 hash, 전체 inventory, runtime identity를 결합하며 제출자의 `audit=true`는 받지 않는다.
동일 프로세스의 동일 bundle/runtime 성공만 캐시한다. runtime hash는 매번 확인하며 claim과
각 요청 전에 원본 DB/report를 다시 읽기 전용 대조한다. 실제 v1 a362와 변경 전 v2 912eb145
동결 runtime은 감사용이다. 과거 report를 새 source에 맞춰 재봉인하지 않는다.

bootstrap은 별도 `probe-history-import-approval-1`과 고정 경로/기기/identity/이력 결합이
필요하다. runner는 bootstrap을 호출하거나 누락 원장을 만들지 않는다. 이번에는 임시 모의 DB만
사용하며 운영 인수 완료 표시를 만들지 않는다. bootstrap CLI는 제공하지 않는다.

고정 path/device/authority ID/history digest와 신뢰하는 최신 sequence/tail을 다음 승인에
결합한다. 경로 복사, 빈 원장, stale 사본, 다른 기기, 부분 rollback은 현재 기준점과 맞지 않으면
거부한다. hostname은 운영자 결합값이며 암호학적 기기 인증이 아니다. 파일·코드·운영자를 신뢰한다.
모든 자료와 외부 최신 기준점까지 함께 과거로 되돌리면 로컬 hash만으로 탐지할 수 없다.
다른 기기 동시 실행 금지와 최신 기준점 별도 보관이 운영 전제다. 옛 동결 프로그램의 별도 실행을
새 코드가 원격 통제하지 않는다. 동결 코드는 감사에만 사용해야 한다.

portable audit는 캡처된 inventory/report/원장 내부 정합성을 검증한다. 외부 DB 전체를 실제로
읽었다는 주장의 진위, 현재 writer 부재·최신성·인간 승인·청구서를 인증하지 않는다.
운영 자격 검사는 물리 원본 대조와 신뢰하는 최신 기준점으로 별도 수행한다.

CP-R1 보완: successor가 있는 모든 snapshot은 승인·journal 재계산 전에 corpus 전체를
해당 runtime의 `probe.prepare()` 결과와 대조한다. fixtures·반복 수·단가를 포함하며 저장된
source/corpus digest 문자열이나 내용 digest 재계산만으로 정규 연구 설계 대조를 대신하지 않는다.
successor가 없는 import-only에는 corpus를 요구하지 않는다. 완성 report의 전체 대조도 유지한다.
이 검사는 새 source의 fixture로 확인하며 과거 자료는 당시 동결 runtime에서만 감사한다.

## 승인, 직렬화와 저장 중단

`probe-continuation-approval-1` 한 객체가 paid 승인과 한정된 위험 수용을 결합한다.
서로 다른 새 run/paid approval/risk acceptance UUID, 승인자 진술·정확한 scope·UTC 유효 기간,
정책/protocol/source/corpus/schedule/endpoint/plan 전체, 과거 대상과 비용/예약/null,
고정 authority checkpoint를 포함한다. 세 ID는 과거 두 ID와 역할을 바꾸어 재사용할 수도 없다.
합성 승인/합성 원장은 실제 HTTP 권한을 주지 않는다. 로컬 기록은 암호학적 인간 인증이 아니다.

권위 원장과 새 probe journal은 같은 SQLite 파일이다. 전체 실행 동안 OS writer lease를
보유하고 `BEGIN IMMEDIATE` 안에서 이력/승인/기간/기준점/ID/첫 예약 가능성을 검사한다.
소비 ID 세 개, SUCCESSOR_CLAIMED, RUN_CLAIMED와 journal 링크를 같은 transaction으로
확정한 뒤 키 조회로 진행한다. 각 RESERVED는 키 조회 전, ATTEMPTED는 전송 전에 확정한다.
ATTEMPTED는 보수적인 전송 가능성 기록이며 실제 서버 도달 증명은 아니다.

별도 exclusive `.attempt-<risk ID>` fence에는 ID와 승인 digest만 쓴다. 실행 허가가 아니다.
flush/fsync 뒤 claim하며 orphan fence도 해당 원장의 다음 claim을 모든 새 ID에 대해 차단한다.
fence조차 생성되지 않은 prefix에는 키/HTTP/claim이 없다. 파일과 최신 기준점을 함께 삭제하는
rollback이나 모든 저장장치의 전원 상실 내구성을 로컬 hash로 보장한다고 주장하지 않는다.

| 마지막 확정 prefix | 의미와 동작 |
| --- | --- |
| 검증 거부, fence 전 | claim/키/HTTP 없음, 자동 재실행 없음 |
| fence만 확정, claim transaction 실패 | claim 없음, orphan fence로 새 ID도 차단 |
| claim commit 직후 예외 | ID 소비와 OPEN 유지, 보상 rollback/재실행 없음 |
| RESERVED | 예약 유지, 후속 실행 차단 |
| ATTEMPTED/RESPONSE 저장 실패 | 마지막 prefix/예약 보존, 추가 전송 없음 |
| SETTLED commit 뒤 예외 | 확정 정산 prefix 보존, 자동 종료/재실행 없음 |
| RUN_STOPPED | FINISHED/UNCERTAIN 보존, 후속 한 번은 이미 소비됨 |

ID 복구/reset/resume/자동 증액 API는 없다. 정상 credential 미확보는 NOT_SENT로 그 요청
예약만 해제한다. 실제 전송 이후 불확정 usage를 0으로 만들지 않는다.

## 별도 후보 plan과 회계

`probe-followup-max72-1`은 오프라인 후보이며 지출 승인이 아니다. 기존 요청 본문/순서/모델
snapshot/effort/schema/service tier/store=false/fixtures는 바꾸지 않는다.

| 항목 | 후보 값 |
| --- | ---: |
| 배치/슬롯/재시도 | 1 / 72 / 0 |
| 요청 입력 예약 / 출력 한도 | 400,000 / 8,192 |
| 누적 입력 / 출력 | 28,800,000 / 589,824 |
| 새 코드 한도 | 24,260,000,000 nano-USD = US$24.26 |
| 과거 확인 / 미확정 예약 | 120,455,850 / 336,864,000 nano-USD |
| 조건부 probe 계보 한도 | 24,717,319,850 nano-USD = US$24.71731985 |
| 입력/cached/출력 단가 | 750 / 75 / 4,500 nano-USD/token |

최대 사용량 72회의 실제 Ledger 합은 24,254,208,000 nano-USD다. 추론 토큰은 출력에 포함되며
다시 더하지 않는다. 유효 가격/usage/신원의 조건부 코드 보호값이며 외부 청구 무조건 상한은 아니다.
기존 US$1.00 계획을 덮어쓰거나 임의 override하지 않는다. 별도 생활 실험 비용을 포함한 프로젝트
전체 한도로 표시하지 않는다.

`conditional_exposure = prior_known + prior_unresolved + new_known + new_reserved`를
각 요청마다 재계산한다. 승인 capacity는 관측 비용이나 별도 hold가 아니며 중복 합산하지 않는다.
새 run 성공으로 새 비용이 확정돼도 계보 total은 null이다. 새 불확정 예약은 과거 예약과 각각
표시한다. 실패/미실행도 72슬롯 분모에 남는다.

## 오프라인 검증과 종료점

정책 테스트는 실제 socket/DNS/HTTPS와 credential을 차단한다. 가짜 이력, 메모리의
synthetic/human-shaped 승인, 가짜 키/연결만 사용한다. 72회/73번째, 한도 경계+1,
1/17/72번째 불확정, 저장 전후 실패, 재봉인 변조, stale/path/device/소비 ID, 프로세스 경합과
패자 키/HTTP 0회를 검사한다. 기존 framing/진단/paid 회귀를 유지한다. 이는 correctness 증거이며
실제 LLM behavioral effect 증거가 아니다.

`python -m journeymap.examples.probe_continuation describe`는
`probe-continuation-policy-proposal-1` 비활성 설명만 출력한다. 실제 ID/원장/승인을 발급하지 않으며
사람 승인 parser는 이 스키마를 거부한다. `audit <report> --frozen-source <당시 이력 src>`는
재호출 없는 감사다. `run`은 독립 검토와 별도 운영 인수 및 정확한 사용자 승인 뒤에만 사용한다.

다음 단계는 독립 검토다. 운영 bootstrap과 실제 승인/키 조회/호출은 이번 범위 밖이다.
F1은 조사 종료·미재현·원인 미확정·기존 수정 필요 판정을 유지하며 재현 검사를 추가하지 않는다.


## MemoryReplica dynamic Actual (2026-11-02)

The additional MemoryReplica protocol is implemented in task-specific modules and preserves the v1/v2/v3 readers and previous one-followup meaning. Its new approval/plan is required even after B; the previous continuation approval does not cover MemoryReplica. It must use the completed latest B snapshot, not CP-R1's import-only state, and it can be consumed once in that same authority. TaskLedger reuses the existing nano-USD reservation/settlement with the fixed 63-request candidate; Decimal usage estimates do not release uncertain reservations. Missing/stale authority, unknown prior fence, consumed IDs or source/runtime mismatch stop before credential preparation. The original UNCERTAIN reservation, B records and F1 state remain intact. No operator intake, human approval file or paid execution was produced here; real eligibility remains unverified until a concrete current anchor and explicit approval are supplied.
