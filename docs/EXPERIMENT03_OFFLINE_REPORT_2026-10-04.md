# Experiment 03 오프라인 구현·검증 기록

2026-10-04 KST. [계약과 일반 실행법](EXPERIMENT03_BASELINE_DIAGNOSTIC.md).
외부 LLM/API 호출 0회, 유료 실행 0회. commit/push/PR/merge/Notion 편집은 수행하지 않았다.

## Checkout과 변경 범위

- 저장소: `C:\Users\user\Documents\JourneyMap`
- remote: `https://github.com/Olcho/JourneyMap.git`
- 최초 branch: `codex/memory-horizon-readiness-audit`
- 최초 HEAD: `57ff9c301167338da142aab253d0af77db786603`; 미커밋 변경 없음.
- fetch한 공식 main: `97a5b6a24079b1f3bf3afb45bac85d30c1468336`.
  기존 HEAD와 실행 코드는 같고 AGENTS/README/ROADMAP만 달랐다.
- 기존 branch와 raw를 보존하고 main에서 `codex/experiment03-pickup-diagnostic` 생성.
- 구현 기준/최종 HEAD: `97a5b6a24079b1f3bf3afb45bac85d30c1468336`로 동일.
  새 구현은 working tree에 있으며 자동 commit하지 않았다.

총 변경 범위는 기존 파일 8개 수정, 새 파일 9개다.

| 파일 | 변경 |
| --- | --- |
| `src/journeymap/adapters/horizon_baseline.py` | 상태 없는 직전 장소 규칙 fixture, 입력 전제 오류 |
| `src/journeymap/scenarios/alderwick/pickup_cue.py` | 별도 초기 안내 상태, Well perception/contributor, prefix |
| `src/journeymap/bootstrap.py` | 별도 MOVE/WAIT-only 진단 composition |
| `src/journeymap/adapters/pickup_cue_prompt.py` | 고정 prompt, 진단 projection, Full prefix, concrete fixture |
| `src/journeymap/experiments/pickup_cue.py` | 실제 prefix, one-shot 평가, 실패 기록, 새 export/40-case 실행 |
| `src/journeymap/experiments/pickup_cue_audit.py` | Provider-free replay/입력 재구성/평가 재계산, inventory audit |
| `src/journeymap/examples/memory_horizon.py` | `previous-place` CLI 선택, k=1 강제 |
| `src/journeymap/examples/pickup_cue.py` | 고정 offline fixture CLI, 별도 audit/replay |
| `tests/test_pickup_cue.py` | 58개 신규 검증 case; 내부 40-case matrix 포함 |
| `docs/EXPERIMENT03_BASELINE_DIAGNOSTIC.md` | 기준선 규칙·진단 프로토콜·실행법·해석 한계 |
| `docs/EXPERIMENT03_OFFLINE_REPORT_2026-10-04.md` | 이 검증 기록 |
| `README.md`, `docs/API.md`, `docs/ARCHITECTURE.md`, `docs/ERD.md`, `docs/ROADMAP.md`, `docs/TEST_STRATEGY.md` | 현재 상태와 상세 문서 링크 |

기존 movement/WaitHandler, perception pipeline, EventTraceArchive, Recency selection,
semantic projection, strict parsing/binding와 ReplayHarness를 재사용했다.
기존 Controller guard, cursor fixture, M8/Phase 1/Recency/Horizon v1/v2 prompt/export/audit,
Distinct Places 8지표와 primary_success, live 12-allocation 계획은 변경하지 않았다.
기존 테스트의 assertion/skip/xfail도 변경하지 않았다. Core나 dependency 변경은 없다.

## 실제 코드 기준선 결과

전용 `fake / memory-horizon-previous-place-fixture-1` 결과:

- target 왕복 MOVE 6회, decision 6 / tick 12에서 첫 완료.
- 이후 unit WAIT 12회, tick 24 종료.
- decisions/submissions 각각 18, coverage 3, repeat 0, strict success true.
- 기존 export schema 3에서 Provider-free replay 일치, correctness-2 audit INCLUDED.
- 같은 입력을 반복하거나 호출 순서/인스턴스/metadata를 바꿔도 같은 선택.

직전 경험 1개를 사용하는 코드 규칙이며 No Event Memory나 LLM 결과가 아니다.
전체 decisions 18은 효율 점수가 아니다.

## 실제 진단 matrix 결과

40개 조합은 모두 audit INCLUDED다. 실제 준비 제출 200회(각 5회), 평가 fixture 호출 40회,
평가 제출 40회다. 각 prefix는 빈 Knowledge, Square/tick 7, cue sequence `6-d`를 만족했다.
준비 중 Provider 호출은 없고 current semantic input/model configuration은 각각 1종이다.

| d | No Event Memory | k1 | k2 | k3 | Full prefix |
| --- | --- | --- | --- | --- | --- |
| 1 | 없음 | 있음 | 있음 | 있음 | 있음 |
| 2 | 없음 | 없음 | 있음 | 있음 | 있음 |
| 3 | 없음 | 없음 | 없음 | 있음 | 있음 |
| 4 | 없음 | 없음 | 없음 | 없음 | 있음 |

cue가 범위 밖인 A/B 10쌍은 전체 ProviderRequest가 동일하고, 범위 안인 10쌍은 과거 안내 값
하나만 다르다. 전달 count는 0/1/2/3/5다. fixture는 cue가 있는 20개에서 target MOVE,
없는 20개에서 WAIT를 실행했다. 이는 fixture correctness이며 LLM 성공률 결과가 아니다.

모든 새 export에서 Engine replay, 실제 perception을 이용한 입력·prompt 재구성, 평가
재계산을 확인했다. baseline/matrix 원본 **59개 파일**은 audit/replay 전후 hash가 같았다.
saved success, prompt와 재계산한 hash, 선택 trace/count, 원본 notice와 재계산한 digest,
receipt, checkpoint, distance, raw response, model 설정 변조를 재봉인해도 거부했다.
누락·중복·오연결 matrix, 다른 actor/run/future/현재 경험, 새 contributor의 숨은 필드도 검사했다.

실패 검증은 올바른/다른 목적지 MOVE, WAIT, 실제 REJECTED/FAILED/UNKNOWN,
malformed/duplicate/nonfinite/invalid UTF-8 JSON, bool/2 duration, authority injection,
refusal/incomplete, provider/transport 예외, 준비/평가 관찰 오류와 기록 오류를 포함한다.
불완전 원본은 원 시도를 남기고 무결성 EXCLUDED와 audit.task_success null로 구분한다.

## 전체 품질 gate

기존 `.venv` launcher는 사라진 Python312 경로를 참조했다. 환경을 덮어쓰거나 설치하지 않고
번들 **Python 3.12.14**, 기존 `.venv/Lib/site-packages`, `src` PYTHONPATH를 사용했다.

```powershell
$journeyPython = 'C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$env:PYTHONPATH = "$PWD\src;$PWD\.venv\Lib\site-packages"
& $journeyPython -m pytest -q --basetemp trials/experiment03-verification-20261004/final-pytest -o cache_dir=trials/experiment03-verification-20261004/pytest-cache
& $journeyPython -m ruff check .
& $journeyPython -m ruff format --check .
& $journeyPython -m mypy
git diff --check
```

- 최종 전체 pytest: **1175 passed in 81.55s**, 기존 1117 + 신규 58. skip/xfail/경고 없음.
- Ruff check: 통과.
- Ruff format check: 152 files already formatted (로컬 offline 검증 wrapper 포함).
- strict mypy: Success, 133 source files.
- git diff check: 통과. Git의 LF→CRLF 안내는 diff 오류가 아니다.

초기 targeted run의 pytest cache 권한 경고는 새 cache 경로를 지정해 해결했다.
초기 실패 주입에서 발견한 기록 오류의 잘못된 INCLUDED 판정을 수정했고 최종 gate에 포함했다.

## 실제 historical raw gate

`trials/m8-first-official-sol`의 실제 공식 원본 **17개 파일**을 읽기 전용으로 사용했다.
Provider-free replay 일치, tick 24, 최종 digest는 기존 값
`c8357de86dc6066843a24e047e241b824f565be2d12ba540317e46f639c07733`이다.
17개 SHA-256 모두 전후 불변이다. 원본 schema의 strict audit는 기존 그대로
`EXCLUDED / NEW_PROVENANCE_REQUIRED`이며 INCLUDED로 소급 재분류하지 않았다.
report/cache는 모두 원본 밖에 저장했다.

## 재현 명령과 보존된 산출물

이번 CLI 실행은 socket 연결을 금지하는 로컬 검증 wrapper를 통해 실행했다.
아래 output은 이미 존재하므로 다시 실행하려면 새 경로를 지정한다.

```powershell
& $journeyPython trials/experiment03-verification-20261004/offline_cli.py journeymap.examples.memory_horizon --fixture previous-place --memory recency-k1 --trial-id experiment03-baseline-final --output trials/experiment03-verification-20261004/baseline-final
& $journeyPython trials/experiment03-verification-20261004/offline_cli.py journeymap.examples.pickup_cue --matrix --output trials/experiment03-verification-20261004/matrix-final
& $journeyPython -m journeymap.examples.pickup_cue --audit-matrix trials/experiment03-verification-20261004/matrix-final
& $journeyPython -m journeymap.examples.pickup_cue --replay trials/experiment03-verification-20261004/matrix-final/inn-d2-recency-k2
```

- `trials/experiment03-verification-20261004/baseline-final`: 기준선 raw.
- `trials/experiment03-verification-20261004/matrix-final`: 40 case raw + matrix manifest.
- `trials/experiment03-verification-20261004/baseline-final-cli.json`, `matrix-final-cli.json`: CLI 결과.
- `trials/experiment03-verification-20261004/offline-gate.json`: 재검증 결과, count/equality와 59개 hash.
- `trials/experiment03-verification-20261004/historical-gate.json`: 공식 M8 gate와 17개 hash.

`trials/`는 기존 정책대로 Git에서 제외되는 로컬 artifact다. 최종 matrix가 기록한 source
digest와 현재 실행 소스의 일치를 별도로 확인했다. 검증 중간 산출물도 삭제하지 않았다.

## 미해결 사항과 해석 한계

요청한 오프라인 구현 범위의 미해결 blocker는 없다. 기존 `.venv` launcher 복구는 별도 환경
정리 사항이며 이번 검증은 확인된 Python 3.12 대체 경로로 완료했다. snapshot은 durable journal이나
재개 기능이 아니며, 장애 원본이 완전하게 재구성되지 않으면 integrity 제외로 남는다.

실제 LLM/API 실행은 하지 않았다. 연구 construct validity와 behavioral effect는 테스트 통과로
결론 내리지 않는다. 후속 live 연결은 별도의 wire/transport 검토, 모델·반복 수·종료·제외·
replacement·randomization·분석 계획 동결과 사용자 승인이 필요하다.
