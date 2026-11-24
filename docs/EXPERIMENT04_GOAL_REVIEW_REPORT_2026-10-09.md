# Experiment 04 목표·평가 계약 보완 완료 보고서

2026-10-09 KST. 첨부 `JourneyMap_Experiment04_Review_2026-10-09.md`에 따른 수정이다. **오프라인 검토 완료**, 실제 API 호출 0, API 키 조회/출력 없음, commit/push/merge 없음. 실험 모델·파라미터·예산·표본 수·조건 순서·live 계층은 확정하거나 실행하지 않았다. 새 provenance 시스템, 05A/05B 확장과 standalone ZIP runner 지원을 추가하지 않았다.

## 기준 및 수정 범위

작업 위치는 `C:/Users/<user>/.codex/worktrees/experiment03-live-preparation/JourneyMap`, HEAD는 `557fb3df10149d7b7738137b7ac1038dfba24e84`, branch는 `codex/experiment03-live-preparation`다. 이전 미커밋 39파일을 before-code에 보존했다. 작업 전 normalized working source는 `f6327a8c90169b316478c6d1e6063870dc3f4aaa2894558e0e1abe5d77c90b14`로 10월 8일 검토 자료와 일치했다.

이번 작업은 기존 working 파일 9개(source 5 + 문서 4)를 수정하고 테스트·보고서 2개를 추가했다. HEAD 대비 전체 변경은 41파일(기존 tracked 문서 6 + untracked 35)이며 기존 미커밋 작업을 삭제하지 않았다. source 변경은 adapters/route_update.py, experiments/route_update.py, route_update_session.py, route_update_audit.py, route_update_metrics.py의 설명 docstring이다. metrics 계산·그래프·이동·WAIT·6회/24tick·첫 점수/회복·정보 경계는 유지했다. 새 테스트는 test_route_update_goal.py의 22개다. 기존 테스트 파일은 수정하지 않았다.

최종 working source SHA-256은 `79c3aac38bed65735b644427e4c1dd1c6fd3e2cb45abf6f0773a7ac68e03dc86`이며 raw-byte source SHA-256은 `adecd6a7a948221446c5f1f112813a545ee0ac788aa3f5775f5ffe7fdc324c06`다. source-final.json에 버전과 범위를 저장한다. 기존 원본 4,667개 중 이번 허용 수정 9개는 변경 전 원문과 hash를 보존하고 나머지 4,658개는 byte/hash 불변으로 확인한다. README/API/TEST_STRATEGY는 append-only, 현재 E04 계약 문서는 목표·audit 범위를 갱신하되 전체 변경 전 본문을 before-code에 남겼다. 이전 완료 보고서, v1 raw·plan·wire·ZIP, E03 구현/결과, primary checkout은 그대로다.

## 목표와 지표

v1의 'G 도착' 목표만으로는 실제 세계의 시간 최적 첫 이동 판정과 일치하지 않았다. v2는 초기 tick 0의 도착 목표와 실제 provider 선택을 유지하고, **후속 tick 3 이후에만** 현재 결정부터 받은 경로 정보에 따른 남은 simulated travel time 최소화를 명시한다. 결정·시간 한도는 상한이며 더 긴 경로를 선호할 이유가 아니다. 받은 업데이트/직접 관찰이 오래된 정보를 대체하면 적용하고 보지 못한 World Truth 접근을 가정하지 않는다.

조건명·폐쇄 정답·특정 올바른 목적지 또는 새 단계 라벨은 추가하지 않았다. 제공된 Observation 시점으로 적용 단계를 구분한다. 모든 조건에 같은 완전한 profile을 제공하며 초기 S→A/B를 scripted preparation이나 고정 목적지로 바꾸지 않았다. JSON schema·비용·WAIT·출처·전체 경험·authority boundary는 같다.

`first_appropriate`의 뜻은 **실제 세계 상태에서 남은 시간 최적인 첫 MOVE인가**다. `eventual_arrival`은 실제 Engine으로 최종 G에 도착했는가다. 열린 A의 A→D→G는 6tick에 도착하므로 eventual_arrival=true지만 A→C→G=4tick보다 길어 first_appropriate=false, extra_ticks=2다. 이것을 도착 실패나 목표 오해라고 하지 않는다. 통보 없는 ②의 false는 정보 접근 제약을 포함한 시스템 결과이며 일반 추론 실패로 부르지 않는다. 기존 recovery 정의는 첫 false 후 도착 여부이며 내부 계획 수정의 증거가 아니다.

## 버전과 과거 감사

| 계약 | protocol | profile | export | audit |
|---|---|---|---|---|
| v1 원본 | experiment04-offline-1 | experiment04-route-change-decision-1 | experiment04-offline-export-1 | experiment04-offline-audit-1 |
| 새 v2 | experiment04-offline-2 | experiment04-route-change-decision-2 | experiment04-offline-export-2 | experiment04-offline-audit-2 |

새 execute/CLI는 v2만 만든다. LEGACY_PROFILE은 원래 v1 문구를 그대로 보존한다. audit는 plan protocol의 정확한 profile/export 조합을 선택해 새로운 Session에서 재구성한다. 그 밖의 버전과 혼합 조합을 성공 처리하지 않는다. 기존 plan/wire/journal/export를 다시 쓰거나 기존 plan digest를 v2에 재사용하지 않았다. fake configuration/scenario/schema는 변화 없는 계약으로 유지했다.

새 v2 demo plan digest는 `390096cf309f24c89dbe89710360e2dcaefc1bd600d37daf414b439f75d32d9a`다. 과거 v1 첫 초기/후속 wire의 byte·길이·SHA-256 렌더링은 정확히 재현했고, 새 목표 때문에 v2 wire는 v1과 구별된다. 의미 입력은 같으며 같은 prefix의 ①② 전체 v2 wire는 byte 단위로 동일하다. ③은 허용된 공식 통보만 다르다.

## audit 보장의 범위

audit는 새 kernel/application에서 원본 응답·의도·사건을 재생하고 Observation, 경험, 지식, receipt, Event, 위치, 시간과 평가를 저장 기록에 대조한다. 저장된 metrics만 승인하지 않는다. 그러나 **runner와 Session/metrics 함수를 공유**하며 별도 작성한 두 평가 알고리즘의 일치 검증은 아니다. INCLUDED는 기록과 재실행의 일치 판정이다.

plan에 source/runtime가 저장돼도 audit_directory가 현재 소스/환경 자체를 대조하는 것은 아니다. 소스 정체와 실행 환경은 source/static gate, ZIP bytes와 SHA-256 manifest는 packaging verification, 원본 보존은 preservation gate에서 별도로 확인한다. INCLUDED만으로 출처·모든 평가 논리·전체 무결성·construct validity가 증명됐다고 설명하지 않는다. 코드 기준선은 지식에 따른 선택과 실제 세계 평가의 해석을 보조한다.

## 실제로 실행한 검증

| 검사 | 이번 직접 실행 결과 |
|---|---|
| 수정 전 v1 ZIP/hash/manifest | SHA-256 6050fafe1ede4319841ad9a028df89f90ef4ed6fc282fcf0b35ecd84be09392e 일치, payload 374개 검증 |
| 수정 전·후 v1 원본 8묶음 Provider 없는 audit | 이전 보고서와 정확히 일치, 원본 hash 불변 |
| E04/목표 보완/아키텍처 pytest | **104 passed**, exit 0 |
| 최종 전체 pytest (기존 E03 포함) | **1,391 passed / 164.94s**, failure/error/skipped 0, exit 0 |
| Ruff check . / format --check . / strict mypy / git diff --check HEAD | 각각 exit 0; 원본 로그와 final static gate에 기록 |
| 새 v2 fake CLI / audit / inspect | 각각 exit 0, 새 출력 경로 및 외부 plan digest 사용 |
| 새 목표 공통성 / ①② 전체 wire / ③ 통보 차이 | 통과 |
| v1 초기/후속 wire 재현 / 미지원·혼합 버전 거부 | 통과 |
| 열린 A 도착 성공·시간 최적 false 구분 | 실제 fake 초기 선택 포함 원본 실행으로 확인 |

bundled Python 3.12.14, 기존 개발 패키지를 사용했고 새 의존성은 설치하지 않았다. offline_cli.py는 socket과 실제 키 조회를 차단한다. 기존 HTTP 테스트는 child process에 실제 키를 읽지 않고 테스트용 가짜 값을 주입한다. 실행 명령·종료 코드·JUnit XML은 trials/experiment04-goal-review-20261009에 보존한다. pytest-full-final.log/xml과 static-gate.json이 최종 결과다.

개발 단계에서 새 테스트 1개가 빈 profile version에 대한 기존 ProviderRequest의 선행 거부 메시지를 다르게 기대해 실패했다. 기존 거부를 그대로 유지하고 테스트 기대만 수정했다. mypy가 테스트 모듈에서 재수출하지 않은 이름 4개를 검출해 해당 정의 모듈을 직접 import하도록 수정했다. 개발 실패 로그는 삭제하지 않았고 최종 전체 통과와 구분했다. 새 prompt가 실제 LLM 행동을 개선했다고 주장하지 않는다.

v1 세부 재검증은 demo/response/wait/time/initial/integrity-final=INCLUDED, recording-final=INCOMPLETE, tamper-final=EXCLUDED다. v1-compatibility-gate.json에 전체 감사 결과와 원본 보고서 일치를 남겼다. audit/inspect에서 Provider 호출·키·네트워크를 차단했다.

## 새 fake 원본과 인계

새 `demo-v2`는 초기 A/B 각 1, 생성 6분기, 실제 fake 초기 2 + 후속 14 = 16호출이다. 첫 적절 5/6, 최종 도착 6/6, 회복 도착 1로 기존 fixture 결과와 같다. 이 같음은 scripted 응답의 correctness 재확인이지 prompt의 행동 효과가 아니다. 원본 v1이나 과거 LLM 자료와 합산하지 않았다.

별도 `arrival-vs-optimal-v2`는 실제 fake 초기 A 선택 후 세 분기를 재구성한다. 열린 ①에서 A→D→G=6tick 도착 true/첫 시간 최적 false를 원본으로 보존한다. 원래 demo와 별개의 목적·지표 예시로 통계 표본을 늘려 합산하지 않았다. fake API usage와 청구 비용은 null/NOT_APPLICABLE_FAKE다.

새 ZIP은 `trials/review-bundles/experiment04-goal-review-<unique UTC timestamp>/JourneyMap_Experiment04_Goal_Review_2026-10-09.zip`에 exclusive 생성한다. 전체 source/tests/docs, AGENTS/pyproject, before/final HEAD·status, 전체 tracked HEAD diff, 기존 reviewed working 본문 대비 **이번 task diff**, 신규 파일 포함 모든 변경 원문, 첨부 계약·완료 보고서·scripts·gates·실제 로그·새 fake 원본을 담는다. v1 raw 8묶음과 저장된 원본 감사도 복사본으로 포함해 새 profile에서의 재감사를 검토할 수 있다. 이전 ZIP 본문은 중복 포함하지 않는다.

상대 경로/크기/SHA-256 manifest, manifest와 ZIP 외부 해시, 압축 해제 bytes/원본 일치 및 packaging 전후 source·HEAD/status/diff·과거 자료 불변을 verification JSON에 기록한다. 실제 ZIP 경로는 최종 응답과 verification JSON에서 보고한다. 키/인증 헤더 값/환경 덤프/.env/.venv/.git/캐시/pytest 임시 자료는 제외한다.

미실행: 실제 API/LLM, 실험 모델·비용·표본·순서 확정, live 전송, standalone ZIP runner 실행, mirror 확장과 05A/05B다. 이번 단계에서 확정하지 않았다. 다음 단계는 목표와 판정이 같은 목적을 다루는지 확인한 뒤의 소규모 실제 파일럿 **준비 검토**이며 실행 승인을 뜻하지 않는다.
