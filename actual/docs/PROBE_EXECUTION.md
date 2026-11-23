# JourneyMap Actual 대응 의사결정 비교 실행 계약 v2

2026-10-26 후속 정책 구현: 아래는 보존된 v2 계약이다. 현재 source의 v2 HTTP/run은
키 조회 전에 거부하며 recording과 감사만 유지한다. 실제 전송은 별도 v3 권위 원장·승인 계약을
검증해야 한다. [후속 정책](PROBE_CONTINUATION_POLICY.md)을 따른다. 운영 인수·실행 승인은 미완료다.

2026-10-26: protocol은 life-reasoning-probe-execution-2다. 요청 입력·순서·계획·회계 gate는 유지하고,
버전 있는 전송 진단을 RESPONSE/NOT_SENT journal과 같은 transaction에 결합한다.
[진단 계약](PROBE_TRANSPORT_DIAGNOSTICS.md), [미확정 회계 정책 초안](PROBE_UNCERTAIN_ACCOUNTING_DRAFT.md),
[예산 검토](PROBE_BUDGET_REVIEW_2026-10-26.md), [사전 분석 기준](PROBE_ANALYSIS_PLAN_2026-10-26.md)을 따른다.
기존 실제 v1 run은 UNCERTAIN으로 보존하며 실행 준비는 미완료다. v2 진단은 이를 해소하거나 다음 실행을 허가하지 않는다.
v1 승인은 새 source/protocol에서 거부한다. v1 report는 변경 전 동결 runtime에서만 감사하며 재봉인하지 않는다.

2026-10-19 KST. 구현과 오프라인 검증 계약이며 유료 실행 승인이 아니다.
기존 life-reasoning-probe-offline-1의 72개 요청을 독립적인 한 번의 판단으로 비교한다.
24시간 생활 실행, 논문 재현, 기억 추가나 모델 routing은 이번 범위가 아니다.

## 동결된 후보 계획

| 항목 | 값 |
| --- | --- |
| 모델 | gpt-5.4-mini-2026-03-17 |
| 조건 | high/기존, medium/기존, medium/짧은 판단 순서 |
| 표본 | 합성 사례 12개, 세 조건, 각 두 반복 |
| 최대 요청 | 72, corpus의 고정 순환 순서 |
| 출력 상한 | 요청당 8,192 |
| service tier / 저장 | default / store=false |
| 후보 비용 상한 | USD 1.00, 실제 승인 아님 |
| 누적 토큰 상한 | 입력 500,000 / 출력 589,824 |
| 요청 예약 | 입력 400,000 + 출력 8,192, 조건부 USD 0.336864 |
| 단가 | 입력 750 / cached input 75 / 출력 4500 nano-USD/token |
| timeout | 응답 완료까지 120,000ms, 초과하면 행동 미전달 |
| 재시도 / 대체 슬롯 | 0 / 없음 |

단가는 기존 계획의 고정 rate card다. 이 변경에서 최신 청구 단가를 다시 조회하거나 계정을 확인하지 않았다.
예약은 모델과 tier가 일치하고 보고 usage가 상한 안에 있다는 조건의 보호 장치다.
가격이나 제공자 동작을 외부에서 강제하는 결제 한도가 아니며 실제 청구서도 인증하지 않는다.

72개 모두 출력 상한을 사용하면 출력 비용만 USD 2.654208이다. 따라서 USD 1.00으로 전체
완료를 보장하지 않는다. 다음 요청의 최악 조건 예약이 남은 예산에 들어가지 않으면 종료한다.
미실행 슬롯을 삭제하거나 조건별 분모를 줄이지 않는다. 종료 시 조건별 표본 수가 달라질 수 있다.
예산을 올리려면 새 계획 버전과 검토, 별도 승인이 필요하다. 이 계획은 소규모 진단 후보이며
72개 완료나 통계적 효과 입증을 위한 확정 표본 설계가 아니다.

## 실행 정책과 정보 경계

각 요청은 같은 사례의 동일 관측을 사용한다. 기존 corpus의 body와 순서를 그대로 결합한다.
case ID, 연구자 fixture, 미래 예약, 승인 정보와 journal은 모델에 전송하지 않는다.
성공 후보는 새 결정적 세계 분기에서 행동 하나를 평가한다. 결과를 다음 모델 관측에 연결하지 않는다.
12개는 결정적 fixture 수이며 독립적으로 수집한 세계 표본 수가 아니다.

READY, INCOMPLETE, REFUSAL은 각각 기록하고 다음 독립 슬롯으로 진행한다. READY의 후보가
잘못된 행동인 경우 엔진 평가에 INVALID_CANDIDATE로 남으며 삭제하거나 재시도하지 않는다.
이 정책은 하나의 실제 생활 세계를 계속 진행하던 기존 paid-life의 INCOMPLETE 정지 정책과 다르다.
기존 paid-life 구현과 high 고정 검증은 수정하지 않는다.

HTTP 오류, 신원 불일치, usage 누락/초과, 중복 response ID, 응답 오류, timeout과 journal 실패는
다음 호출을 막는다. 사용량이 유효하고 모델/tier/response ID가 확인된 경우에만 rate card로 정산한다.
추론 토큰은 출력의 부분집합으로 한 번만 과금 계산한다. 나머지는 예약을 유지하고 비용을 null로 둔다.
식별자가 같은 응답을 두 반복으로 재사용하지 않는다.

## 승인과 영속 registry

별도 protocol은 life-reasoning-probe-execution-2다. 승인 결합에는 run UUID, 승인 UUID,
source, 전체 corpus, 요청 순서, 계획 전체, endpoint와 registry 절대 경로가 포함된다.
사람이 명시 승인한 뒤 별도 작성한 approval만 실제 경로에 전달한다. 도구가 승인서를 만들지 않는다.

- approved_by: human-user
- decision: approve-one-paid-probe
- scope: read-openai-api-key, paid-responses
- bound: propose 출력의 binding과 완전히 일치

이는 신뢰된 로컬 운영자의 승인 기록이지 사람을 암호학적으로 인증하는 장치가 아니다.
파일과 코드, 계정 운영자를 신뢰한다. registry 삭제/복제/되돌리기는 허용된 운영이 아니다.
다른 기기에서 실행하려면 사용 이력을 먼저 인수하며, 빈 registry로 소비 이력을 우회하지 않는다.

전용 DB 파일명은 probe-runs.sqlite3이다. 기존 artifacts/phase4_3/runs.sqlite3는 사용하지 않는다.
기존 다른 형식 DB를 발견하면 초기화하지 않고 거부한다. 새 DB도 승인 검증 뒤에만 생성한다.
preflight는 새 출력 폴더 안의 합성 전용 DB를 사용하며 실제 registry로 옮겨 쓰지 않는다.

run ID와 approval ID는 UNIQUE로 한 번만 소비한다. OPEN이나 UNCERTAIN run이 하나라도 남으면
다른 승인으로 새 실행을 시작하는 것도 차단한다. 실행 재개, reset, 자동 재시도 기능은 없다.

## 기록 순서와 종료의 의미

RUN_CLAIMED → RESERVED → ATTEMPTED → RESPONSE → SETTLED를 거쳐 다음 슬롯 또는
RUN_STOPPED로 진행한다. 각 event와 row count, tail hash, status를 SQLite FULL transaction에서
함께 확정한다. 예약 기록은 키 조회보다 앞서고 ATTEMPTED 기록은 연결 및 전송보다 앞선다.
따라서 ATTEMPTED는 보수적인 전송 가능성 기록이며 실제 HTTP 도달을 증명하지 않는다.

키를 얻지 못하면 NOT_SENT로 기록해 예약을 해제한다. 전송 시도 뒤의 오류는 확실히 미전송이라고
단정하지 않는다. 프로세스가 종료되거나 기록 저장이 실패하면 OPEN과 잔여 예약을 그대로 보존한다.
응답은 저장됐지만 SETTLED 전 종료된 경우도 자동 완료나 재호출로 바꾸지 않는다.
inspect는 읽기 전용 진단이며 회계를 복구하거나 상태를 변경하는 명령이 아니다.

FINISHED는 이번 실행의 종료와 회계 정합성을 뜻한다. 72개 완료, 행동 품질, 24시간 완주를 뜻하지 않는다.
UNCERTAIN은 실제 비용을 확정할 수 없어 예약이 남은 종료다. OPEN은 종료 기록까지 확정되지 않은 상태다.

HTTPS는 api.openai.com의 /v1/responses만 사용한다. redirect나 SDK 재시도는 없다.
키는 환경변수 OPENAI_API_KEY에서 승인 후 읽고, 헤더는 기록하지 않는다. 키가 응답 원문이나
정규화 JSON에 나타나면 응답을 보관하지 않고 중단한다. 잘못된 JSON/UTF-8도 원문을 보관하지 않는다.
이 경우 비용은 확인 불가로 남는다. 환경변수 외 저장소를 자동으로 탐색하지 않는다.

동기 HTTP의 socket timeout과 read 사이 deadline 검사를 사용한다. 강제 종료 가능한 전체 벽시계
120초 제한은 아니다. 한 번의 blocking read가 돌아올 때까지 지연될 수 있다. 응답 완료 시각이
120초를 초과하면 TIMEOUT이고, 완전히 받은 유효 usage는 정산한다. 살아 있는 worker를 백그라운드에
두고 다음 요청을 시작하지 않는다. 기록된 지연은 HTTP 시작부터 수신 완료까지이며 키/DB 준비 시간은 제외한다.

PE-R1 수신 경계: EOF/isclosed나 JSON 파싱 성공만으로 HTTP 메시지가 완료됐다고 보지 않는다.
Content-Length가 남거나 chunked 수신이 완료되지 않으면 원문과 usage를 채택하지 않고
TRANSPORT_UNCERTAIN으로 중단한다. 엔진 평가와 다음 전송은 없으며 예약과 null 비용을 유지한다.
정상 Content-Length, chunked, close-delimited 수신은 지원한다. 완전히 받은 늦은 응답의
usage 정산, 2MB 초과 원문 보류와 키 echo 차단은 유지한다. 이 변경은 probe 전송 경계에만 적용한다.

## replay와 감사

audit는 고정 소스에서 corpus를 재생성하고, journal 순서, raw 응답, 정산과 snapshot을 다시 계산한다.
원문 없이 SETTLED를 추가하거나, 비용/상태/body를 변조하고 hash만 다시 계산해도 거부한다.
READY 응답만 기존 로컬 엔진 분기로 평가하고 모든 실패 및 미실행 슬롯은 별도 분모에 유지한다.
다시 모델에 호출하지 않는다. 외부 제공자의 응답 진위나 청구서, 인간 승인을 독립 인증하는 감사는 아니다.

소스 파일 추가로 source identity와 corpus digest가 변경된다. 기존 probe 산출물과 과거 실제 기록을
새 source에 맞춰 재봉인하지 않는다. 예전 산출물의 재평가는 해당 동결 소스를 사용한다.

## 명령

프로젝트 Python 3.12와 PYTHONPATH=src를 사용한다. 출력 폴더는 새 경로여야 한다.

```text
python -m journeymap.examples.probe_execution preflight --output artifacts/probe-execution/offline-v1
python -m journeymap.examples.probe_execution audit artifacts/probe-execution/offline-v1/report.json
python -m journeymap.examples.probe_execution propose --run-id <새 UUID> --registry <검토할 전용 DB 절대 경로> --output <새 제안 폴더>
python -m journeymap.examples.probe_execution inspect --registry <전용 DB> --run-id <기존 UUID> --corpus <저장 corpus.json> --output <새 진단 폴더>
```

propose는 authorized=false 계획만 저장하고 registry나 키를 읽지 않는다. preflight는 recording
transport와 명시적인 합성 승인 객체를 사용한다. 합성 승인 객체는 실제 HTTP 경로에서 거부된다.
preflight DB에는 합성 승인 metadata가 남지만 사람이 승인한 approval 파일을 생성하는 것은 아니다.

과거 v2의 run 하위 명령은 --approval, --run-id, --registry, --output을 받았으나 현재는 거부한다.
이번 구현/데스크톱 적용 지시에 run 실행은 포함되지 않는다. 독립 검토와 사용자 실행 승인 이후에만
실제 경로, 기기 간 소비 이력과 예산을 확인하고 구체적인 명령을 확정한다.

## 다음 검토

데스크톱에 적용한 뒤 승인 결합, 기록 누락, crash 경계, 비용과 timeout의 독립 검토를 받는다.
기존 두 실제 run의 원본 요청 대조는 별도 미완료 작업이며 이 합성 실험이 대신하지 않는다.
F1 조사 종료/미재현/원인 미확정과 기존 수정 필요 판정을 유지한다.
개발용 권고는 Sol / High다. 비용 증가나 실제 호출은 이 문서로 승인되지 않는다.
진단 v2 반영 후 독립 검토와 미확정 회계 정책 판단이 먼저 필요하다. 새 실제 후보/승인 파일은 이번에 만들지 않는다.
