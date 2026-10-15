# Phase 4.3 · 승인된 생활 Responses 경로

2026-10-15. Phase 4.2 독립 검토의 후속 지시를 구현한다. HTTP/credential/승인/journal과
생활 엔진 연결 코드는 제공하지만, 이번 작업은 **유료 실행 승인이나 실제 API 실행이 아니다**.
테스트는 합성 승인·합성 key·가짜 connection만 사용한다. 기존 전원 RULE/offline/UI는 유지한다.

## 계획과 실행 결합

LifeExecutionPlan의 모델·reasoning·상한과 digest는 Phase 4.2 그대로다.
gpt-5.4-mini-2026-03-17, high, default tier, seed42, Ada만 Provider, tick0~24,
32 attempts, output4096/회, 누적 input500000/output131072, generation USD1.00,
120초, 자동 retry/수리/fallback 없음, 연속 엔진 무효3회 중단이다.
단가·조건부 예약 계산과 공식 출처는 [기존 계획](PHASE4_2_LIVE_PREFLIGHT.md)을 따른다.
개발 도구 Sol/High와 API 모델 선택은 별개다. 실제 실행 직전 가격·접근·schema 호환성을 재확인한다.

| 결합 항목 | 이번 검토용 제안 |
| --- | --- |
| 계획 digest | 89447928f2357efef57f0f9cbc9ce8a53beced16cdb7a5f6136301d34b95380d |
| Python 소스 digest | 35f1248a19cb5e98573ac544a93d428727bf7087b7e1f6b8b1707aaf879b94b9 |
| 프로토콜 | life-paid-responses-1 |
| 프로토콜 digest | fe36110945c5b323c583268e067fb73e462ad004ffc87f07117d30f4f5d8b322 |
| 제안 run ID | a7b8f57e-9633-40f8-b8f3-6f9c1df0609f |
| canonical registry | 기존 worktree의 artifacts/phase4_3/runs.sqlite3 |

소스 digest는 package 내 모든 Python 파일의 상대 경로와 LF 정규화 바이트를 포함한다.
프로토콜 digest는 버전·생활 prompt와 schema를 포함한다. registry의 절대 경로도 결합한다.
입력 body에는 승인/run/예산/검증 라벨을 넣지 않고 기존 Observation 정보만 제공한다.

ExecutionApproval은 operator가 별도 인간 승인을 받은 뒤 공급하는 로컬 선언 파일이다.
approval_id UUID, approved_by=human-user, decision=approve-one-paid-life-run,
scope=[read-openai-api-key,paid-responses]와 정확한 bound 다섯 필드를 검증한다.
digest나 key 존재만으로 허가하지 않는다. CLI는 --execute, --run-id, --approval을 함께 요구한다.
기본 명령은 authorized=false 제안만 출력하며 journal/credential/connection을 만들지 않는다.
현재 제안의 template은 NOT_APPROVED여서 실행할 수 없다. 실제 승인 파일은 생성하지 않았다.

이 파일은 인간을 암호학적으로 인증하는 수단이 아니다. 로컬 operator와 파일 시스템을 신뢰하는
명시적 승인 기록이다. Codex는 별도 사용자 승인 없이 유효한 파일을 만들거나 실행하면 안 된다.
과거 M8/연구 승인이나 임의 caller의 digest를 재사용하지 않는다. 악의적인 Python 코드 실행이나
registry 삭제까지 막는 보안 경계라고 주장하지 않는다.

## Durable journal과 재사용 차단

RunJournal은 표준 라이브러리 SQLite, synchronous=FULL, DELETE journal과 transaction을 사용한다.
runs 테이블은 run_id와 approval_id 모두 unique다. claim은 credential 조회 전에 영속화한다.
종료/불확정/중도 종료된 ID는 다시 열거나 counter를 초기화하지 않는다. live resume는 없다.
CLI의 registry 위치는 고정이며 caller가 임의 새 위치로 한도를 초기화하는 옵션은 없다.
운영자가 이 DB를 보존해야 한다. 복사본/다른 PC를 가로지르는 분산 사용량 제한은 아니다.

entries는 run/ordinal/stage/data/previous digest를 기록한다. append는 하나의 FULL transaction이며
저장 실패는 다음 네트워크 동작을 차단한다. hash chain은 손상 검사용이며 서명/외부 진본 증명은 아니다.

| stage | 의미 |
| --- | --- |
| RUN_CLAIMED | 승인·계획·소스·프로토콜·run 결합을 소비함 |
| PREPARED_NOT_SENT | sequence/request digest와 예약을 영속화, 아직 credential/network 이전 |
| ATTEMPTED | 안전한 전체 request/body 기록 완료, 이후 connection을 한 번 시도함 |
| NOT_SENT | credential/전송 전 실패를 확실히 확인, 실제 connection 시작 없음 |
| HTTP_RECEIVED | 비200 status만 보존, body/headers는 보존하지 않음 |
| RESPONSE_ENVELOPE_RECEIVED | 200 응답의 byte 수/hash 수신, raw 공개 가능성 검증 전 |
| RESPONSE_RECEIVED | strict JSON과 secret scan을 통과한 raw |
| USAGE_CHECKED / USAGE_PRICED | reported tokens 확인 / 승인 단가를 적용할 수 있는 금액 계산 |
| REQUEST_ERROR | 전송 시도 이후 오류·불확정, 재시도 없음 |
| OWNER_TIMEOUT / OWNER_PACKET | 세계 정지 / 완료 packet 수신·late 폐기 |
| RESPONSE_CLASSIFIED / ACTOR_RECEIPT / ACTOR_COMPLETION | 후보 분류와 trusted 접수·완료 연결 |
| RESPONSE_DELIVERY | worker 완료 시 owner가 이미 닫혔는지 기록 |
| RUN_FINISHED / RUN_UNCERTAIN | 성공 여부와 별개인 소비된 run의 종료 상태 |

ATTEMPTED는 서버가 받았다는 증거가 아니다. connection 이전 crash도 보수적으로 불확정이다.
전송 후 응답 기록 실패/부분 전송/프로세스 종료는 자동 재시도나 재개를 금지한다.
DB 손상/분실, 파일 시스템 또는 전원 장애에서 복구 가능성을 실제로 실험한 것은 아니다.

## 생활 전용 HTTP와 정보 경계

LifeHTTPProvider는 identity.kind=live인 별도 concrete adapter다. 가짜 connection 주입 테스트도
이 실제 구현의 capability를 시험한 것이며 실제 모델 증거로 이름을 바꾸지 않는다.
admit_provider가 승인과 registry를 먼저 확인한다. credential loader와 connection factory는 별도로
주입 가능하며 production 기본값만 환경 OPENAI_API_KEY와 HTTPSConnection을 사용한다.
기존 연구 OpenAIProvider와 모의 RecordingResponsesTransport 타입 제한은 변경하지 않는다.

목적지는 api.openai.com의 POST /v1/responses로 고정한다. HTTPS 인증서 검증은 표준 기본값이다.
redirect/retry/proxy/custom headers/tools는 없다. Authorization과 Content-Type만 직접 지정한다.
body는 생활 schema, store=false, high, max_output_tokens4096, default tier를 사용한다.
전체 body65,536 bytes, response2,000,000 bytes, 행동 text16,384 bytes 제한이다.
bounded read1과 socket timeout120초 및 owner의 단조 deadline을 적용한다.
timeout은 원격 취소나 미과금의 증거가 아니며 OS/DNS/막힌 thread의 강제 종료를 보장하지 않는다.

PaidLifeSession만 고정 세계 설정으로 조립한다. 일반 MixedLifeSession에 live Provider를 주입하는
우회는 거부한다. 일반 LifeDecisionService도 approved concrete adapter 외 live 구현을 거부한다.
현재 Observation으로 trusted actor/run/tick/Observation ID를 결합하고 기존 engine이 검증한다.
worker는 Provider/transport/journal metadata만 처리하며 canonical 변경은 owner만 한다.
세계·기억·사회·UI는 확장하지 않았다. paid Provider/session은 checkpoint 저장을 계속 거부한다.

## 회계, timeout, 비밀 보호

Budget의 전송 전 조건부 예약은 한 회 $0.318432이다. 상한을 수용하지 못하면 key/connection
전에 멈춘다. 전송이 확실히 시작되지 않았으면 NOT_SENT로 기록하고 예약을 해제한다.
전송 불확정/unknown usage는 예약을 유지한다. run ID는 어느 경우든 다시 쓰지 않는다.

reported usage를 먼저 별도 확인하고 **model과 tier가 맞을 때만** 승인 단가로 정산한다.
model/tier 불일치는 reported tokens를 보존하되 cost_state=UNCERTAIN, 예약 유지,
기존 known rate-card 누적 금액에 해당 요청을 추가하지 않는다. 이 예약이 알 수 없는 다른
모델/tier 요금까지 보장하는 상한이라고 표시하지 않는다. usage 이상/예약 초과도 불확정이다.
금액은 세금·환전·실 청구서가 아닌 조건부 rate-card 계산이다. reasoning은 output에 다시 더하지 않는다.
refusal/incomplete는 검증 가능한 usage를 정산한 뒤 중단한다.

시작/완료는 Phase4.1의 같은 단조 clock을 사용한다. 완료≤기한은 허용, 이후 완료는 폐기한다.
owner timeout은 세계를 멈추며 아직 보내지 않은 요청도 전송 전에 차단한다.
이미 시도한 요청의 완성된 late 응답은 행동으로 실행하지 않고 usage와 raw/journal을 회수한다.
owner가 닫힌 뒤에도 살아 있는 worker는 안전한 회계 결과를 journal에 남길 수 있다.
CLI는 최대120초 더 drain하고, 여전히 미회수면 RUN_UNCERTAIN으로 닫는다. 강제 crash에서는
late usage가 회수되지 않을 수 있으며 예약과 소비된 run을 유지한다. 자동 crash recovery는 없다.

실 credential은 기록하지 않는다. response headers와 HTTP error body는 보존하지 않고,
예외는 allowlisted reason으로 바꾼다. 정상 JSON은 해석·정규화한 뒤 secret echo를 검사한다.
raw/중첩 metadata/Unicode escape echo는 SECRET_ECHO로 중단하고 행동에 전달하지 않는다.
malformed/deep envelope는 비밀 없음이 입증되지 않아 raw를 숨기고 byte/hash만 남긴다.
유효 envelope 안의 잘못된 행동 JSON은 안전한 원문을 보존하고 기존 F1 경계에서 멈춘다.
secret의 임의 인코딩/외부 파일/OS memory까지 다루는 범용 DLP라고 주장하지 않는다.

## 정확한 명령과 승인 경계

아래 첫 명령은 안전한 제안 출력이다. 두 번째 실행 명령은 **독립 검토와 별도 승인 후에만**
사용할 명령이며 이번 작업에서는 실행하지 않았다. 승인 파일도 아직 없다.

```powershell
Set-Location C:\Users\user\Documents\JourneyMap\.worktrees\phase1
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_paid --run-id a7b8f57e-9633-40f8-b8f3-6f9c1df0609f
# 별도 승인 후에만: 현재 실행 금지
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_paid --execute --run-id a7b8f57e-9633-40f8-b8f3-6f9c1df0609f --approval artifacts/phase4_3/approval-human.json
# 보고서 재생: 실제 key/API/Provider 필요 없음
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_paid --replay artifacts/phase4_3/final/replayable-normal.json
```

유효 승인 후에는 추가 구조 변경 없이 CLI로 실행할 수 있다. 가격/접근 조건이 바뀌거나 소스가
달라지면 기존 승인을 사용할 수 없다. 제안 run은 아직 canonical registry에서 소비되지 않았다.
실제 실행의 보고서는 registry 옆 `<run-id>.json`에 저장되며 inputs/events/results/execution,
controller raw·후보·receipt·completion·journal을 포함한다. replay는 같은 소스의 data-only 실행이며
Provider/credential/connection을 만들지 않는다. source 불일치/변조/불완전 journal은 거부한다.
replay의 journal 검사는 chain와 응답/행동 연결 검사이며 외부 청구서 인증은 아니다.

검증과 산출물은 [완료 보고서](PHASE4_3_REPORT_2026-10-15.md)를 따른다.
