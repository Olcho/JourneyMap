# Phase 4.1 · 생활 Provider 오류 경계

2026-10-15. Phase 4 독립 검토의 F1/F2를 수정한다. 생활 행동·경제·prompt/schema·지도는 유지한다.
이 문서는 [Phase 4 계약](PHASE4_OFFLINE_LLM.md)의 오류/timeout 부분에 대한 후속 계약이다.
Phase 1~4 계약과 완료 보고서는 당시 기록으로 보존한다. 실제 모델 실행을 승인하지 않는다.

## F1: 깊은 응답 JSON

`'[' * 1500 + '0' + ']' * 1500`은 3,001바이트라 응답 크기 제한 안에 있지만 Python JSON 파서가
RecursionError를 낼 수 있다. 공용 strict_json은 변경하지 않고 LifeDecisionService.resolve에서
이 예상 가능한 응답 오류를 JSON_DEPTH_EXCEEDED로 기록한다. 고정된 최대 깊이를 새로 선언하지는 않는다.

파싱 전에는 READY의 observation/ready를 비우지 않는다. 성공하면 PARSED/IDLE과 함께 비우고,
예상 실패면 공통 _fail 경계에서 call=ERROR, service=PAUSED_ERROR, ready/observation=None으로
전환한다. 원문은 call.raw의 안전한 Record에 그대로 보존하며 Observation ID도 기존 history와 연결된다.
자동 retry/fallback/행동 접수 없이 멈춘다. 조회·정지·안전 저장·복원과 원문 분류 replay를 지원한다.
공용 연구 parser/prompt/schema와 기존 후보 검증 의미는 변경하지 않는다.

## F2: 완료 시각으로 timeout 판정

LifeDecisionService는 단조 clock callable을 갖고 기본값은 time.monotonic이다.
MixedLifeSession의 trusted composition에서 `decision_clock=`으로 테스트 시계를 주입할 수 있다.
LiveLife의 배속/pump용 현실 시각 인자는 Provider deadline 시계로 사용하지 않는다.

- 시작 S: owner가 worker를 시작하기 직전에 같은 clock으로 측정한다.
- 완료 C: worker가 Provider 반환/실패와 identity 검증을 마치고 결과를 mailbox에 게시할 때 측정한다.
- 기한 D: S + timeout_seconds.
- C ≤ D이면 제시간 완료다. owner가 D 이후 늦게 poll하더라도 큐에 있는 이 결과는 READY가 될 수 있다.
- C > D이면 중간 poll 유무와 관계없이 TIMEOUT/PAUSED_ERROR이며 응답은 실행하지 않는다.
- 큐가 비었을 때 owner clock > D이면 timeout을 기록한다. clock = D에서는 아직 정지시키지 않아
  정각 완료를 허용한다. timeout 후 도착한 결과는 원문을 보존하고 discarded_late로 표시한다.

완료 시각 측정과 큐 publication, owner의 empty 확인/timeout 판정을 동일 mailbox lock으로
직렬화한다. 제시간 timestamp만 측정된 뒤 아직 큐에 넣지 않은 틈에서 owner가 timeout을 기록하는
경합을 막는다. 완료는 응답의 transport 반환 순간만이 아니라 최종 결과의 게시 경계라는 정의다.
worker는 완료 packet/시각 메타데이터만 만들며 call 상태·행동 접수·canonical 시간 변화는 owner만 수행한다.

_started_at/_completed_at, clock, lock, 큐, thread는 실행 중 메타데이터다. checkpoint payload와
LifeCall의 결정론적 기록에 넣지 않는다. 실제 서로 다른 clock origin에서도 정상 checkpoint가 같다.
pending/불확정 모델 요청은 계속 저장 거부이며 timeout 후 worker가 끝나 owner가 packet을 회수한
안전 경계에서 저장할 수 있다. late raw는 분석용으로 보존하지만 replay에서 실행 후보로 다시 파싱/접수하지 않는다.
replay는 TIMEOUT/ERROR/discarded_late와 action/candidate/receipt/result 부재를 검증한다.

## 검증과 실행

```powershell
Set-Location C:\Users\user\Documents\JourneyMap\.worktrees\phase1
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m pytest tests/test_life_error_boundaries.py tests/test_life_llm.py -q
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_ui --controller offline
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_mixed --until 5 --save artifacts/phase4_1/my-work.json
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_mixed --resume artifacts/phase4_1/my-work.json --save artifacts/phase4_1/my-final.json
```

공개 GamePort/행동 후보 계약은 그대로다. decider.begin(observation)/poll()은 내부 service API이며
deadline은 주입한 단조 clock만 사용한다. 예전 service.poll(now) 테스트는 clock 주입으로 바꿨고
기존 timeout/late 정지의 기대값은 유지했다. 새 오류 경계 검사는 sleep 없이 clock/gate로 제어한다.
Event.wait의 짧은 timeout은 worker 게시 완료를 확인하는 테스트 실패 상한이다.

실행 증거 helper는 `scripts/verify_phase4_1.py`와 `verify_phase4_1_boundaries.py`다.
현재 checkout의 Phase 2/4 기준선 artifact를 대조하며 새 output 경로를 사용한다.
지도 helper만 Node vm으로 실제 기존 drawMap을 실행한다. production Python 의존성을 추가하지 않는다.
checkpoint schema v2와 source compatibility hash 계약을 유지한다. 수정 전 저장본의 자동 migration은 없다.
정상 소스 hash가 달라져 checkpoint digest는 바뀌지만 같은 수정 소스의 연속/분할/복원은 같아야 한다.

결과와 검증 범위는 [완료 보고서](PHASE4_1_REPORT_2026-10-15.md)를 따른다.
실제 API/키 조회·유료 실행·commit/push/merge, 생활/기억/사회 기능 확장은 포함하지 않는다.
