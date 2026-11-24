# Experiment 04 live 준비 findings 후속 구현 완료 보고서

2026-10-09 KST. 첨부 `JourneyMap_Experiment04_Live_Preparation_Findings_2026-10-09.md`의 A/B 및 count 오프라인 준비 구현을 완료했다. 실제 count·generation API 호출 0회, 실제 키 조회 0회이며 commit/push/merge를 하지 않았다. Mock 결과는 구현 계약 검증이다. 서비스 수용·가격·LLM behavioral evidence 또는 효과 입증으로 해석하지 않는다.

## 작업 기준과 보존

작업 위치: `C:\Users\<user>\.codex\worktrees\experiment03-live-preparation\JourneyMap`.
HEAD `557fb3df10149d7b7738137b7ac1038dfba24e84`, branch `codex/experiment03-live-preparation` 유지.
검토 working source `abc4a0614d21aac928e4f0204f9e7c885abc129e0845a345b43054c994c5d650`와 시작 소스가 일치했다.
최종 working source `05a5823a94db68b99b8dcb229b9c7b03a9531b8080202bb43797db5d43f583c8` (normalized UTF8/LF, src+pyproject), raw src hash `eff77867be1913baa66c51faebf16507e9afe2fd5a63cbe549d5283ff19023ef`.
기존 미커밋 51개 원문을 before-code로 보존했고 전체 기존 자료 11,293개의 시작 SHA-256을 캡처했다.
이번에는 기존 8개만 수정(문서 5개 append, 실행 경로 3개)하고 source 7개·test 1개·본 보고서 1개를 새로 만들었다. 현재 전체 변경 60개, tracked 6개/untracked 54개 원문을 검토 ZIP에 포함한다. 승인 수정 대상 8개를 제외한 11,285개는 해시 동일하며 이전 ZIP도 포함해 보존한다. Git HEAD/status/full tracked diff와 검토 working 기준 task diff를 별도로 남긴다.

## 수정 내용

A: readonly validate_plan은 저장 output_path를 출처 문자열로 그대로 비교한다. 현재 호스트에서 resolve/existence를 요구하지 않는다. 파일은 전달받은 bundle directory에서만 읽는다. config/profile/source/runtime/digest 바인딩은 유지한다. Native 실행 preflight는 현재 소스·정확한 새 출력 위치를 계속 검증하고 실제 count/gen 발송 직전도 source/plan을 재검증한다. 과거 plan의 경로나 hash를 변경·재봉인하지 않았다.

B: 각 정상 prefix의 세 tick-3 semantic input에서 OLD-MAP/RECEIVED-UPDATE를 계산하고 입력, 전체 최적 첫 route 집합, selected_route, lexicographic tie-break, 첫 이동 전용 scope를 export/summary에 저장한다. 실제 world/condition은 정책에 전달하지 않으며 정책 결과를 모델 입력에 재주입하지 않는다. 미시도 분기도 policy_computed와 model_called/model_first_judgment를 분리한다. 새 audit가 세계·관찰·semantic input을 재생하여 정책을 재계산한다. 잘못된 정책으로 export와 journal을 재봉인한 단위 fixture는 EXCLUDED다.

버전: 새 execution/export/audit `experiment04-responses-*-2`, count `experiment04-responses-input-count-1`. CLI 새 기록은 v2를 만들고 legacy execution v1 및 기존 offline v1/v2는 계속 읽는다. 과거 raw에 정책/count 필드를 소급 삽입하지 않는다.

Count 경로: `adapters/route_update_count.py`, `experiments/route_update_counted{,_plan,_cost,_session,_audit,_service}.py`. Count transport는 생성 transport와 endpoint/단일 사용 상태가 분리되어 있다. 생성 body에서 model/input/text(strict schema)/reasoning만 명시적으로 투영한다. store/max_output_tokens/service_tier 제외 근거와 transport-only 설정 목록을 기록한다. 각각 timeout=120, max_response_bytes=2000000. 생성 설정은 gpt-6.1-sol / low / max_output_tokens=2048 / exact returned_models=[gpt-6.1-sol]이다. Engine seed 42와 기존 fixed balanced 6순서를 유지한다.

공통 guard→durable count 요청·INTENT→COUNT_SEND→응답·엄격 count 검증→COUNT_RESULT→생성 최대 비용 예약→generation INTENT/SEND→응답/결과로 진행한다. 기존 pending 관찰 하나를 사용하며 count 중 tick/모델 입력/행동이 바뀌지 않는다. 계수 매 생성 기회당 최대 1회, 생성 단 1회; 캐시/retry/fallback/resume 없음. 계수 실패/negative/bool/object/크기/20,000 초과는 전체 중단하고 생성하지 않는다. count 후 wire 변경과 count/usage 불일치도 전체 중단한다. raw·usage·미확정 예약은 남긴다.

Scope는 count-only 또는 count-and-generate. count-only smoke generation_cap=0이므로 해당 승인이 생성으로 확대되지 않는다. frozen digest, stage/scope, output, endpoint 목록, 분리된 count/generation/network caps 및 확정한 금액의 exact 승인과 gate가 필요하다. 이전 smoke/실험 승인 및 금액을 재사용하지 않았다.

## 검증과 원본

증거 경로: `trials/experiment04-live-findings-verification-20261009`.

| 검사 | 결과 |
| --- | --- |
| Python | 3.12.14 |
| 전체 pytest | 1,525 passed, failures/errors/skips 0, 326.60초 |
| 새 count/path/policy 회귀 | 62개, 전체 최종 pytest에 포함하여 통과 |
| Ruff check / format | 통과, 205 Python files format 확인 |
| strict mypy | 통과, 176 source/test files |
| git diff HEAD --check | 통과; Git EOL 경고는 별도 로그에 보존 |
| 새 mock raw | 21개 묶음, INCLUDED 및 계약별 정상/실패 stop 재감사 |
| 정상 smoke | synthetic count 1 + generation 1; pilot 지표 없음 |
| 정상 pilot | synthetic count 48 + generation 48, initial 6, branches 18, 정책 18개 분기 |
| 최대 상한 fixture | synthetic count 114 + generation 114 = 228, 각 분기 6결정 뒤 종료 |
| count-only fixture | count 1 + generation 0; 생성 scope 거부 |
| count 실패/이상 반환 | 생성 0회로 전체 중단; 예약 보존 |
| count=usage 불일치 | 전체 중단, 원본 count/usage/예약/보수적 usage 비용 유지 |
| 미시도 정책 | 정상 prefix 후 count 실패 시 세 정책 계산 보존, 모델 호출 false |
| 과거 live-prep v1 12묶음 | 원본·이동 사본 모두 기존 보고서 exact 일치 |
| 과거 offline v1/v2 10묶음 | 원본·이동 사본 모두 기존 보고서 exact 일치; 기존 INCOMPLETE/EXCLUDED 상태도 유지 |
| 경로 해석 금지 | legacy live 12사본에서 Path.resolve 차단 상태 감사 통과; native mismatch 및 path/digest 변조 거부 |
| 감사 격리 | Provider·real/mock send·HTTP 생성·socket·키 조회·subprocess/Git 명시 차단한 별도 재감사 |
| 원본/ZIP | preservation-gate 및 외부 ZIP verification의 byte/hash 검사로 확인 |

`offline_cli.py`, `run_checks.py`, `verify_offline.py`, `verify_audit_isolation.py`, `final_check.py`, `package_review.py`와 JUnit, 최종 log/gate JSON, 계획·config·가격 검토·raw·audit/inspect를 포함한다. 새 테스트는 생성 응답 refusal/malformed/incomplete/token_cap/timeout/HTTP/identity/usage/tier도 회귀 검사한다. 엔진·관찰·평가·여섯 순서·기존 E03 회귀는 전체 테스트로 유지된다.

최종 근거는 pytest-full-final/static-gate/offline-gate/historical-gate/audit-isolation-gate/final-gate다. `pytest-targeted-final.log/xml`은 이름과 달리 개발 중 추가 import/fixture 수정 전 실패한 중간 실행이며 최종 gate로 사용하지 않는다. 개발 실패 로그는 보존하되 pytest-dev-01.log는 pytest fixture 오류에서 환경 repr가 출력되어 ZIP에서 제외한다. ZIP에는 환경 덤프를 넣지 않는다. 해당 fixture 이름은 짧게 지정하고 원래 테스트를 최종 전체 실행에서 통과시켰다.

## 준비 상태와 미해결 외부 확인

기본 새 live 후보는 DRAFT / UNAUTHORIZED, count price UNKNOWN, 서비스 실제 확인 UNVERIFIED다. Mock 구현 계약 통과와 실제 서비스 확인을 다른 필드로 구분한다. 실제 smoke 승인·실제 live READY 파일·서비스 확인 완료 기록을 만들지 않았다.

공식 [count guide](https://developers.openai.com/api/docs/guides/token-counting), [API reference](https://developers.openai.com/api/reference/resources/responses/subresources/input_tokens/methods/count), [pricing](https://developers.openai.com/api/docs/pricing)을 2026-10-09 확인했다. 메시지 formatting과 입력/schema 계수 계약 및 허용 필드를 반영했다. 이 모델·계정의 reasoning/schema 수용과 반환 generation input_tokens=count는 실제 승인 smoke에서 확인해야 한다. 별도 count 단가는 명시적 근거를 찾지 못해 UNKNOWN으로 남겼다. 무료라고 가정하지 않는다.

USD10은 PROPOSED, 승인 금액은 null이다. 기존 USD8.10520은 입력 상한 20,000, 출력 상한 2048, 보수적 입력 $2.50/M·출력 $10/M의 생성 115회 조건부 비용이고 count 비용 포함 총액이 아니다. 입력 상한/합성 count/실제 측정값을 구분하며 이번 실제 반환 usage 또는 청구 추정액은 없다. Mock ledger의 0.00010/count는 테스트용 합성 상한이다.

후속 상한: smoke count 1+generation 1=network 2, pilot count 114+generation 114=network 228, 합계 count 115+generation 115=network 230. Count는 행동 표본이 아니다. 이번 mock과 과거 one-shot/실제 smoke를 합산하지 않는다.

외부 가격 검토 근거를 제공받은 새 smoke 후보에는 READY_SERVICE_CHECK를 발급할 수 있는 조건부 구현을 준비했다. 이는 확인을 위한 후보이지 서비스 검증 완료가 아니다. 미래 승인 smoke의 live raw·exact 승인·schema/reasoning·count=usage·결과·ledger를 Provider/key/network 없이 재생해 unsigned witness를 만드는 경로도 구현했다. Mock/수동 status는 서비스 확인 근거로 거부하며 pilot은 가격 근거와 source/runtime가 같은 smoke witness 모두 있어야 READY 후보를 만들 수 있다. Source 변경이면 다시 검토해야 한다. 지금 실제 gate를 우회하거나 이 경로를 실행하지 않았다.

미래 준비 명령(현재 task에서는 같은 명령으로 만들어 저장한 DRAFT만 있음):

```powershell
python -m journeymap.examples.route_update_live plan trials/NEW/draft-smoke-plan.json --stage smoke --mode live --output trials/NEW/smoke
python -m journeymap.examples.route_update_live plan trials/NEW/draft-pilot-plan.json --stage pilot --mode live --output trials/NEW/pilot
```

실제 후속 발송 명령/price review/witness/승인 요건은 `docs/EXPERIMENT04_LIVE_PREPARATION.md` 후속 계약 절에 있다. 새 output 부모를 먼저 만들고 이미 존재하는 경로나 과거 승인을 재사용하지 않는다. 서비스 비용 확인 및 사용자의 해당 count/gen 범위·금액·digest 승인 없이 발송하지 않는다.

Linux/다른 실제 OS에서 실행한 증거는 없다. 이 호스트의 WSL은 설치되지 않아 native Linux 검사를 할 수 없었다. 대신 Windows 저장 경로에 대한 호스트 resolve 금지 검사와 같은 호스트의 다른 폴더 사본 감사를 실제로 수행했고, original plan digest를 유지했다. Linux native 검사는 후속 검토 환경에서 추가할 수 있다.

Audit 및 witness의 unsigned hash는 원본 보존·형태·입력/digest·한도·ledger·행동/평가·정책의 내부 일관성만 확인한다. 외부 서명 없이 저장 raw가 서비스 사실임을 증명하지 않는다. SEND는 durable 발송 의도이며 네트워크 전달/청구 완료 증명이 아니다. Source/runtime identity는 별도 현재 source gate에서 확인하며 readonly audit는 원본 software provenance를 보존한다.

## 검토 ZIP

`trials/review-bundles/experiment04-live-findings-<새 UTC 시각>` 아래에 새 ZIP, SHA-256 sidecar, 외부 verification JSON과 `extracted-verified`를 생성한다. 기존 ZIP·원본은 덮어쓰지 않는다. ZIP은 전체 src/tests/docs와 tracked diff·신규 파일 본문·기존 변경 원문·계약/보고서·21개 새 raw·22개 원본과 이동 사본·검증 scripts/logs/gates를 포함한다. 파일별 상대 경로·bytes·SHA-256을 MANIFEST.json에 기록한다(자체 hash는 외부 verification). API key·인증 헤더 값·환경 덤프·.env·.venv·.git·cache·테스트 임시 폴더·과거 ZIP 본문은 제외한다.

검증은 ZIP CRC, 중복/위험 경로, manifest 전수 hash/size, 압축 해제 bytes=payload=원본, source/prior artifacts SHA 불변, HEAD/status/full diff 불변을 확인한다. ZIP의 실제 최종 경로와 hash는 완료 응답 및 외부 verification에 기록한다.
