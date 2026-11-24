# JourneyMap Actual 비교 실행기 구현 및 오프라인 검증 보고서

2026-10-20 KST 정리. 구현과 테스트는 10월 19일 수행했고, 완료된 JUnit과 전달 근거를 10월 20일 확인했다.
클라우드 검토 소스에서 완료한 작업이다. 이번 변경은 아직 데스크톱에 적용하지 않았다.

## 결과

기존 72개 요청 corpus에 별도 실행 계획, 단일 소비 승인, 전용 SQLite journal, HTTP/recording
transport와 무재호출 감사를 연결했다. 기존 probe와 paid-life, Core 및 기존 테스트 코드는 수정하지 않았다.
실제 API 호출, 실제 키 조회, 계정 접근과 사람의 승인 파일 생성은 모두 0회다.

모델은 기존 gpt-5.4-mini snapshot이며 high/기존 프롬프트, medium/기존 프롬프트,
medium/짧은 판단 순서의 세 조건을 유지한다. 조건별 두 반복과 합성 사례 12개의 총 72개 슬롯이다.
각 슬롯은 동일 관측에서 행동 하나를 선택하는 독립 분기이며 24시간 연속 생활 실행이 아니다.

후보 비용 상한은 USD 1.00이다. 72개가 출력 상한을 모두 사용하면 출력 요금만 USD 2.654208이므로
이 예산으로 전체 완료를 보장하지 않는다. 다음 요청의 조건부 USD 0.336864 예약이 들어가지 않으면
중단하고 미실행 슬롯을 분모에 남긴다. 비용 한도를 늘리거나 실제 실행을 승인한 것은 아니다.

INCOMPLETE/REFUSAL과 잘못된 행동도 결과로 보존하고 다음 독립 슬롯으로 진행한다.
신원/usage/기록/HTTP 오류나 timeout은 전체 실행을 중단한다. 응답 완료 시각을 기준으로 판정하며
완전히 받은 늦은 응답의 유효한 사용량은 회수한다. 모르는 비용을 0으로 만들지 않는다.

## 검증 결과

| 검사 | 직접 확인한 결과 |
| --- | --- |
| 신규 test_probe_execution.py | 54 passed, JUnit 1250.269초, 실패/오류/skip 0 |
| 기존 관련 테스트 4개 파일 | 123 passed, pytest 302.37초, 실패/오류/skip 0 |
| 합계 | 서로 다른 관련 테스트 177개, 전체 회귀 테스트 수가 아님 |
| strict mypy src tests | 178개 소스 파일 통과 |
| Ruff check | 통과 |
| Ruff format --check | 최종 artifacts 제외 범위 241개 파일 통과 |
| diff 검사 | 통과 |
| CLI 모의 preflight | 72개 슬롯, 290개 journal 항목, FINISHED, 잔여 예약 0 |
| 별도 프로세스 audit | data_only_equal=true, provider_recalled=false, FINISHED |

신규 테스트의 시간이 긴 사실을 그대로 기록했다. 테스트 재실행 횟수를 합산하지 않았다.
일시적인 도구 세션 문제 이후 파일로 남은 JUnit을 확인했으며 두 테스트 묶음은 정상 종료 기록을 가진다.
전체 회귀와 F1 재현 검사는 수행하지 않았다. 마지막 lint/format 확인은 artifacts를 제외했고
저장소 설정이나 기존 스크립트는 변경하지 않았다. 초기 전체 범위 Ruff와 mypy도 통과했다.

핵심 반례와 검증 범위:

- 승인 누락과 source/plan/corpus/registry/scope/주체/결정 불일치가 registry 및 키 접근보다 먼저 거부된다.
- 합성 승인은 실제 HTTP 경로에서 거부된다. 가짜 HTTPS 검증용 사람 형태 승인도 테스트 객체일 뿐 실제 승인이 아니다.
- 같은 run 또는 approval ID를 다시 쓰지 못한다. OPEN/UNCERTAIN이 남은 registry는 새 실행도 차단한다.
- 예약, 전송 시도, 응답 저장, 정산 뒤의 중간 종료를 주입해 기록과 재실행 차단을 확인했다.
- 응답 원문/정산 삭제, 순서/body/회계/종료 사유 변조와 재봉인 반례를 거부했다.
- timeout 후 정산, 중복 response ID, 모델/tier 불일치, usage 누락, HTTP 오류, 깊은 JSON을 검증했다.
- 예산 반례에서 18개만 시도한 뒤 중단했고 54개 미실행 슬롯을 포함한 전체 72개 분모를 유지했다.
- 가짜 HTTPS로 endpoint, 키 조회 전 예약, 전송 전 journal, 비밀 문자열 차단과 redirect 미추적을 검증했다.

## 소스와 보존

클라우드 HEAD: `032f900d72a4691ce06fee219578cd3dd928de71`.
클라우드 브랜치: `codex/actual-output-budget-8192`.

적용 기준 source identity:
`40a2253c52a001a1d8c38231644ba1ab98e7195dd55438315caf0c50e43764db`

새 source identity:
`eb55f187d8001616c1635bd12df9c811c28402b822b3c689c8de76c43218a7b0`

기존 240개 파일 중 문서 7개만 수정했고, 나머지 233개는 SHA-256이 같다.
기존 source와 테스트, 미커밋 인수인계 자료, F1 진단 파일은 보존했다.
이전 reasoning probe ZIP SHA-256도 다음 값 그대로다.
`6ba799472f253c1c7e6155a90c4fa907e6f627c24edcd75861a29d5165cba41b`

기존 문서 수정 7개:
README.md, docs/API.md, docs/ARCHITECTURE.md, docs/ERD.md,
docs/ROADMAP.md, docs/TEST_STRATEGY.md, docs/LIFE_REASONING_PROBE.md.

신규 파일 7개:

- src/journeymap/adapters/probe_execution.py
- src/journeymap/adapters/probe_journal.py
- src/journeymap/experiments/paid_probe.py
- src/journeymap/examples/probe_execution.py
- tests/test_probe_execution.py
- docs/PROBE_EXECUTION.md
- 이 보고서

전달 patch는 총 14개 파일만 대상으로 한다. 새 worktree나 Git commit/push/merge는 없다.
데스크톱 registry와 원본 자료는 이 환경에서 직접 읽거나 변경하지 않았다.
새 source와 corpus identity를 과거 기록에 소급 적용하지 않는다. 과거 감사는 해당 동결 소스를 사용한다.

## 제한과 다음 단계

1. 구현자의 오프라인 검증 완료다. 독립 검토 통과나 유료 실행 준비 완료 판정이 아니다.
2. 사람의 승인은 신뢰된 로컬 운영자 기록이다. hash chain은 외부 제공자의 응답 진위나 청구서 인증이 아니다.
3. registry는 신뢰된 로컬 파일이다. 기기 간 이력을 인수하지 않고 새 DB를 만드는 방식은 허용되지 않는다.
4. timeout은 동기 HTTP read timeout과 완료 시각 검사다. 강제 종료 가능한 전체 벽시계 120초 상한은 아니다.
5. 중간 종료의 OPEN/UNCERTAIN 상태를 자동 회계 복구하거나 재개하는 명령은 없다. inspect는 읽기 전용이다.
6. 실제 토큰 절감, 판단 품질과 생활 완주 효과는 미측정이다. 합성 입력은 과거 실제 실패 입력의 재현이 아니다.
7. 과거 실제 두 run의 원본 요청 내용 대조는 아직 미완료다. 이번 합성 실험으로 대체하지 않는다.
8. 고정 단가는 기존 계획을 사용했다. 실제 실행 승인 전 최신 공식 조건과 기기별 소비 이력을 확인해야 한다.

다음은 ZIP의 DESKTOP_APPLY.md에 따라 기존 phase1에 반영하고 오프라인 검증하는 것이다.
이후 승인/회계/기록 누락/crash/timeout의 독립 검토를 진행한다. 실제 실행은 그 뒤 별도 명시적 승인 대상이다.
F1 조사 종료/미재현/원인 미확정과 기존 수정 필요 판정은 유지한다. 개발용 권고는 Sol / High다.
