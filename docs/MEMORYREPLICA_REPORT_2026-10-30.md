# MemoryReplica 통합·오프라인 검증 결과

2026-10-30 KST

MemoryReplica 전용 입력 projection, 재구성/행동 분리 runner와 안정 환경 B 과제를 실제
E04 연구 worktree에 추가했다. 현재 소스를 과거 ZIP 소스로 교체하지 않았다.

## 작업 기준

- 실제 변경 위치: `C:\Users\<user>\.codex\worktrees\experiment03-live-preparation\JourneyMap`
- 브랜치: `codex/experiment03-live-preparation`
- HEAD: `557fb3df10149d7b7738137b7ac1038dfba24e84`
- 현재 채팅 경로인 `C:\Users\<user>\Documents\JourneyMap`은 E03 diagnostic 체크아웃이고,
  `.worktrees\phase1`은 Actual 계열이다. 두 곳의 기존 소스는 수정하지 않았다.
- E04의 `experiments/route_update_session.py`, `adapters/route_update.py`,
  `scenarios/route_update.py`를 연구 worktree에서 직접 확인했다. 기존 E04 구현은
  미추적 파일이므로 그 상태와 원문을 보존했다.
- Task B ZIP의 manifest 13개 항목을 검증했다. 제공 evidence의 기준 소스·설정 140개는
  통합 전 현재 worktree와 SHA-256이 모두 일치했다. 별도 과거 소스를 덮어쓰지 않았다.
- Python 3.12.14 번들 runtime과 기존 Documents 프로젝트 `.venv/Lib/site-packages`를
  사용했다. 의존성 설치·환경 수리·키 조회는 수행하지 않았다.

## 실제 변경 파일

새 구현 5개:

1. `src/journeymap_memoryreplica/projection.py`
2. `src/journeymap_memoryreplica/paired.py`
3. `src/journeymap_memoryreplica/probe.py`
4. `src/journeymap_memoryreplica/task.py`
5. `src/journeymap_memoryreplica/__init__.py`

새 테스트 4개:

1. `tests/test_memoryreplica.py`
2. `tests/test_memoryreplica_paired.py`
3. `tests/test_memoryreplica_task.py`
4. `tests/test_memoryreplica_integration.py`

새 계약·보고서 2개: `docs/MEMORYREPLICA_OFFLINE.md`,
`docs/MEMORYREPLICA_REPORT_2026-10-30.md`.

기존 내용 뒤에만 설명을 추가한 문서 6개: `README.md`, `docs/API.md`,
`docs/ARCHITECTURE.md`, `docs/ERD.md`, `docs/ROADMAP.md`, `docs/TEST_STRATEGY.md`.

첫 전체 검사는 1,627 passed/19 setup errors였다. MemoryReplica를 기존 `journeymap`
패키지 안에 추가하면서 E04의 엄격한 과거 v3→v4 source allowlist에 걸렸다.
기존 gate나 테스트를 완화하지 않고 `src/journeymap_memoryreplica` 독립 패키지로
이동했다. 기존 E04는 이 패키지를 import하지 않으며, 기존 source scope
`src/journeymap/**/*.py+pyproject.toml`과 7개 변경 허용 목록은 그대로다.
MemoryReplica 자체 기록은 두 패키지를 모두 fingerprint한다.

ZIP의 standalone imports를 이 전용 package와 기존 E04 imports로 연결하고 타입·스타일
규칙을 적용했다. 과거 `MANIFEST.json` 강제 검증 대신 현재 source/import root 확인과
실행 전후 source/configuration hash 검사를 적용했다. 전용 profile은
`memoryreplica-task-offline-1`이며 기존 E04 profile과 canonical 상태 구조는 그대로다.

## 검증 결과

- 최종 전체 pytest: **1,646 passed, 실패/오류/skip 0, 311.04초**.
  기존 1,607개와 MemoryReplica 39개를 포함한다.

- MemoryReplica 집중 검사: **39 passed**. ZIP의 31개 검사와 추가 통합 검사 8개다.
- strict mypy: **190개 source/test 파일 통과**.
- 전체 Ruff check 통과, format check **224개 파일 통과**.
- 기존 Git 줄바꿈 설정의 `git diff --check` 통과. LF/CRLF 안내 경고만 있다.
  임시로 autocrlf를 끈 검사는 기존 CRLF를 whitespace로 진단했으며, 원문을 정규화하지
  않고 정상 설정에서 재확인했다. 최초 로그와 최종 로그를 구분해 보존했다.
- 별도 새 Python process에서 socket과 실제 credential 조회를 차단한 CLI 3개 완료:
  B 과제 3조건/6개 공통 경로 witness, 입력 probe 17개, 재구성/행동 pair 9개.
  각 CLI는 현재 source/configuration 145개와 실제 import 위치를 확인했다.

준비 경험은 S→A→C→D→A, 시작은 A/tick13, 목표는 B다. 전체 canonical 세계와 전체
경험은 동일하고 초기 지도는 모든 기억 조건의 현재·과거 입력에서 제외한다.

| 기억 범위 | 초기 입력에서 근거가 완비된 최단 비용 | 실제 세계 최적 비용 |
| --- | --- | --- |
| 없음 | null | 4tick |
| 최근 3개 | 8tick | 4tick |
| 전체 | 4tick | 4tick |

세 기억 조건 모두에서 A→S→B는 실제 Engine으로 4tick, A→D→B는 8tick에 도착했다.
기억 없음에서도 S 재방문 뒤 S→B가 현재 국소 관찰로 나타난다. `null`은 초기 입력에
완전한 경로 근거가 없다는 의미이며 도착 불가능이나 실패를 뜻하지 않는다.

재구성과 첫 행동 요청은 같은 context에서 응답 전 독립적으로 고정된다. 재구성 응답,
recall score, truth optimum은 행동 입력으로 전달하지 않는다. 후속 행동에도 동일한
기억 정책을 적용한다. JSON 실패, Engine 거부, 미도달, fixture 소진, 시간 한도는
별도로 기록한다. Engine 거부 후 정상 이동으로 회복하는 실제 offline 실행도 검사했다.

## 보존

작업 전 inventory 35,786개 중 **35,780개 파일은 바이트가 동일**하다. 공용 문서 6개는
추가 절 앞의 기존 원문 hash가 동일하다. 보존 실패는 없다.

- 기존 소스 139개와 테스트 파일 43개가 모두 동일하다.
- 기존 실험 기록 파일 35,570개가 모두 동일하다.
- inventory 범위: src/tests/docs/trials 및 README/AGENTS/pyproject.
  cache, pytest 이름 임시 디렉터리, bytecode, DB 및 `.env`는 제외했다.
- 운영 DB에는 접근하지 않았다. 실제 승인 파일·실제 실행 output/claim을 만들지 않았다.
  기존 전체 테스트의 합성 입력은 지정한 pytest 임시 경로에서만 사용한다.
- 실제 LLM/API 호출, 실제 키 조회, commit/push/merge, Git refs 변경은 수행하지 않았다.

## 남은 연구 범위와 기록

이번 결과는 **첫 안정 환경에서 입력 근거의 과제 관련성과 프로그램 correctness**다.
실제 LLM의 기억 활용·행동 효과를 측정하지 않았다. 새 실패/갱신 환경, 두 번째 지도,
장소명 치환, 실제 모델·추론·입출력 예산과 반복 실험은 완료 범위에 포함하지 않는다.
같은 tick의 여러 신규 정보는 E04 `learned_at` 기반이며 일반적인 획득 순서 모델은 없다.
현재 source/hash 검사는 과거 raw 전부의 독립 audit나 외부 provenance 증명도 아니다.
F1 재현이나 별도 운영 검토를 재시작하지 않았다.

검증 자료는 `C:\Users\<user>\Documents\JourneyMap\artifacts\memoryreplica-integration-20261030`
아래에 있다:

- `source-comparison.json`, `input-zip-sha256.txt`: 입력 패키지/현재 소스 대조.
- `before-files.json`, `preservation-result.json`: 작업 전 inventory와 보존 판정.
- `pytest-full-final.log`, `pytest-full-final.xml`, `static-isolation-final.log`: 최종 전체 검사 근거.
- `entrypoints-isolated-final.log`, `entrypoints-isolated-final/*.json`: 별도 process CLI 결과.
- `CHANGESET.json`, `implementation.patch`, `final-files/`: 실제 적용 변경을 검토할 자료.
