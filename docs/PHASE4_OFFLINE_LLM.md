# Phase 4 · 생활 세계의 선택적 Provider 연결

2026-10-15. 기본은 전원 RULE이다. opt-in `MixedLifeSession`은 노동자 한 명(기본 Ada)만
`LLM_ADAPTER_OFFLINE`으로 제어하고 다른 네 명은 기존 LifeController를 사용한다.
이는 실제 LLM 실행이 아니라 literal scripted response fixture를 Provider 계약에 연결한 기능이다.
fixture는 관찰로부터 규칙 답을 계산하지 않으며 adapter 뒤의 LifeController도 Ada를 대신 판단하지 않는다.

## 정확한 PowerShell 실행법

```powershell
Set-Location C:\Users\user\Documents\JourneyMap\.worktrees\phase1
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
# 기본: 전원 규칙, 최대 168시간
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_ui
# Ada 모의 Provider: 24시간, 요청 상한 32회
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_ui --controller offline
# UI 대기/정지 검증: 모의 응답을 버튼으로 전달. 이 옵션만 timeout 300초
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_ui --controller offline --hold-responses
```

브라우저에서 `http://127.0.0.1:8765`를 연다. `--port`로 포트를 바꿀 수 있다.
일반 모의 실행 timeout은 5초다. 수동 hold는 테스트용 응답 gate이며 실제 지연 모델이 아니다.
응답 대기 중 정지한 뒤 응답을 전달하면 DECISION_READY_PAUSED다. 단계/시작을 눌러야 접수한다.
조회/배속/중복 시작은 같은 판단 기회를 재발급하지 않는다. 종료는 Ctrl+C다.

```powershell
# 오프라인 혼합 실행과 입력/응답/후보/엔진 결과 export
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_mixed --output artifacts/phase4/my-mixed --save artifacts/phase4/my-final.json
# WORK가 진행 중인 5시간에 저장, 새 process에서 24시간까지 재개
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_mixed --until 5 --save artifacts/phase4/my-work.json
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_mixed --resume artifacts/phase4/my-work.json --save artifacts/phase4/my-resumed.json
# 한도 정지 예제
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_mixed --limit 1 --output artifacts/phase4/my-limit
```

출력 directory는 새 경로를 사용한다. UI 기본 저장 위치는 artifacts/phase4/live-checkpoint.json이다.
UI에 `--resume <path>`를 주면 저장된 제어기 배정과 설정을 복원한다. 저장본을 CLI 옵션으로
조용히 다른 모드로 바꾸지 않는다. 실제 Provider 선택 옵션은 없고 offline factory의 live kind는
credential/transport 생성보다 먼저 PermissionError로 거부한다.

## 정보와 후보 계약

ProviderRequest/RawModelResponse/identity/failure와 기존 strict_json, normalize_request,
행동별 payload 검증을 재사용한다. OpenAI request_body는 opt-in format_override만 추가했다.
기존 기본 body/schema/prompt/연구 wire는 유지된다. 통신 구현을 복사하거나 새 실제 연결을 만들지 않았다.

생활 prompt=life-observation-decision-1, schema=life-candidate-1,
configuration=life-offline-1이다. 실제 outgoing body 전체는 RecordingMockTransport에서 기록한다.
body는 model/input/store=false/text.format/max_output_tokens/reasoning만 가진다.
인물별 입력은 simulation_time/horizon과 현재 Observation.content다. 자기 공개 결과, 역할/거처/
초기 지도/고용 관계/현장 기회는 이 content의 기존 perception 경계를 따른다.
예산/개발 검증 라벨, 규칙 blocked/retry memory, 타인 private 자원, 미래 예약, researcher UI
데이터, 이전 응답 ID와 외부 대화는 입력에 없다. 보관한 전체 call 기록도 다음 판단의 memory로 넣지 않는다.
규칙 정책에는 과거 학습 상태가 있으므로 두 제어기의 기억 조건이나 성능 비교가 공정하다고 주장하지 않는다.

정확한 후보는 `{action_type,payload}`다. MOVE/WAIT/REST/BUY/CONSUME/WORK만 허용한다.
WORK는 `{job_id: nonempty string}`이며 다른 행동은 기존 엄격한 payload 계약을 따른다.
양의 정수에 boolean은 불가하고 추가 필드, 중복 JSON key, 비유한 값, authority 필드는 거부한다.
trusted adapter가 실제 Observation에서 actor/run/time/request ID를 결합해 bound concurrent port에
제출한다. 유효 후보의 존재하지 않는 ID/현재 현장 조건/완료 실패는 엔진 판정이다.
세계나 자원을 모델 텍스트로 수정하지 않는다. horizon을 넘는 pending은 기존대로 보존한다.
모델 내부 사고를 요구하지 않는다. UI에는 outgoing 입력, 실제 응답, 후보, 접수와 완료 결과를 분리한다.

## 시간·대기·실패 상태

MixedLifeSession은 LifeSession의 sampling/accounting/engine을 사용하고 남은 actor 활성화 순서를
보존한다. worker에는 ProviderRequest와 모의 응답 transport만 전달한다. kernel/GamePort는 없다.
행동 접수/완료/시간 진행은 HTTP owner thread에서만 한다. 응답 대기 동안 다른 인물의 행동도
새로 진행하거나 완료시키지 않으며 세계 시각/욕구가 동결된다. 강한 비동기 세계는 미구현이다.

| 상태 | 의미 |
|---|---|
| RULE / LLM_ADAPTER_OFFLINE | 인물 제어기 구분, 후자는 실제 모델이 아님 |
| WAITING_FOR_DECISION | 한 판단 기회의 응답 대기, 조회/정지 가능, 저장/복원 불가 |
| DECISION_READY_PAUSED | 수신했으나 미접수, 명시적 재개만 접수하며 안전 저장 가능 |
| PAUSED_ERROR | refusal/incomplete/invalid candidate/transport/timeout/size/identity 오류로 정지 |
| LIMIT_REACHED | 요청 상한 또는 연속 무효 행동 상한, 추가 호출 차단 |
| HORIZON | 가상 기간 종료, 추가 tick 진행 차단 |

정지 후 도착한 응답은 READY로 보관하고 시간을 자동 재개하지 않는다. 종료 세션의 late 답은
접수하지 않는다. timeout이 기록된 요청의 늦은 응답도 폐기한다. 실패 시 JSON 수리/자동 retry/
RULE·WAIT fallback은 없다. 엔진 거부/완료 실패는 Provider 오류와 별도 기록하며 인물당 한 tick에
최대 한 판단만 가능하다. 연속 세 번 무효 행동이면 LIMIT_REACHED다. 성공 완료 시 연속 실패가 초기화된다.

기본 요청 상한32, 전체 outgoing body 최대65,536 UTF-8 bytes, 출력 상한1024 tokens 및
응답 text 최대16,384 UTF-8 bytes다. mock transport는 실제 토큰 과금/사용량을 측정하지 않는다.
요청 전 한도와 입력 크기를 검사한다. requests_started는 worker로 넘긴 요청 시도 수이며 실패도 포함한다.
fixture 소진/입력 크기 오류는 전송하지 않고 정지한다. 실제 모델 token limit의 이행은 검증하지 않았다.

## checkpoint와 재생

journeymap-life-checkpoint-2를 사용한다. Phase 3의 source hash 일치/과거 source 거부 계약은 유지하며
v1 자동 migration은 없다. 현재 source의 전원 RULE 저장/복원은 유지된다.
base world/pending/queue/RNG/authority/정책/기록 외에 제어기 종류·actor 배정·prompt/schema/config
identity·모의 응답 fixture·응답 위치·call 기록·요청 수/한도·상태·남은 활성화 순서를 저장한다.
callable/thread/Event/queue/Provider 연결/credential은 저장하지 않는다. 신뢰된 고정 factory가 재구성한다.
임의 provider class/import는 실행하지 않는다. 주입한 custom test Provider의 checkpoint는 지원하지 않는다.

모델 요청이 전송 중이거나 timeout 뒤 worker가 아직 끝나지 않은 불확정 상태는 저장/복원 거부다.
엔진 MOVE/WORK/REST pending과 별개다. 수신 후 미접수 READY는 응답과 결합 Observation을 저장하고
복원 후 Provider를 재호출하지 않는다. 다른 actor의 현재 tick 활성화도 중복시키지 않는다.
limit/error 저장본의 카운터와 정지는 그대로 유지한다. 실제 유료 통신의 강제 종료 복구는 구현하지 않았다.

replay_recorded는 보관한 입력/body/응답의 파싱 분류·후보·trusted binding·공개 결과를 확인한 뒤
기존 live-input replay로 실제 권한/엔진 기록을 재현한다. Provider는 호출하지 않는다.
mock의 반복 가능성과 실제 모델 응답 재현성은 구분한다.

## 지도와 검증

drawMap은 directed route마다 서로 다른 offset의 화살표와 자체 passable 상태를 표시한다.
square-shop만 닫힌 실제 closure에서 shop-square는 열린 화살표로 유지된다.
preview_phase4_maps.py의 양방향 폐쇄는 명시적 QA용 추가 system input이며 실제 closure 규칙을 바꾸지 않는다.
실제 브라우저에서 세 통행 조합, 대기 중 조회/정지, 저장 차단, READY 저장/복원/무재호출을 확인한다.
전체 offline gate와 실제 결과는 [Phase 4 보고서](PHASE4_REPORT_2026-10-15.md)를 따른다.

## 별도 승인이 필요한 실제 실행 사전 계획

목표 장면은 같은 5명 세계에서 Ada의 자기 욕구/식량/자금 관리다. 최대 가상24시간, 요청32회,
출력1024 tokens/회, 한 요청 timeout과 실패/반복 무효/한도/horizon 중단 조건을 고정한다.
기억은 현재 Observation만 사용하고 비교 실험이나 기억 효과 주장은 하지 않는다.
실제 실행 전 지원되는 모델 ID, reasoning 설정, structured output 호환성, timeout과 가격을 확인해야 한다.
현재 모델/실측 입력·출력·reasoning 토큰/단가/비용은 확인하지 않았고 금액을 산출하지 않았다.
실제 transport의 생활 schema 선택과 승인 gate를 검증한 뒤 새 명시적 유료 실행 승인이 필요하다.
과거 호출 승인이나 예산은 재사용하지 않는다. 이번 작업에는 실제 LLM/API 호출과 key 조회가 없다.
