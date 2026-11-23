# JourneyMap Actual 오프라인 추론 비교 도구 완료 보고서

2026-10-19. 클라우드 검토 소스에서 구현했다. 데스크톱 반영이나 실제 모델 실험 완료 보고가 아니다.

## 구현 결과

기존 모델 snapshot과 출력 8,192를 유지한 세 조건을 준비한다.

| 조건 | 추론 강도 | 프롬프트 |
| --- | --- | --- |
| high_baseline | high | 기존 원문 |
| medium_baseline | medium | 기존 원문 |
| medium_structured | medium | 기존 원문에 짧은 판단 순서 추가 |

12개 합성 사례에 조건당 두 반복, 총 72개 요청 슬롯을 만든다. 호출한 수가 아니다.
조건 간 관측을 고정하고 연구용 식별자와 미래 정보를 모델 입력에서 제외한다.
medium 비교는 기존 유료 실행기의 high 검증을 우회하지 않는 독립 오프라인 모듈이다.

prepare, mock, evaluate, audit 명령을 제공한다. 저장된 응답을 분류하고 유효한 usage를
실패 응답에서도 보존한다. 추론 토큰을 출력에 중복 합산하지 않는다. usage 누락 시 전체 비용은
null로 남긴다. 후보 행동은 새 세계의 실제 엔진에서 접수와 완료를 별도로 평가한다.
저장 결과를 새 프로세스에서 data-only 재평가할 수 있다.

## 검증과 보존

- 관련 pytest 123개 통과: 신규 29개와 기존 관련 94개. JUnit 시간 26.825초.
- 마지막 수정은 테스트의 sqlite3 연결 반환값에 타입 cast를 추가한 것이다. 실행 코드의 변경은 없다.
- 이 수정 후 신규 테스트 29개를 다시 실행해 49.86초에 모두 통과했다. 123개와 합산하지 않는다.
- Ruff check와 format check 통과. strict mypy 173개 소스 파일 통과.
- git diff --check 통과. 전달 patch 적용 재구성과 변경 파일 SHA-256 대조는 묶음의 validation 결과를 따른다.
- CLI mock 저장 결과와 새 프로세스 audit 결과가 일치했다. 실제 API 증거 인증이 아니다.
- 작업 시작 시 기록한 기존 235개 파일 중 README와 계약 안내 문서 4개만 수정했다.
  나머지 231개 파일은 SHA-256이 같다. 기존 src, 테스트, F1 진단 파일도 이 범위에 포함한다.
- 전체 회귀 테스트와 F1 재현 검사는 수행하지 않았다.

초기 테스트에서는 구조화 프롬프트의 추가 줄바꿈과 테스트용 DB 접근 차단 문제가 발견됐다.
줄바꿈을 수정하고 sqlite 메모리 DB만 허용하도록 차단 fixture를 수정한 뒤 관련 123개가 통과했다.
초기 DB 차단 실패 JUnit도 validation/related-tests.xml에 남겼다.
이후 strict mypy의 테스트 helper 반환 타입 오류는 명시적 cast로 수정했다.

## 변경 파일

기존 수정: README.md, docs/API.md, docs/ARCHITECTURE.md, docs/TEST_STRATEGY.md.

신규 구현과 테스트:

- src/journeymap/experiments/life_reasoning_probe.py
- src/journeymap/examples/life_reasoning_probe.py
- tests/test_life_reasoning_probe.py
- docs/LIFE_REASONING_PROBE.md
- 이 완료 보고서

전달 묶음에는 별도로 DESKTOP_APPLY.md, 변경 patch, 해시 manifest, 검증 기록과 합성 결과를 넣는다.
기존 미커밋 인수인계 문서, 실제 DB, 실제 raw 기록, 키와 승인 파일은 포함하지 않는다.

## 기준 소스

클라우드 검토 Git HEAD: `032f900d72a4691ce06fee219578cd3dd928de71`.
클라우드 브랜치: `codex/actual-output-budget-8192`. commit, push, merge는 수행하지 않았다.

기존 소스 identity:
`f29e9631f80b19d832e83ce7969318bae061a88b543424ed7254499aca91f973`

신규 소스 identity:
`40a2253c52a001a1d8c38231644ba1ab98e7195dd55438315caf0c50e43764db`

기존 소스는 새 두 모듈을 제외하고 같은 계산법으로 대조했다. 새 모듈을 추가하면 전체 소스
identity는 달라진다. 기존 승인이나 paid report 결합을 수정하거나 재봉인하지 않는다.
과거 실제 기록의 검증은 기존 동결 소스를 사용한다.

데스크톱 대상은 기존 `C:\Users\<user>\Documents\JourneyMap\.worktrees\phase1`이다.
이 환경에서 데스크톱 파일과 registry를 직접 대조하거나 변경한 것은 아니다.
데스크톱 HEAD가 클라우드 검토 HEAD로 바뀌었다고 해석하면 안 된다.

## 결과의 한계와 다음 작업

모의 응답과 사용량은 처리 검증용이다. 토큰 절약, 판단 품질, 24시간 완주 효과는 측정하지 않았다.
합성 tick 6/9는 과거 실제 실패 입력의 재현이 아니다. 실제 원본 요청의 내용 대조는 미완료다.
행동 SUCCEEDED는 생활 목표나 최적 선택의 성공을 뜻하지 않는다. 입력된 외부 응답의 실제 호출
여부와 승인 적법성도 이 도구로 인증하지 않는다.

Plan-and-Budget의 아이디어를 단순화해 적용했으며 논문 재현, 별도 planner, 수치형 soft token budget,
기억 정책이나 모델 routing을 구현한 것은 아니다.

다음 작업은 전달 묶음을 기존 데스크톱 phase1에 반영하고 관련 오프라인 검증을 하는 것이다.
유료 비교는 별도 계획, 총 호출과 예산, journal, 승인 단일 소비 결합과 감사가 준비된 후 별도 승인 대상이다.
이번 작업의 키 조회, 계정 접근, 실제 API 호출, 승인 파일 생성은 모두 0회다.
F1 조사 종료, 미재현, 원인 미확정과 기존 수정 필요 판정은 유지한다.

개발용 권고: Sol / High. 실험의 High 대 Medium 비교와 구분한다.
