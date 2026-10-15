# JourneyMap Phase 4 완료 보고서

2026-10-15. 생활 세계의 선택적 Provider 연결을 **실제 API 없는 오프라인 통합**으로 구현·검증했다.
모의 응답의 동작 검증이며 실제 LLM 인물이 생활에 성공했다는 결과가 아니다.

## 기준선과 변경

작업 공간은 `C:\Users\user\Documents\JourneyMap\.worktrees\phase1`, branch는
`codex/phase1-concurrent-runtime`, HEAD는 `97a5b6a24079b1f3bf3afb45bac85d30c1468336`이다.
미커밋 Phase 1+2+3 전체 181개 source/config/test/doc를 Phase 4 시작 ZIP과 해시 목록으로 보존했다.
원래 저장소의 Python 3.12.10 venv를 사용하고 PYTHONPATH를 이 작업 트리 src로 고정했다.

먼저 drawMap의 양방향 병합을 제거했다. 각 directed route마다 별도 offset 화살표와 자체 통행
상태를 표시하므로 square-shop 폐쇄가 shop-square의 표시를 닫지 않는다. 엔진 규칙은 유지했다.

기존 ProviderRequest/RawModelResponse/identity/failure, strict JSON과 trusted binding,
payload validator, 실제 concurrent GamePort/engine을 재사용했다. 기존 LLMController/연구 runner,
기본 prompt/schema/wire를 변경하지 않았다. OpenAI body builder에는 기본 결과를 유지하는
명시적 format_override만 추가했고 통신 구현을 복사하지 않았다.

새 생활 adapter는 versioned prompt/schema와 MOVE/WAIT/REST/BUY/CONSUME/WORK를 지원한다.
기본 UI/기존 life CLI는 전원 규칙이다. `life_ui --controller offline` 또는 별도 `life_mixed` 예제로
Ada만 모의 Provider에 연결한다. 다른 네 인물은 기존 규칙 정책이다.
현재 Observation만 입력하며 규칙 blocked/retry memory나 researcher truth를 주지 않는다.
worker는 Provider 요청만 처리하고 canonical 접수/시간 진행은 owner thread만 수행한다.
응답 대기 중 세계가 멈추며 조회/정지는 가능하다. 오류·한도·late 응답을 분리하고 fallback/retry하지 않는다.

checkpoint v2는 controller 배정/config identity/응답 위치/카운터/기록/남은 actor 활성화를 함께
직접 복원한다. 전송 중/불확정 외부 요청은 저장·복원 거부한다. 수신 후 미접수 READY는 안전 저장과
무재호출 재개를 지원한다. 현 source의 전원 규칙 저장 계약도 유지하고 과거 source/v1 migration은 없다.

주요 변경 파일:

- 신규 adapters/life_llm.py, experiments/life_decisions.py, experiments/mixed_life.py,
  examples/life_mixed.py, tests/test_life_llm.py.
- 기존 openai_provider.py의 opt-in body 형식, life.py의 동일 sampling helper 추출,
  live.py/checkpoint.py/life_ui.py·html의 선택 모드·대기·조회·복원·방향별 지도.
- 실행·검증·패키징 script, Phase 4 계약/보고서 및 README/API/architecture/ERD/test strategy/roadmap.
- 정확한 시작/최종 파일 크기·해시와 전용 변경 목록은 검토 ZIP의 PHASE4_CHANGES.json에 있다.

## 실제 검증 결과

| 검사 | 결과 |
|---|---|
| 최종 전체 offline pytest | **1,242 passed**, 187.14초, 실패/오류/skip 0 |
| 최종 Phase 4 반례/통합 검사 | **34 passed**, 7.01초 |
| 사전 LLM + Phase 3 checkpoint 회귀 | **52 passed**, 75.36초 |
| 기존 life/concurrent/architecture 회귀 | **80 passed**, 18.79초 |
| Ruff check / format | 통과 / 도구 출력 187 files already formatted |
| strict mypy | 156 source/test 파일 통과 |
| git diff --check | 통과 |

기존 1,208개 검사를 삭제/완화하지 않고 34개를 추가했다. 사전52개 검사 이후 응답 replay 분류
검증과 두 반례를 추가했고 최종34개 및 전체1,242개를 실행했다. 전체 gate 이후 runtime Python은
변경하지 않았다. UI 상태 안내 문구는 브라우저에서 최종 확인했다. 문서/패키징 후 전체 gate를 반복하지 않았다.
새 검사에서는 socket/DNS와 실제 OPENAI_API_KEY 조회를 차단했다. 기존 연구 transport 테스트의
모의 자격증명/모의 통신은 실제 key 조회·외부 호출과 구분한다.

### 전원 규칙 회귀

normal/stockout/no-money/no-materials/no-wages/closure를 다시 각각168시간 실행했다.
summary/execution/events/results/attempts/inputs/decisions가 기존 Phase 2 출력과 구조로 일치했다.
제외한 값은 summary의 elapsed_seconds와 simulation_elapsed_seconds 두 현실 시간 필드뿐이다.
명시적 포장 metadata로 의미 차이를 숨기지 않았다. 여섯 조건 모두 engine replay와 live-input replay가 통과했다.
정상 조건의 화폐256/원료129/빵90, 부족 조건의 정체와 위험도 유지됐다.
현 source의 RULE MOVE/WORK/REST checkpoint를 새 process에서168시간까지 재개한 동등성도 확인했다.

### 혼합 예제의 실제 기록

literal-life-24h-1은 명시적인 모의 응답 배열이다. 관찰 기반 규칙 생성이나 실제 모델 응답이 아니다.
24시간에 요청12/상한32회, Event171개, application Observation/attempt 각64개다.
Ada의 모의 후보가 실제 engine에서 MOVE6, BUY2, WORK2, CONSUME1, REST1로 완료됐다.
임금20을 받았고 같은 세계의 나머지 네 인물도 규칙 정책으로 진행했다.
이 예제에서 화폐256 유지, 원료160−8=152, 빵8+64−18=54를 확인했다.
정답 응답 fixture의 결과이므로 실제 LLM 능력/경제 안정성/정책 공정성 평가가 아니다.

모든 요청에는 outgoing body/실제 모의 raw 응답/파싱 후보/trusted ActionRequest/접수 및 완료
공개 결과를 구분해 기록했다. 기록된 응답의 분류·binding·receipt와 application 입력 replay를
Provider 재호출 없이 확인했다. raw 응답을 바꾼 반례도 거부됐다.
같은 자기 Observation에서 MOVE와 WAIT 두 유효 후보가 실제로 다른 행동/위치를 만들었다.
Ada의 LifeController.decide를 차단해도 연결이 작동했으므로 규칙 정책이 결과를 대신 정하지 않는다.

혼합 MOVE2시간/WORK5시간/REST13시간 pending에서 저장→원 세션 종료→새 Python process
복원→24시간 실행이 연속 실행의 전체 checkpoint와 inputs에 일치했다.
별도 CLI 증거의 WORK 저장본도 새 process에서 동일하게 요청12회로 이어졌다.
최종 mixed checkpoint digest는
`b7dc3e0e28dff83d8cd042086e33a986cc498fa4e9c028079493c98197ceed2c`다.
한도1 예제는 다음 판단 기회인1시간에 LIMIT_REACHED, 요청1회를 보존하며 추가 전송하지 않았다.

### 실패·정보·시간 반례

malformed/중복 key/추가 authority/boolean 수량·duration/job/비유한 값/남의 정보 조회 후보를
거부했다. 존재하지 않는 route/job은 파싱 유효 후 엔진 REJECTED이며 세 번 반복 시 제한 정지했다.
closure의 완료 FAILED/ROUTE_IMPASSABLE은 Provider 오류와 구분됐다.
refusal/incomplete/input·output size/transport/HTTP/timeout 원인을 구분하고 자동 수리·retry·대체하지 않았다.
timeout 검사는 제어된 clock으로 수행하여 실제 긴 sleep을 사용하지 않았다.

원격 employer money/flour와 미래 closure 입력만 다른 두 세계에서 정보 획득 전 Ada의 실제
recording transport outgoing body가 같았다. body의 생활 schema에는 WORK가 있고 기존 schema에는 없다.
request limit, 규칙 blocked/retry 상태, 이전 응답 ID, 부수 metadata가 outgoing에 섞이지 않았다.
future/타인 private/연구자 정보의 일반적 무누출 증명을 주장하지 않으며 실제 수행한 통제 반례다.

제어 가능한 지연 중 세계/시각/Observation 수가 동결됐고 polling/배속/중복 시작으로 요청이 늘지 않았다.
정지 후 응답은 READY로 보관되며 explicit 재개 전 접수하지 않았다. late timeout/종료 답은 폐기했다.
전송 중 저장·복원 거부, READY 저장·복원 무재호출, 카운터/fixture 위치/요청·Observation ID와
다른 actor의 활성화 중복 방지, 한도 저장본의 정지 보존을 확인했다.

### 실제 브라우저 검증

Codex in-app browser에서 최종 로컬 UI를 조작했다. 대기0시간/요청1회에서 조회와 정지가 가능했고
저장 버튼이 차단됐다. 정지 뒤 모의 응답을 전달하면 DECISION_READY_PAUSED였다.
그 상태를 저장하고1시간 진행한 뒤0시간으로 복원하여 다시 접수해도 요청은1회였다.
최종 자동 모의 UI는16×로24시간/HORIZON, 요청12/32, 추가 단계 비활성화를 표시했다.
제어기 RULE/LLM_ADAPTER_OFFLINE과 입력/응답/후보/결과 기록을 확인했다. console error는0개였다.

지도는 각8개 directed 화살표의 DOM 상태와 screenshot을 확인했다.
양방향 통행은 두 open 화살표, 단방향 폐쇄는 square-shop만 closed/shop-square는 open,
양방향 폐쇄는 두 closed 화살표였다. 마지막은 명시적 추가 CloseRoute 입력을 가진 QA fixture다.
실제 closure의 엔진 규칙을 역방향 폐쇄로 바꾸지 않았다. 모바일 재설계/실기기 검증은 하지 않았다.

## 보존과 산출물

보호 manifest의870개 파일을 실제 읽기 전용으로 대조하여 변경/누락0개다.
Phase 1/2/3 계약·보고서와 기존 concurrent/life/checkpoint 테스트는 시작 ZIP과 byte-identical이다.
기존 Phase 2 ZIP SHA `63c307cf3e5e5ca07e334501f32aff50fbd2b1bfd6e4d92096b9593acbd1dbba`,
Phase 3 ZIP SHA `427af3f2ac6a2e87b2cb1f5dbd4b0e4578a88681822c96f25d3c03db77cb98db`도 유지됐다.
전체74,942파일 재감사를 반복하지 않았다. E03/E04 작업 트리/원본과 다른 프로젝트에 쓰지 않았다.

- 실행/계약/실제 실행 사전 계획: docs/PHASE4_OFFLINE_LLM.md
- 검토 ZIP: artifacts/phase4/JourneyMap_Phase4_review_2026-10-15.zip 및 .zip.sha256
- 검사/보존: artifacts/phase4의 JUnit·로그·protection-summary.json
- 실행 증거: final의 여섯168시간 export, RULE 재개, mixed/limited 입력·응답·엔진 기록과 체크포인트
- 화면: ui-waiting.jpg, ui-ada.jpg, ui-mixed-final.jpg, map 세 경우 및 UI/지도 검증 JSON

검토 ZIP은 완전한 최종 source/config/test/doc, HEAD 대비 tracked patch와 모든 신규 source,
Phase 4 시작 ZIP/변경 해시, 핵심 실행/저장/검사/화면 증거를 담는다.
HEAD archive+patch+신규 source로 재구성 후 byte 동일성을 확인하고 manifest는 자기 자신을 제외한
payload의 크기/SHA-256을 기록한다. 최종 ZIP SHA는 별도 sidecar다. 비밀값/venv/환경 전체/원본 연구 raw는 제외한다.

## 한계와 남은 실제 모델 검증

모의 Provider의 배선·권한·기록·시간·저장 correctness를 확인했다. 실제 모델의 후보 품질,
생활 행동, 토큰 사용량/비용/지연은 검증하지 않았다. RULE은 과거 blocked/retry memory를 가지며
이번 adapter는 현재 Observation만 사용하므로 공정한 성능 비교를 설계했다고 주장하지 않는다.
실제 유료 요청의 불확정 강제 종료 복구, 장기 기억/망각/새 경제/인구 확대/무한 실행/E05는 범위 밖이다.
실제 모델/가격/토큰 설정 확인과 생활 schema의 승인된 실제 transport 검증, 새로운 명시적 실행 승인이 남아 있다.
구체적인24시간/32요청/1024출력 상한과 중단 계획은 계약 문서에 남겼으며 금액을 추정하지 않았다.

**실제 LLM/API 호출 0, 실제 API key·자격증명 조회 0, 유료 실행 0, commit/push/merge 0.**
localhost UI 통신은 실제 모델 통신과 구분한다. 추가 사회/경제/기억 기능으로 확장하지 않았다.
