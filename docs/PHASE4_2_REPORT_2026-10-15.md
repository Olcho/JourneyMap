# JourneyMap Phase 4.2 완료 보고서

2026-10-15. Phase 4.1 독립 검토의 후속 지시에 따라 생활용 Responses wire·응답 분류·usage·한도
처리를 오프라인에서 검증하고 [고정 실행 제안](PHASE4_2_LIVE_PREFLIGHT.md)을 준비했다.
실제 network adapter/admission 연결이나 유료 실행을 완료했다는 의미는 아니다.

작업은 `C:\Users\user\Documents\JourneyMap\.worktrees\phase1`,
`codex/phase1-concurrent-runtime`, HEAD `97a5b6a24079b1f3bf3afb45bac85d30c1468336`에서 수행했다.
기존 Phase 1~4.1 미커밋 구현을 그대로 유지했다. reset/checkout/commit/push/merge는 하지 않았다.
F1/F2는 독립 통과 기록으로 보존하며 재수정하지 않았다.

## 구현과 승인 제안

- 새 `adapters/life_responses.py`: 생활 schema를 사용하는 pinned Responses body, strict raw envelope
  분류, usage 정합성, 정수 nano-USD 예약/정산, terminal budget stop과 recording transport.
- 새 `examples/life_preflight.py`: fixture bridge를 실제 MixedLifeSession에 연결하는 오프라인 CLI.
  기존 offline envelope와 제안 wire body를 따로 보존하고 실제 모델로 표시하지 않는다.
- 새 `tests/test_life_responses.py`: 24개 검증. socket/DNS/실제 키 조회를 차단한 상태에서 실행.
- 새 실행 제안/보고서/packager와 README/API/ARCHITECTURE/ERD/TEST_STRATEGY/ROADMAP 링크를 갱신했다.
  production 의존성이나 생활 도메인 기능은 추가하지 않았다.

실행 제안은 `gpt-5.4-mini-2026-03-17 / high / service_tier=default`, seed42, Ada 한 명,
최대 가상24시간·전송32회·출력4096 tokens/회·입력500,000/output131,072 tokens 누적·USD1.00이다.
120초 timeout, 오류 즉시 정지, 자동 retry/fallback 없음, 기존 연속 엔진 무효3회 정지다.
출력 상한은 reasoning을 포함한다. 모델과 단가의 공식 문서 근거 및 계산 조건은 실행 제안에 있다.

계획 digest:
`89447928f2357efef57f0f9cbc9ce8a53beced16cdb7a5f6136301d34b95380d`

계획 digest나 모의 통과는 승인 token이 아니다. 개발 도구의 권장 Sol/High와 실험용 API 모델도
별개다. 실제 키 조회/전송을 선택할 수 있는 옵션은 추가하지 않았다.

## 사용량과 실패 처리

전송 전에 context 전체400,000 input + 최대4096 output에 해당하는 $0.318432를 보수적으로
예약한다. byte 수를 token 실측치로 사용하지 않는다. 누적 호출/token/비용 상한에 예약이
들어가지 않으면 transport를 호출하지 않는다. 잔여 예산이 작으면 실제 입력이 작아도 멈출 수 있다.

정상 usage는 input/output/total/cached/reasoning 정수, 합과 부분집합을 검증한 뒤 정산한다.
cached input만 할인하며 output_tokens에 이미 포함된 reasoning을 다시 더하지 않는다.
unknown usage/HTTP 오류/불확정 전송/usage 초과는 예약을 유지하고 다음 요청을 막는다.
유효 usage가 있는 refusal/incomplete/identity/tier 실패는 사용량 기록 후 중단한다.
raw와 wire error, budget state는 별도 증거에 남긴다. 전송 예외의 사적인 문자열은 기록하지 않는다.
현재 raw는 합성 데이터이며 실 credential redaction을 검증한 기록은 아니다.

Provider는 전송 메타데이터만 소유한다. 기존 owner만 행동을 접수하고 canonical 시간을 진행한다.
엔진 판단과 Provider failure는 구분하며 READY raw만 기존 생활 parser에 전달한다.
일반 잘못된 JSON과 깊은 후보 JSON은 기존 INVALID_CANDIDATE/JSON_DEPTH_EXCEEDED 경계에서 멈춘다.
custom Provider checkpoint는 계속 거부하며 새 live 저장/복원 계약을 만들지 않았다.

## 검증 결과

Python3.12.10과 명시적인 worktree/src PYTHONPATH를 사용했다.

| 검증 | 실제 결과 |
| --- | --- |
| 전체 pytest | **1,273 passed, 168.58초**, 실패/오류/건너뛰기0. full-final.xml, pytest-final.log |
| 신규 + Phase4/4.1 | **65 passed, 8.57초**. targeted.xml, targeted.log |
| 신규 사전 검증 | 24개, pinned body·schema, status/refusal/incomplete, identity/tier/ID, malformed/deep/duplicate envelope |
| accounting | reasoning 중복 합산 방지, 누적 한도·정확한 비용 경계·초과, unknown usage/불확정 전송 예약 유지 |
| 전송 전 차단 | 크기/호출/token/비용 한도, 잘못된 envelope/계획, kind=live 거부 |
| 실제 엔진 bridge | tick24, 요청12, 사건171, 진행 중 행동3, 기존 정상 life_report와 동일 |
| 규칙 대체 방지 | Ada의 LifeController.decide를 예외 함수로 막고 사전 검증 완료 |
| replay | Provider 무재호출, inputs/결과 일치, 응답12개 검증 |
| 기존 checkpoint/READY/오류 | 기존 테스트와 소스를 그대로 유지하고 전체 suite 통과 |
| Ruff check / format | 최종 통과 로그 ruff-check.log, ruff-format.log |
| strict mypy | 160 source files 통과. mypy.log |
| git diff --check | 통과. diff-check.log |

사전 검증 CLI 실행 결과는 preflight-final.json과 example-final.log다.
wire body/합성 raw/usage/회계 및 기존 controller 기록과 life_report를 모두 포함한다.
모의 token 값1000/100 등과 계산 금액은 테스트 입력이다. 실제 토큰·비용·성과의 추정치가 아니다.
첫 개발 검사에서는 pytest 예약 fixture 이름과 sandbox 기본 temp 경로를 바로잡았고 타입 검사도
수정했다. standard tier를 명시하기 위해 초기 전체 검사를 중단한 뒤 최종 소스로 전체를 다시 실행했다.
위 수치는 최종 성공 실행이며 개발 중 검사와 혼합하지 않았다.

## 보존과 배포 검증

시작 소스197개를 ZIP과 SHA-256으로 보존했다. 기존 파일의 변경은 `.gitignore`와 README,
API/ARCHITECTURE/ERD/ROADMAP/TEST_STRATEGY 문서 일곱 개뿐이다.
이전 모든 Python 소스·테스트, 공용 parser·OpenAIProvider·연구 adapter·생활 prompt/schema·fixture,
지도 HTML과 Phase1~4.1 계약/보고서는 바이트 동일하다. preservation.json에 결과를 기록했다.
Phase4와 Phase4.1 검토 ZIP의 SHA-256도 각각 기존 sidecar와 같다.

보호 대상870개 Windows 파일 전체와 74,942개 과거 파일의 검사를 이번에 반복했다고 주장하지
않는다. 이번 보존 검사는 시작 소스197개와 두 이전 ZIP이다. 여섯 RULE 시나리오와 브라우저
화면을 별도로 반복하지 않았다. 기존 엔진/지도 불변과 전체 회귀 suite, 혼합 결과를 확인했다.

새 검토 ZIP은 `artifacts/phase4_2/JourneyMap_Phase4_2_review_2026-10-15.zip`이다.
전체 현재 source/test/config/doc, 이 보고서와 실행 제안, 시작 스냅샷, HEAD_TO_FINAL.patch,
Phase4.2 파일별 변경, JUnit/정적 검사/CLI/보존 증거를 포함한다.
HEAD archive + tracked patch + 새 파일 + 명시적 line-ending 복구로 최종 소스를 재구성해
파일 목록과 바이트 일치를 검사한다. 재구성한 src를 쓰는 새 프로세스에서 사전 검증 CLI를
다시 실행해 preflight-final.json 전체와 동일성을 확인한다. reconstruction-resume.json에 기록한다.
MANIFEST.json은 자신을 제외한 모든 payload의 크기와 SHA-256을 담으며 ZIP 전체 hash는
`.zip.sha256` sidecar다. venv/cache/Git 내부/credential/옛 연구 raw 원본은 포함하지 않는다.

## 남은 경계

현재 단계의 검증 대상은 wire/accounting과 offline engine 통합이다. 실제 API schema 접수,
계정 모델 접근, 실 usage와 과금, 실 네트워크/credential redaction, paid crash recovery는 미검증이다.
실행 전 고정 계획과 당시 소스에 대한 별도 승인을 받아야 한다. 승인에 결합된 HTTP composition,
durable attempted-call/usage journal과 timeout/late 회수 경계의 최종 연결도 필요하다.
이 작업이 그 paid runner를 제공하거나 실제 호출 승인을 발생시킨다고 해석하지 않는다.

실제 LLM/API 호출 **0회**, 실제 키 조회 **0회**, 유료 실행 **0회**,
commit/push/merge **각0회**, subagent **0개**다. 공식 문서 열람만 수행했다.
테스트와 모의 결과는 correctness 증거이며 실제 모델의 생활 판단·생존·기억 효과·비용 또는
연구 construct validity의 증거가 아니다.
