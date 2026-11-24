# Experiment 04 오프라인 구현 완료 보고서

2026-10-08. **오프라인 검토 완료**. 실제 API 호출 0회, 키 조회/출력 없음, commit/push/merge 없음. fake fixture는 프로토콜·정보 경계·correctness 검증 자료이며 LLM 행동 효과나 construct validity의 증거가 아니다.

## 작업 기준과 기존 자료

- 작업 위치: `C:/Users/<user>/.codex/worktrees/experiment03-live-preparation/JourneyMap`.
- HEAD: `557fb3df10149d7b7738137b7ac1038dfba24e84`, branch `codex/experiment03-live-preparation`. 시작과 종료가 같다.
- 10월 6일 검토 source와 작업 전 working source는 `a3f9fe5ac39e87be701ecb578ec342516fd35b41bcf57c535758e55e787c4ec0`으로 일치했다. 이전 미커밋 변경은 26파일(기존 tracked 문서 6 + untracked 20)이었다.
- E04는 source 9파일, 테스트 2파일, 문서 2파일을 새로 추가했다. 기존 source/tests는 수정하지 않았다. README와 API/ARCHITECTURE/ERD/ROADMAP/TEST_STRATEGY에는 원문 뒤에 안내만 추가했고 작업 전 본문을 before-code에 보존했다. 최종 변경은 39파일이며 33개 untracked 본문도 ZIP에 포함한다.
- primary checkout, 기존 실제/오프라인 실험과 ZIP은 보존했다. 전체 3,724개 이전 파일을 hash로 확인하며 현재 overview 문서 6개는 원본 본문을 보존한 append-only 변경으로 구분한다. 나머지 3,718개는 byte/hash 불변이다. 상세 수치는 preservation-gate와 ZIP verification에서 확인한다.
- 최종 normalized working source SHA-256: `f6327a8c90169b316478c6d1e6063870dc3f4aaa2894558e0e1abe5d77c90b14`. raw-byte source SHA-256: `fec33f9aa1ae5d5cb2d3b5b3be74685edfaa48abf8d61034d34afa3a31d240ee`. `source-final.json` 및 plan의 source identity에 범위와 버전을 기록했다.

## 구현

독립 18방향 경로/한 인물 시나리오, tick 0의 작성된 전체 지도, 실제 provider 초기 S→A/B 이동과 닫힌 경험, tick 2 checkpoint, 새 application에서의 3조건 의도 재결합, tick 3 원자적 양방향 폐쇄 및 수신자 공식 통보를 구현했다. 시나리오에는 선언·도메인 투영만 두고 application 조립은 `experiments/route_update_runtime.py`에 분리했다.

E04 전용 semantic allowlist/profile/schema는 현재와 전체 과거 경험에서 내부 식별자를 제거한다. 첫 전체 canonical wire의 ①② byte equality와 ③의 공식 통보만 허용하는 차이를 호출 전에 확인한다. A/B의 원격 폐쇄 은닉, C/G의 직접 확인, 반환 이후 receipt 제공, 기록별 출처와 학습 시점을 유지한다. 기존 03 prompt/knowledge/session/metrics와 Core 권한 경계는 변경하지 않았다.

OLD-MAP과 RECEIVED-UPDATE는 첫 tick 3 이동만 비교하는 지식 기반 기준선이다. 실제 세계의 최소 잔여 비용/모든 동률 최적 간선 판정과 분리했다. 첫 응답의 nullable 판정, 실제 Engine 도착, 후속 회복, 준비·외부·후속 시간과 6결정/24tick 한도, 응답·infrastructure·integrity·기록 실패 및 미시도를 각각 기록한다. 독립 audit는 요약 점수를 믿지 않고 새 kernel/application/Observation/archive/knowledge/receipt/Event/position으로 입력과 결과를 다시 계산한다.

구현 계약·정보 취득 규칙·모든 분모·종료 상태와 CLI는 [EXPERIMENT04_OFFLINE.md](EXPERIMENT04_OFFLINE.md)에 있다. 원본 지시문은 evidence의 implementation-contract.md다.

## 최종 fake 실행 원본

`trials/experiment04-offline-verification-20261008/demo-final`은 초기 A 1/B 1의 fixture 예제다. 실제 초기 fake provider 호출 2, 후속 14, 합계 **16 fake 호출**, 재결합 prefix 6회에는 provider 호출이 없다. 각 prefix별 초기 결정의 실제 제출 및 경험을 보존했다. planned initial 2, 정상 prefix 2, 생성 분기 6, 판정 가능 첫 행동 6, 적절 첫 MOVE 5, 최종 도착 6, 회복 도착 1이다. 적절성 5/6과 운영상 도착률 6/6은 별개 분모다.

| prefix | 조건 | 첫 적절성 | 최종 도착 | 후속 결정/제출 | 후속 tick | 초과 tick |
|---|---|---|---|---|---|---|
| A | ① | true | true | 2/2 | 4 | 0 |
| A | ② | false | true | 4/4 | 8 | 2 |
| A | ③ | true | true | 2/2 | 6 | 0 |
| B | ① | true | true | 2/2 | 6 | 0 |
| B | ② | true | true | 2/2 | 6 | 0 |
| B | ③ | true | true | 2/2 | 6 | 0 |

A②의 C-to-G 실제 엔진 시작 거부는 0tick이며 후속 C-to-D/D-to-G로 회복했다. 첫 false는 최종 도착 뒤에도 유지됐다. ②에 필요한 정보가 없는 사실을 일반 추론 실패로 해석하지 않는다. fake가 선택한 S→A/B를 전체 경로 계획으로 추정하지 않는다.

최종 소스로 fake CLI를 별도 `cli-demo-final`에 실제 실행했다. 이는 같은 fixture의 CLI correctness 재확인으로 표본을 늘려 합산하지 않았다. audit/inspect CLI는 `demo-final`을 읽으며 plan digest를 외부 인자로 전달했다. Plan digest: `53249823675e1ecb3d3bf085074ba6c363d82dcf34c81a151e816c4d25df1451`.

response-final, wait-final, time-final, initial-final, integrity-final, recording-final 및 resealed tamper-final의 실제 원본·audit도 보존했다. refusal/malformed/infrastructure의 first null, 완료 실패 false/unknown final, 초기 실패에서 분기 없음, 6회·24tick 경계, 목표 도착 즉시 종료, global integrity 중단 및 기록 실패 뒤 검증된 첫 점수/unknown final을 확인했다. API usage/cost는 **null / NOT_APPLICABLE_FAKE**다. 과거 실제 LLM 결과와 합산하지 않았다.

## 실행 검증과 결과

현재 bundled Python 3.12.14에 기존 pytest 8.4.2/Ruff 0.16.6/mypy 1.20.2를 사용했다. 기존 `.venv` launcher가 깨져 Python 경로와 기존 site-packages를 지정했으며 패키지를 설치하지 않았다. 오프라인 wrapper는 socket 연결과 실제 키 조회를 금지한다. 전체 기존 HTTP fixture 테스트의 child process에는 실제 키를 읽지 않고 테스트 전용 가짜 값을 넣었다.

| 검사 | 결과 |
|---|---|
| 변경 전 전체 pytest | 1,296 passed / 161.12s |
| E04 + 아키텍처 관련 최종 pytest | 82 passed (E04 신규 73 + 기존 경계 9) |
| 최종 전체 pytest | **1,369 passed / 134.592s**, failures/errors/skipped 0, exit 0 |
| Ruff check . | exit 0 |
| Ruff format --check . | exit 0 |
| strict mypy (src + tests 전체) | exit 0 |
| git diff --check HEAD | exit 0 |
| 최종 fake CLI / audit CLI / inspect CLI | 각각 exit 0 |
| 독립 E04 재구성 및 reseal 변조 검사 | 정상 INCLUDED, recording INCOMPLETE, 변조 EXCLUDED |
| 이전 실제 sequential 40 / one-shot 40 / smoke 1 | 무호출 audit INCLUDED, 기존 보고서와 정확히 일치 |
| 이전 sequential mock / baseline / diagnostic | audit INCLUDED |
| 과거 v1 / M8 정책 | 기존 strict exclusion 유지 (LEGACY_PLAN_MODE_UNBOUND / NEW_PROVENANCE_REQUIRED) |

실행 명령은 offline_cli.py pytest -q --tb=short --basetemp .../pytest-full-verified --junitxml .../pytest-full-verified.xml, python -m ruff check ., python -m ruff format --check ., python -m mypy, git diff --check HEAD다. final_check.py, final_offline.py, 원본 CLI JSON, static/offline/historical/preservation gate JSON 및 로그와 JUnit XML을 evidence에 저장했다. 최종 gate는 static-gate.json이며 개별 종료 코드와 source 일치를 확인한다.

개발 중 실패 로그도 삭제하지 않았다. 처음 신규 테스트의 infrastructure fixture가 기존 ProviderFailure 허용 목록과 달라 3개가 실패했고 E04 synthetic TRANSPORT_ERROR로 바로잡았다. 별도 수신자 테스트 fixture의 manifest digest 누락 1개를 수정했다. 최초 전체 회귀의 scenario import 경계 1개는 application 조립을 별도 runtime 모듈로 옮겨 수정했다. 기존 03 실패는 변경 전/최종 모두 0이다. 검증 script의 존재하지 않는 legacy fake-provider import 오류는 script만 수정했다. 최종 통과 로그와 초기 개발 실패 로그를 명확히 구분한다.

## 검토 ZIP과 남은 항목

새 ZIP은 `trials/review-bundles/experiment04-offline-<UTC unique timestamp>/JourneyMap_Experiment04_Offline_review_2026-10-08.zip`으로만 생성한다. 기존 ZIP을 덮어쓰지 않는다. 전체 src/tests/docs, AGENTS.md, pyproject.toml, 모든 변경 원문/새 untracked 목록과 본문, HEAD·status 전후·전체 tracked diff, 계약·보고서·최종 fake 원본·검증 scripts/gates/로그를 포함한다. MANIFEST.json은 상대 경로·byte count·SHA-256을 제공한다. 자신의 해시는 외부 verification JSON에, ZIP hash는 .zip.sha256에도 기록한다. 압축 해제 결과/manifest/원본 bytes 일치 및 packaging 전후 source·기존 artifact·HEAD/status/diff 불변을 확인한 VERIFIED 보고를 새 bundle 옆에 남긴다. 실제 경로는 최종 응답과 verification JSON을 참조한다.

비밀키·인증 헤더 값·환경변수 덤프·.env·.venv·.git·캐시·pytest 임시 출력·기존 ZIP은 포함하지 않는다. manifest/preservation 목록은 이전 파일들의 해시 증거이며 이전 raw ZIP 본문을 새 ZIP에 복사하지 않는다.

남은 연구 항목은 실험 모델·파라미터·금액 예산·표본 수·본실험 순서와 mirror 매핑이다. 중립 장소명 전단사 fixture는 선택 사항으로 미구현이다. 별도 Research Design/Compatibility 참고 문서는 로컬에서 찾지 못했으므로 첨부의 자체 완결 계약으로 진행했다. 6.1 Sol / High를 실험 대상 모델로 설정하지 않았다. 초안의 6prefix/18분기/114호출은 산술 상한이며 API 승인이나 독립 표본 수가 아니다. 이 작업은 **오프라인 검토 단계에서 종료**한다.
