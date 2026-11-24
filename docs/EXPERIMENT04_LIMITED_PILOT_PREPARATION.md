# Experiment 04 별도 제한 pilot 준비 — 2026-10-10

이번 작업은 비용 정책과 서비스 증거 호환성을 오프라인으로 구현·검토한다. 실제 API·키 조회 없이 새 후보와 PENDING 승인안을 만들며, 기존 smoke의 consumed claim과 응답을 재사용하지 않는다.

`PILOT_COUNT_FEE_UNKNOWN_V1`/execution-export-audit v4는 pilot/count-and-generate, 새 초기6개와 기존 p001~p006 순열, count/generation/network 114/114/228에만 적용한다. gpt-6.1-sol/low/default/storefalse/기존 strict schema/input20000/output2048/timeout120/response2000000과 seed42를 유지한다. 초기 도착 목표, 후속 가용 정보 기준 시간 최소화, 6회/24tick 및 OLD-MAP/RECEIVED-UPDATE 첫 선택 기준선을 변경하지 않는다.

초기 A/B 분포를 그대로 기록한다. A는 안내로 최적 첫 이동이 C에서 D로 바뀌는 경우이고 B는 세 조건 모두 D가 최적이므로 구분해 해석한다. 초기 실패를 보충하지 않고 첫 선택·발견 뒤 회복·최종 도달·시간·호출 수를 별도로 기록한다.

생성 조건부 예약액은 문자열 8.03472이며 114*0.07048이다. 계수 가격 UNKNOWN, fee/charge/combined upper bound=null, total_cost_bound_guaranteed=false. 실패한 count와 generation도 시도·예약에 남기며 전체 USD10 보장으로 표시하지 않는다. 이전 smoke 비용과 표본을 합산하지 않는다.

계획/비용/승인/호환성/감사/CLI dispatch만 국소 수정한다. runner·core·scenario·profile/schema·count projection·HTTP/decode·session/복원·평가 전체는 검토 ZIP source와 byte 동일한 보호 대상이다. source 전체 inventory의 변경은 정확한 허용 파일 집합으로 제한한다.

호환성 기록에는 검증된 smoke ZIP hash, 원래 plan/witness digest와 witness bytes hash, 과거 전체 source, 새 전체 source hash, runtime, 변경별 before/after hash 및 원문, 전체 보호 목록, 이전/현재 실제 smoke·A/B 세 조건과 후속 경험 재생 비교를 포함한다. 과거 witness의 source를 새 hash로 바꾸지 않는다. 새 plan은 witness와 compatibility digest를 묶고 현재 전체 source/runtime를 검사한다. 저장 자료 재감사는 출처 Windows path를 resolve하거나 현재 Git을 읽지 않는다. Session/metrics는 공유하며 unsigned 비교 증거의 한계를 명시한다.

```powershell
# 기존 부모 아래 새 경로; compatibility는 source scope 밖의 최종 기록이다.
python -m journeymap.examples.route_update_live plan trials/NEW/pilot-plan.json --stage pilot --mode live --pilot-count-fee-unknown --service-review ACTUAL_WITNESS.json --compatibility COMPATIBILITY.json --output trials/NEW/live-pilot --authorization-template trials/NEW/authorization-template.json
python -m journeymap.examples.route_update_live mock trials/NEW/mock-pilot --stage pilot --pilot-count-fee-unknown
python -m journeymap.examples.route_update_live audit trials/NEW/mock-pilot
python -m journeymap.examples.route_update_live inspect trials/NEW/mock-pilot
```

후보 READY_FOR_REVIEW는 서비스 증거/호환성 검증과 가격 UNKNOWN/최종 승인 PENDING을 분리한 상태다. mock fixture에 실제 서비스 witness를 연결하지 않는다. 생산 live 후보에는 고정된 실제 v3 smoke만 연결한다. 기존 v3 smoke 승인, 누락/pending/다른 plan/source/runtime/output/느슨한 bool-int 승인은 발송 전에 거부한다. 새 HUMAN_APPROVED 파일과 actual output/claim은 최종 사용자 승인 뒤에만 생성한다. 자동 retry/fallback/resume/보충/추가 실행 없음.

최종 source/runtime와 plan digest, 정확한 사용자 PowerShell 명령, 실행 경로와 승인 문장은 새 준비 폴더의 PILOT_APPROVAL_REQUEST.md에 기록한다. 현재 준비 단계의 검증 결과와 미수행 사항은 완료 보고서를 따른다. 모델 효과나 통계적 차이를 입증하는 작업이 아니다.
v4의 현재 서비스 상태는 `readiness.service_evidence`, `readiness.source_compatibility`와 고정 witness/compatibility digest에서 판정한다. 상속한 `model_support_review`, `count_projection`, `counter_review`의 이전 검토 문구는 당시 공식 문서·구현 검토 이력이며 최신 서비스 발송 승인으로 승격하지 않는다. 계수 가격과 최종 승인 상태는 별도다.