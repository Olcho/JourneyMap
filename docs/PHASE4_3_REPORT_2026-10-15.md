# JourneyMap Phase 4.3 완료 보고서

2026-10-15. Phase 4.2 독립 검토의 지시에 따라 승인 후 추가 구조 변경 없이 사용할 수 있는
생활용 HTTP/credential/승인/journal/회계/engine/replay 경로를 구현하고 오프라인으로 검증했다.
실제 모델 실행 준비의 독립 통과 판정이나 유료 실행 승인을 대신하지 않는다.

작업 위치는 `C:\Users\user\Documents\JourneyMap\.worktrees\phase1`, 브랜치는
`codex/phase1-concurrent-runtime`, HEAD는 `97a5b6a24079b1f3bf3afb45bac85d30c1468336`이다.
기존 Phase1~4.2 미커밋 작업을 이어 갔고 reset/checkout/commit/push/merge를 하지 않았다.

## 구현

- `adapters/life_admission.py`: 인간 승인 선언의 plan/source/protocol/run/registry 결합,
  SQLite FULL journal, run_id/approval_id 단일 소비, 종료·불확정 실행 재사용 차단.
- `adapters/life_http.py`: 실제 live identity의 별도 생활 HTTP adapter, 승인 이후 credential loader,
  고정 HTTPS/생활 schema/크기·timeout 제한, reported usage와 조건부 가격/불확실 회계 구분.
- `experiments/paid_life.py`: 고정 seed42/Ada/24h composition, owner의 분류·receipt·completion 기록,
  timeout 후 world 동결과 late 회계 회수, custom/live checkpoint 금지.
- `experiments/paid_replay.py`: 저장된 inputs/events/results/execution·journal/raw를 data-only로 검증.
- `examples/life_paid.py`: 기본 제안 출력, 별도 승인 후 --execute, 무재호출 --replay.
- 기존 `life_decisions.py`는 approved concrete Provider와 단조 deadline 전달만 확장했고,
  `mixed_life.py`는 replay request/body hook과 일반 MixedSession의 live 우회 거부를 추가했다.
  기존 F1/F2 판정, offline 기본값과 규칙 생활 로직은 유지했다.
- 새 `tests/test_paid_life.py`, 검증/packaging helper, [상세 계약](PHASE4_3_PAID_PATH.md)과 관련 문서.

공용 strict_json, 기존 연구 OpenAIProvider와 연구 adapter/prompt/schema, 생활 prompt/schema와
fixture, 도메인/엔진, 지도 HTML 및 기존 테스트는 변경하지 않았다. 새 production 의존성은 없다.
Phase4.2 recording transport의 타입 제한이나 기대값을 무력화하지 않았다.
새 provider의 kind=live는 구현의 capability다. 아래 실행은 가짜 connection을 주입한 모의 증거다.

## 승인과 영속 전송

기본 명령은 제안만 출력하고 key/network/journal을 만들지 않는다. 유효한 승인 선언과 정확한
결합이 있어야 run을 claim한다. 제안 digest와 key 존재는 승인으로 취급하지 않는다.
run과 approval UUID는 영속 unique이며 재시작해 counter를 초기화할 수 없다.
전송 전에 sequence/request digest/예약을 PREPARED_NOT_SENT로 commit하고, 안전한 전체
request/body를 ATTEMPTED로 commit한 뒤 connection을 한 번 생성한다. journal 실패면 보내지 않는다.

NOT_SENT, attempted, response 수신, usage 확인/가격 계산, owner timeout, late 폐기와
종료/불확정을 구분한다. 전송 후 저장 실패/부분 전송/프로세스 종료는 자동 재시도·재개하지 않는다.
FULL transaction과 새 프로세스 거부는 검증했지만 실제 전원 장애나 디스크 복구는 실험하지 않았다.
승인 파일은 신뢰된 로컬 operator의 선언이다. 인간을 암호학적으로 인증하거나 악의적인 registry
삭제를 막는 경계라고 주장하지 않는다. 실제 canonical registry와 유효 승인 파일은 만들지 않았다.

## 회계·timeout·비밀 보호

입력 전체 context와 출력 한도를 예약하는 기존 보수적 USD0.318432/회 방식을 유지했다.
reported usage는 별도 기록하고 model/tier가 승인값과 맞을 때만 단가로 정산한다.
독립 검토의 wrong model/priority 반례에서 cost_state=UNCERTAIN, 해당 요청 known 금액0,
reserved_cost=318,432,000 nano-USD와 reported tokens를 유지했다. 원래 예약을 미지 모델/tier의
비용 보장 상한으로 표시하지 않는다. unknown/invalid/excess usage도 추가 전송을 차단한다.
reasoning은 output에 중복 합산하지 않는다. rate-card 금액은 실제 청구서가 아니다.

F2의 정각 허용·완료 후 deadline 판정을 유지한다. timeout 전 아직 보내지 않은 요청은
전송 전에 막는다. 이미 시도한 요청의 완성된 late 응답은 행동으로 실행하지 않고 usage를 회수한다.
owner가 닫힌 뒤 반환한 worker도 journal에 결과를 남긴다. CLI의 bounded drain 뒤에도 미회수면
RUN_UNCERTAIN을 유지한다. 외부 취소·미과금이나 강제 thread 종료를 주장하지 않는다.

합성 비밀을 정상 text, 중첩 metadata, Unicode escape, malformed envelope, headers,
전송/credential 예외와 부분 전송에 넣고 raw/journal/controller export에 없음을 확인했다.
HTTP error body/headers와 예외 세부 문자열은 기록하지 않는다. secret echo는 행동에 전달하지 않는다.
malformed/deep envelope는 비밀 없음이 입증되지 않아 raw를 숨기고 byte/hash와 reason을 남긴다.
이는 범용 임의 인코딩 DLP나 OS memory 보호의 증거가 아니다.

## 최종 검증

Python3.12.10, worktree/src의 명시적 PYTHONPATH를 사용했다.

| 검증 | 최종 결과 |
| --- | --- |
| 전체 pytest | **1,321 passed, 297.91초**, 실패/오류/건너뛰기0. full-final.xml/pytest-final.log |
| paid + Phase4/4.1/4.2 | **113 passed, 36.81초**. targeted-final.xml/log |
| 신규 paid tests | 48개. 실제 key/socket/DNS 차단, 합성 승인·key·fake connection만 사용 |
| 정적 검사 | Ruff check/format, strict mypy166 files, git diff --check 통과 로그 |
| 승인/한도/journal | 부재·결합 불일치·소비 ID·한도·전송 전 저장 실패에서 send 차단 |
| 불확정 장애 | 응답 저장 실패/부분 전송/HTTP 오류/invalid usage에서 예약 유지·후속 전송 차단 |
| F1/F2 | 기존 회귀 유지. paid 완료219.999/220/220.001과 timeout-first/late 회수 확인 |
| 정상 fake HTTPS | 요청12, tick24, 사건171, 진행 중3, Phase4.2 life_report 전체와 동일 |
| Ada fallback 금지 | Ada RULE decide를 막아도 정상 완료 |
| replay | Provider.generate/socket/DNS/key를 차단하고 memory 및 data-only replay 성공 |
| 새 프로세스 | 소비 run을 다른 프로세스에서 재사용하려 해도 credential/connection 전에 거부 |
| 비밀 echo | safe allowlisted error, raw/journal/export에 합성 비밀과 예외 private detail 없음 |
| 기존 READY/checkpoint/연구 adapter | 기존 테스트를 그대로 유지하고 전체 suite 통과 |

전체1,273개를 유지하고48개를 추가했다. 개발 중 테스트 import/타입, replay의 기록된 system
schedule hydration 등을 수정했다. 최종 runtime을 고정한 뒤 위 전체 검사를 다시 실행했다.
시간 경계는 주입 clock/gate를 사용하며 sleep으로 맞추지 않는다. Event.wait 상한은 테스트 실패 경계다.

실행 증거: final/normal.json, replayable-normal.json, pricing-uncertainty.json,
late-recovery.json, execution-proposal.json, verification-final.log, replay-final.log,
restart-blocked.json 및 restart-check.py. 검증 helper는 기존 dev pytest가 필요하며 production
의존성을 추가하지 않는다. 합성 token/금액은 계산기 입력이며 실제 모델 사용량·예상 비용이 아니다.

## 보존·재구성과 ZIP

시작 소스203개를 별도 ZIP/SHA-256으로 보존했다. 기존194개는 바이트 동일하다.
변경된 기존9개는 `.gitignore`, README, 문서5개와 life_decisions/mixed_life 두 연결 코드다.
이전 Phase4/4.1/4.2 ZIP도 기존 sidecar hash와 동일하다. preservation.json을 따른다.
과거870개 또는74,942개 파일, 여섯 RULE 시나리오와 브라우저를 이번에 다시 검사했다고
주장하지 않는다. 기존 engine/지도 보존과 전체 회귀/혼합 결과를 확인했다.

새 ZIP: `artifacts/phase4_3/JourneyMap_Phase4_3_review_2026-10-15.zip`.
전체 현재 source/test/doc/config, 시작 스냅샷, HEAD_TO_FINAL.patch, Phase4.3 파일별 변경,
보고서·계약·JUnit·정적 검사·실행 증거와 무결성 manifest를 포함한다.
HEAD archive + tracked diff + 새 파일 + 명시적 line-ending 복구로 최종 소스 바이트를 재구성한다.
재구성된 src를 사용하는 새 프로세스에서 Provider/key/socket/DNS를 막고 저장된 정상 보고서를
data-only replay한다. reconstruction-replay.json으로 결과를 기록한다.
MANIFEST.json은 자신을 제외한 payload의 크기/SHA-256, sidecar는 ZIP 전체 hash다.
실 registry, 승인 파일, credential, venv/cache/Git 내부, 옛 연구 raw 원본은 포함하지 않는다.

## 미실행 명령과 남은 한계

상세 결합값과 안전한 제안/replay 명령은 [실행 계약](PHASE4_3_PAID_PATH.md)에 있다.
아래는 **독립 검토 및 별도 인간 승인 후에만** 사용할 정확한 명령이며 실행하지 않았다.

```powershell
Set-Location C:\Users\user\Documents\JourneyMap\.worktrees\phase1
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
& C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe -m journeymap.examples.life_paid --execute --run-id a7b8f57e-9633-40f8-b8f3-6f9c1df0609f --approval artifacts/phase4_3/approval-human.json
```

approval-human.json은 존재하지 않는다. source/가격/계정 접근 조건이 바뀌면 이 제안을 다시 검토한다.
실 API schema 접수, 실제 모델 접근/행동, live TLS·credential·청구서, 실제 process/power-loss 복구는
미검증이다. live checkpoint와 자동 crash recovery는 없다. 데이터 replay는 engine/원문/연결
검사이며 외부 청구서 인증이나 인간 승인 진본 서명이 아니다.

실제 외부 LLM/API 호출 **0회**, 실제 key 조회 **0회**, 유료 실행 **0회**,
commit/push/merge **각0회**, subagent **0개**다. 모의 correctness를 모델의 판단·생존·기억 효과나
연구 construct validity 증거로 해석하지 않는다.
