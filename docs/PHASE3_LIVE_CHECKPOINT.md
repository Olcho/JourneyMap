# Phase 3 · 로컬 관찰 UI와 LifeSession 체크포인트

2026-10-15. Phase 2 독립 검토의 다음 단계 권고를 현재 미커밋 Phase 1+2 위에 구현한다.
범위는 같은 5명, 동일 life-loop-1 정책/경제, 최대 168시간이다.

## 실행

개발 작업 위치는 `.worktrees/phase1`이다. PowerShell 예:

```powershell
Set-Location C:\Users\user\Documents\JourneyMap\.worktrees\phase1
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_ui
```

브라우저에서 `http://127.0.0.1:8765`를 연다. 처음에는 정지 상태다.
시작/정지/한 시간 진행, 0.5×/1×/4×/16×, 저장/복원을 제공한다.
1×는 초당 최대 한 tick이다. 느린 호스트에서 밀린 tick을 건너뛰지 않는다.
`--closure`는 2시간의 square→workshop 경로 폐쇄 조건이다.
`--checkpoint <path>`는 저장 대상, `--resume <path>`는 새 프로세스 시작 시 직접 복원이다.
종료는 터미널 Ctrl+C이며 UI는 외부에 배포하지 않는다.

```powershell
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_checkpoint --until 8 --save artifacts/phase3/work.json
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_checkpoint --resume artifacts/phase3/work.json --until 168 --save artifacts/phase3/resumed.json
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_ui --resume artifacts/phase3/resumed.json
```

## 시간과 조회 계약

LifeSession만 canonical 시간 진행을 소유한다. LiveLife는 각 driver 경계에서 run_until(now+1)을
호출한다. HTTPServer와 pump는 같은 소유 thread에서 직렬 실행된다. 배속은 다음 tick의 현실
시간 deadline만 바꾼다. 연속/분할/단계 실행이 같은 tick에 도달하면 inputs까지 같도록 이미
처리한 경계의 advance를 중복 기록하지 않는다. Phase 2의 연속 실행 결과는 유지된다.

연구자 보기는 전체 위치/이동/행동/욕구/자원/선택 근거와 전역 Event를 표시한다.
인물 보기는 기존 trusted perception→contributor pipeline의 읽기 전용 미리보기,
실제로 정책에 전달된 마지막 Observation, 본인 선택과 접수 결과를 표시한다.
미리보기는 ObservationHistory.generate나 Controller.decide를 호출하지 않는다.
실제 Observation의 tick/ID를 별도로 표시한다. 인물 보기에 다른 인물의 전역 상태/선택/Event를
실어 보내지 않는다. 이 선택기는 로컬 연구자용 inspection UI이며 사용자별 권한 서버는 아니다.
LIVE_SESSION은 현재 실행 인스턴스이며 완료 로그 playback은 구현하지 않았다.
horizon에서는 시작/단계를 차단하고 HORIZON과 pending 보존을 표시한다. 168시간 이후 실행이나
욕구 예약 자동 연장은 제공하지 않는다.

## 직접 저장·복원 계약

`journeymap-life-checkpoint-1`은 UTF-8 JSON envelope와 payload SHA-256이다.
trusted composition adapter만 사용하고 Controller/GamePort에는 주입하지 않는다.
pickle, 파일 지정 import, 임의 클래스 생성, 과거 입력 replay를 사용하지 않는다.
허용된 record/enum만 해독하고 factory로 재구성한 서비스에 검증된 데이터를 주입한다.
boot는 persistence 연결/모듈 의존성만 초기화하며 행동을 실행하지 않는다.
복원 생성에서는 needs/closure 재예약도 생략한다.

저장 대상:

- LifeConfig/RunManifest, clock/lifecycle, transition/event sequence, RNG state/draw_count.
- domain world, in-flight 원 요청/timing/absence, admission fingerprint/cache,
  tie priority cursor, 완료 batch 경계, scheduler heap과 다음 insertion sequence.
- events/results/system outcomes/journal/commands, application requests/receipts/intents/attempts.
- actor-owned KnowledgeLedger, ObservationHistory와 ID index, 다음 sequence의 기반 기록.
- 정책 blocked routes/seen receipt/BUY·WORK retry time/전체 선택 기록.
- LifeSession inputs/observations, driver cursor, sampled ticks, daily/risk/max-need 회계 위치.

callbacks, registry callable, EventBus subscriber, DB 연결은 저장하지 않고 factory가 재구성한다.
이 v1은 고정 LifeSession 조립만 지원하며 custom contributor/subscriber는 지원하지 않는다.
source 전체 Python SHA(줄바꿈 정규화)와 module version이 다르면 fail closed한다.
자동 schema/code migration은 없다.

handler/application/Observation 생성 중, indeterminate request 또는 중단된 완료 batch에서는
저장을 거부한다. UI 저장은 먼저 정지하고 성공한 driver 경계에서 실행한다. 이동/작업/휴식 pending은
지원하며 duration/quote를 재계산하지 않는다. 새 process도 처음부터 재실행하지 않는다.
digest/format/record shape/type, run/actor/sequence/reference/cache/scheduler/pending 경계를
확인한 후 복원 세션을 노출한다. SHA는 손상 감지이며 서명/인증이 아니다. 로컬 신뢰 파일용이며
악의적으로 재작성된 모든 경제/의미 관계를 증명하는 범용 검증기는 아니다.

저장은 같은 directory의 임시 파일에 flush/fsync 후 os.replace한다. 교체 실패 시 이전 파일을
보존하고 임시 파일을 정리한다. 기록량과 파일/메모리 크기가 함께 증가하는 최대 7일 기능이다.
별도 append log에 덧붙이지 않고 내부 archive와 cursor를 이어 사용한다. 재시도는 cache를
사용하여 effects/events/results를 다시 만들지 않는다. 명시적 재시도 자체는 새로운 attempt다.

## 검증

tests/test_life_checkpoint.py는 MOVE/WORK/REST pending 저장→원 세션 종료→새 Python process
재개→168시간 전체 checkpoint/inputs/decisions/report/engine equality를 검사한다.
폐쇄 학습/부족 재시도/RNG, ID 충돌/완료 전후 재시도 무효과, speed/polling 무영향,
horizon, 원격 private truth 반례, 재봉인 손상 및 atomic write 실패를 검증한다.
기존 Phase 1/2와 연구 baseline 전체 offline gate를 유지한다.
correctness 증거이며 실제 LLM 효과/장기 경제/일반적 공정성 증거는 아니다.
