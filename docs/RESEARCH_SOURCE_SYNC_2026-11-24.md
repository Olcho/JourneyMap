# JourneyMap 소스 계보와 오프라인 재현

## 구현 위치

| 계열 | 소스와 진입점 |
| --- | --- |
| E03 Memory Horizon | `src/journeymap/examples/{memory_horizon,pickup_cue,pickup_cue_live,pickup_sequential}.py` 및 대응 실험·감사 모듈. [수령처 진단](EXPERIMENT03_BASELINE_DIAGNOSTIC.md) · [연속 행동](EXPERIMENT03_PICKUP_SEQUENTIAL.md) |
| E04 Information Change / Replanning | `src/journeymap/scenarios/route_update.py`와 `adapters/route_update*.py`, `experiments/route_update*.py`. [오프라인 계약](EXPERIMENT04_OFFLINE.md) · [제한 pilot](EXPERIMENT04_LIMITED_PILOT_PREPARATION.md) |
| Actual | `actual/src/journeymap`의 concurrent Engine, 생활 시뮬레이션, Phase 4.4, Probe 및 MemoryReplica 운영 원장. [실행 문서](../actual/README.md) · [Phase 4.4](../actual/docs/PHASE4_4_OUTPUT_BUDGET.md) |
| MemoryReplica v1 | `src/journeymap_memoryreplica`의 실험 환경, `experiments/memoryreplica-v1`의 동결 입력·N/S/F/M/R 기억 선택·F1/E·집계, `actual/src/journeymap`의 실행 조정 및 감사. [재현 절차](../experiments/memoryreplica-v1/README.md) · [600건 결과](MEMORYREPLICA_RESULTS_2026-11-23.md) |

루트와 `actual/`은 각각 `journeymap` 패키지를 설치한다. 각 디렉터리에서 별도 Python 3.12 가상환경을 사용한다. Actual이 MemoryReplica 연구 코드를 호출할 때는 별도 Python 자식 프로세스에서 연구 소스를 가져온다.

## 검증 명령

다음은 저장소 루트에서 실행하는 Bash 예시다. Windows에서는 `python3.12`를 `py -3.12`로, 각 가상환경의 `bin/python`을 `Scripts/python.exe`로 바꾸면 된다.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff format --check src tests
.venv/bin/python -m mypy
.venv/bin/python -X utf8 -m pytest

.venv/bin/python -X utf8 -m journeymap.examples.memory_horizon --fixture previous-place --memory recency-k1 --trial-id offline-e03 --output trials/offline-e03
.venv/bin/python -X utf8 -m journeymap.examples.memory_horizon --audit trials/offline-e03
.venv/bin/python -X utf8 -m journeymap.examples.memory_horizon --replay trials/offline-e03
.venv/bin/python -X utf8 -m journeymap.examples.route_update fake --output trials/offline-e04
.venv/bin/python -X utf8 -m journeymap.examples.route_update_live mock trials/mock-e04 --stage smoke
.venv/bin/python -X utf8 -m journeymap.examples.route_update_live audit trials/mock-e04

cd actual
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m ruff check src tests
.venv/bin/python -m ruff format --check src tests
.venv/bin/python -m mypy
.venv/bin/python -X utf8 -m pytest
.venv/bin/python -X utf8 -m journeymap.examples.probe_execution preflight --output trials/actual-preflight
.venv/bin/python -X utf8 -m journeymap.examples.probe_execution audit trials/actual-preflight/report.json
```

E04 제한 pilot 호환성 검사에는 보존된 실제 witness와 CPython 3.12.14가 필요하다. 해당 파일에 접근할 수 있으면 `JOURNEYMAP_E04_PILOT_FIXTURE`에 그 파일의 경로를 지정한다. E04의 초기 프로토콜 복원 검사는 `trials/experiment04-offline-verification-20261008/`에 따로 보존한 자료가 필요하다. 공개 CI에서는 이 비공개 fixture에 의존하는 검사를 건너뛴다. 위 replay, fake 및 recording-mock 검사는 Engine·권한·입력·감사의 일관성을 검증한다. 실제 모델의 행동 결과는 별도 실행 기록으로 평가한다.

## 실행 소스와 보존 기록

- E03 오프라인 진단의 기준 커밋은 `557fb3df10149d7b7738137b7ac1038dfba24e84`다. 이후 E03 수령처 연속 행동의 10월 6일 실행은 working source SHA-256 `a3f9fe5ac39e87be701ecb578ec342516fd35b41bcf57c535758e55e787c4ec0`을 기록했다. 실행 당시 plan·run·audit는 원본 연구 작업공간의 `trials/live-pickup-sequential-sol-20261006/`에 보존한다.
- E04 10월 10일 승인 smoke와 10월 11일 교정 pilot의 working source SHA-256은 각각 `94150b4f5a635a8931f297249a3306f094a77d8df43435911a9157be9eb5de94`, `ee2ab56b42567a5c263b7f46223891a0255d61c68e8c945d470c5885cbda24c9`다. 실행·중단 기록과 source-before 및 감사는 원본 연구 작업공간의 `trials/experiment04-approved-*-run-*`에 보존한다. 공개 코드는 후속 작업을 포함한다.
- Actual의 4,096 출력 버전 `b7717c2112c076b6f194d26ca0507d13b5b8a4d1`과 별도 8,192 버전 `739f08d34325106cf2eaaacc3fadcdecd0858ddd`는 각각 기존 원격 브랜치에 있다. 로컬 `.worktrees/phase1`은 해당 이후의 오류 경계, Probe와 독립 retention 원장까지 포함한다. 두 출력 한도의 기록은 해당 source/plan으로 감사한다.
- MemoryReplica의 두 실행 바인딩에 있는 연구 소스 148개와 입력 파일 16개(첫 평가)·13개(후속 평가)의 SHA-256이 각각 일치했다. 11월 23일 완료된 후속 평가에 사용된 Actual 소스 144개는 이 저장소의 Actual 최초 가져오기 커밋 `6e97a8f0cc2227ae7fa78cc60b35a9fec919e1c2`의 체크아웃과 일치한다. 이후 공개 레이아웃에 맞춰 Git source-tree 경로만 상대 경로로 바꿨다. 원래 바인딩과 소스 지문은 [SHA-256 목록](../experiments/memoryreplica-v1/source-sha256.json)에 기록했다.
- 첫 평가 시작 시의 Actual 소스 144개는 `artifacts/memoryreplica-v1-main-recovery-2183/frozen-main-1001/`에 남아 있다. 저장 복구 및 DNS 후속 단계의 소스·실행기도 해당 바인딩과 일치했다. HTTP 503 후속 단계의 소스는 두 보존본의 파일을 해당 실행 바인딩과 대조하여 144개 전부 확인했다. 첫 평가의 최종 점수 export와 후속 독립 원장으로 600건을 다시 집계했다.

공개 범위는 구현, 검증 코드, 생성한 실험 입력, 프롬프트, SHA-256 목록, 결과 집계표다. 실제 Responses, 승인·사람 식별 정보, 운영 SQLite와 백업 및 독립 witness fixture는 로컬에 보존한다. 저장된 전체 응답의 재생·원장 감사에는 해당 원본과 실행 시점 소스가 필요하다.
