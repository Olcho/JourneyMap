# Experiment 04 live 준비 완료 보고서 — 2026-10-09 KST

오프라인 live 준비 구현, mock 실행 및 전체 회귀를 완료했다. **실제 API 호출 0회, 키 조회 0회**다.
유료 실행 승인 파일/값을 생성하지 않았고 commit/push/merge를 하지 않았다.
live 계획은 DRAFT/UNAUTHORIZED다. 모델별 tokenizer와 schema/서비스 overhead의 신뢰할 수 있는
사전 token 상한 근거가 없어 LIVE READY를 발급하지 않았다. 승인 JSON만 추가해 실행할 수 없다.

## 기준 및 보존

작업: `C:\Users\<user>\.codex\worktrees\experiment03-live-preparation\JourneyMap`.
브랜치 `codex/experiment03-live-preparation`, HEAD `557fb3df10149d7b7738137b7ac1038dfba24e84` 유지.
시작 normalized working source는 지시문 기준 `79c3aac38bed65735b644427e4c1dd1c6fd3e2cb45abf6f0773a7ac68e03dc86`와 일치했다.
최종 normalized source는 `abc4a0614d21aac928e4f0204f9e7c885abc129e0845a345b43054c994c5d650`다.
raw source SHA-256은 `9c3f0c63ec7a4578593ff83ee991fd2b4b5efbac40029e2c21946bc1cbe69c67`다.
source identity는 src/journeymap Python와 pyproject를 포함하며 테스트/docs/자료 자체는 ZIP manifest로 식별한다.

시작 시 기존 변경 파일41개를 before-code에 복사하고 자료8,667개의 해시를 저장했다.
허용된 기존 문서4개의 append를 제외한8,663개는 그대로 유지했다. 이전 E03/E04 실행 소스와
기록, 이전 ZIP 및 primary checkout은 변경하지 않았다. 문서 원문도 before-code에 보존했다.
이번에 기존 문서4개를 갱신하고 새 파일10개를 추가했다. 전체 WIP51개 중 신규 Git 미추적45개다.
전체 tracked HEAD diff만으로 새 파일이 포함되지 않으므로 변경 원문 전체도 ZIP에 넣었다.

## 구현 내용

- `adapters/route_update_live.py`: E04 전용 config/wire, exact candidate 설정, E04 provenance decode,
  단일 fixed-endpoint HTTP transport 및 같은 bytes 경계의 비활성 mock. 실제 HTTP 경로는 미실행이다.
- `experiments/route_update_live_plan.py`: smoke/pilot 분리, 6순열 고정 배정, source/runtime/profile/schema/config
  결합, 별도 readiness/authorization, 수동 DRAFT/해시 수정 거부와 계수 미완료 gate.
- `experiments/route_update_live_cost.py`: Decimal 사전 최대 비용 예약, 호출/입력/금액 차단,
  모든 예약 유지, usage 기반 보수적 추정과 미확정 예약 분리. live 계수 증거를 조작할 수 없다.
- `experiments/route_update_live_session.py`: 기존 Session의 application/관찰/archive/engine boundary 재사용,
  별도 live/mock response 검증. 기존 fake-only finish와 semantic/v2 profile은 변경하지 않았다.
- `experiments/route_update_live.py`: 초기 선택 실제 제출과 새 권한 prefix 재생, 세 첫 입력 선검사,
  배정 순서 실행, 원문 bytes/journal/export, 전체/분기 중단, claim에 의한 같은 plan 재실행 거부,
  A/B 층과 null 포함 대응표·분모·계획/실제 순서.
- `experiments/route_update_live_audit.py`: Provider/key/network/Git lookup 없는 새 application 재생.
  원문 wire/response/receipt/Events/position/metrics/비용/예약과 lifecycle·배정·export를 대조한다.
- `examples/route_update_live.py`: plan/mock/audit/inspect와 별도 gate로 막힌 live/smoke CLI.
- `tests/test_route_update_live.py`: 신규72개 회귀. 기존 문서 README/API/ARCHITECTURE/TEST_STRATEGY append,
  새 계약 `EXPERIMENT04_LIVE_PREPARATION.md`와 이 보고서.

6prefix는 독립 초기 시도이며 18분기/114호출을 독립 표본으로 해석하지 않는다.
③이 첫 순서여도 세 첫 입력 검사를 먼저 완료한다. 한 번 준비된 관찰을 다시 열지 않는다.
초기 실패가 있어도 번호별 순서를 유지하고 A가 나올 때까지 재호출하지 않는다.
정상 Engine 거부/유효 WAIT와 오답은 기회 내에서 진행한다. 도착 시 즉시 중단한다.
모델 응답 실패와 infrastructure/integrity/비용/미발송/unknown을 분리하고 첫 점수를 유지한다.

## 실제 실행한 검증

Python3.12.14, Ruff와 strict mypy, `git diff --check HEAD` 통과.
Ruff format: 195 files already formatted. mypy: 168 source files, 오류 없음.
전체 pytest: **1,463 passed in 223.69s**, 기존1,391+신규72. skip/failure/error0.
pytest 자식은 offline_cli.py로 소켓 연결을 막고 inherited 실제 키 값을 읽지 않은 채 합성 값으로 교체했다.
신규 테스트와 mock/감사 자식은 실제 키 조회 및 HTTP transport 생성을 금지했다.
기존 HTTP 테스트는 자체 비활성 mock만 사용한다. 개발 중 실패 로그도 보존하되 최종 gate와 구분했다.

최종 E04 HTTP mock:

| 묶음 | 초기 시도 | 분기 | mock send | audit |
|---|---:|---:|---:|---|
| smoke-final |1|0|1|INCLUDED|
| pilot-final |6|18|48|INCLUDED|

smoke를 pilot 초기 응답으로 재사용하지 않았으며 통계가 분리되어 있다.
pilot-final은 A3/B3인 제어된 스크립트다. A의③/② 첫 최적 MOVE 대응은③만true3쌍,
B는 조건별 D 방향 유지3/3이다. A②의 첫 실패 후 도착3건은 첫 점수를 false로 유지한다.
이 숫자는 **mock protocol correctness 증거**이며 모델 정보 활용이나 효과의 증거가 아니다.
①② 같은 첫 wire에 서로 다른 응답을 허용하는 테스트도 통과했다.

최대114회/분기6결정 테스트, 24tick 전 제출 차단, 유효 첫 점수 후 token cap 전체 중단,
초기 실패/no-A/번호 보존, refusal/malformed/일반 incomplete와 timeout/HTTP/identity/usage/tier,
발송 전 입력/금액/호출 차단, INTENT 기록 실패 때 미발송, RESULT 기록 실패 후 nullable final,
mock/live 혼합·미승인·수동 READY·source/config/schema/order 수정, raw 및 재봉인 journal 변조를 검증했다.

최종 failure 원본9묶음과 input-limit 미발송 원본을 별도로 보존했다.
response failure fixture는 분기 종료 후 예정된 초기 시도를 진행하며,
global error fixture는 첫 오류가 나온 두 번째 send에서 전체를 멈춘다.
통신/identity/usage/tier 오류의 미확정 예약0.07048USD를 지우지 않았다.
각 raw와 audit는 묶음별이며 본실험/과거 자료와 합산하지 않는다.

E04 offline v1 원본8묶음과 v2 원본2묶음을 Provider 없이 재감사하여 저장된 보고서와
정확히 동일함 및 원본 SHA-256 불변을 확인했다. recording/tamper의 기존 INCOMPLETE/EXCLUDED도 유지했다.
전체 회귀에는 기존 E03 smoke/live/sequential protocol/engine 검사가 포함된다.

## 계획·비용 및 미해결 실행 요건

`trials/experiment04-live-preparation-20261009`에 별도 draft-smoke-plan.json과 draft-pilot-plan.json,
candidate-config.json, model-and-pricing-review.json, cost-planning.json,
input-size-and-readiness.json을 생성했다. 전체 순서·설정·소스·runtime·digest가 포함되어 있다.
두 live plan은 DRAFT, authorization UNAUTHORIZED, approved_usd=null이다.
실제 live/smoke 명령을 실행하거나 키 존재 여부를 확인하지 않았다.

공식 모델/가격/Reasoning/Structured Outputs/Counting tokens 문서를2026-10-09KST 확인했다.
[모델](https://developers.openai.com/api/docs/models/gpt-6.1-sol) 및
[가격](https://developers.openai.com/api/docs/pricing)의 Standard 단가를 저장했다.
alias 사용이며 확인되지 않은 snapshot ID를 만들지 않았다. 2048은 충분함이 미검증인 후보다.
실제 계정 접근·E04 schema 수용·지역/특수 계약 적용 범위는 후속 승인 단계의 확인 사항이다.

합계 USD10은 PROPOSED이며 승인 금액이 아니다. 입력20,000token이 검증되고 출력2048 및
검토 단가가 적용된다는 조건에서 호출0.07048, pilot114회8.03472,
smoke와 합계115회8.10520USD다. 계수와 서비스 overhead 근거가 없어 무조건 비용 상한으로 표현하지 않는다.
최종 mock request48개의 bytes는4,931~14,905였다. 실제 token 수 또는 모든 입력 상한이 아니다.
[공식 계수 문서](https://developers.openai.com/api/docs/guides/token-counting)의 로컬 계수 한계에 비추어
모델별 tokenizer/버전 및 schema/envelope 추가 계수 근거가 아직 부족하다.
계수를 위한 실제 API도 호출하지 않았다. 이 부족분을 해결하는 후속 구현·검토 없이는 LIVE READY가 없다.

pilot mock ledger의 최대 예약은3.38304USD, 합성 usage 기반 보수적 추정은0.1680USD다.
smoke는 예약0.07048, 합성 usage 추정0.0035USD다. 실제 청구액/모델 token 측정이 아니며 서로 통계를 합산하지 않았다.
Ledger는 모든 발송의 최대 예약을 끝까지 유지하고 unknown charge를 별도 표시한다.
예약 및 SEND 기록은 과금 또는 실제 HTTP 도달 증명이 아니다.

## 실행 명령과 기록 위치

이 작업에서 사용한 Python은
`C:\Users\<user>\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`다.
PYTHONPATH는 worktree의 src와 기존 primary .venv/Lib/site-packages(개발 도구)였다.
새 의존성을 설치하거나 깨진 예전 .venv launcher를 바꾸지 않았다.

```powershell
python trials/experiment04-live-preparation-20261009/run_checks.py
python trials/experiment04-live-preparation-20261009/verify_offline.py
python trials/experiment04-live-preparation-20261009/final_check.py
python trials/experiment04-live-preparation-20261009/package_review.py
```

실제 CLI 원문 명령은 offline-gate.json/각 최종 로그에 들어 있다.
smoke/pilot의 mock, audit, inspect를 wrapper로 실행했고 audit 결과 동일성과 raw 불변을 확인했다.
static-gate.json, historical-gate.json, offline-gate.json, preservation-gate.json,
final-gate.json, 최종 JUnit/exit/log와 사용한 scripts를 보존했다.

## 검토 ZIP 및 감사 한계

검토 ZIP은 새 UTC timestamp 경로에 생성하여 기존 ZIP/원본을 덮어쓰지 않는다.
기준 HEAD/status/전체 tracked diff, 미추적 포함51개 변경 원문과 전체src/tests/docs,
계약/보고서/before-code/작업 patch, draft plan/config/비용·readiness,
mock 원문·실패 묶음·audit/inspect·검증 scripts/gate/log, 기존 v1/v2 감사 원본을 포함한다.
MANIFEST.json은 상대 경로·bytes·SHA-256을 기록하며 자신은 외부 verification에 hash를 남긴다.
새 압축 해제 폴더에서 모든 payload 및 manifest를 byte 단위로 비교하고,
원본·이전 자료·HEAD/status/diff 불변과 금지 경로/credential 패턴 제외를 확인한다.
검증된 최종 ZIP 경로·SHA 및 압축 해제 기록은 ZIP 옆 .verification.json/.zip.sha256에 있다.

ZIP은 standalone runner가 아니며 source_identity는 실제 Git checkout을 전제로 한다.
실행기/audit가 Session/metrics/plan·cost 계산을 공유하므로 독립 평가 알고리즘 증명이 아니다.
audit INCLUDED와 별도 source/runtime 및 ZIP 원본 검증을 구분한다.
키/인증 헤더 값/환경 덤프/.env/.venv/.git은 포함하지 않는다.
