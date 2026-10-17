

## 2026-10-17 KST 후속 재판정 — 현재 판정

**현재 판정은 “수정 필요”를 유지한다. F1 상태는 “이번 전체 실행에서 미재현, 원인 미확정”이다.** 위의 최초 검토·실패 traceback·기준 커밋 대조·검사 수치는 당시의 기록으로 그대로 보존한다. 이번 통과를 수정 완료, 환경 문제의 확정, 최초 실패의 무효화 또는 F1 종결로 해석하지 않는다. 이 판정은 지금 특정 소스 수정을 지시한다는 뜻이 아니라, 기존 검토에서 요구한 원인 확인과 검증 종결 근거가 아직 부족하다는 뜻이다.

### 직접 읽은 증거와 사용자 제공 결과

사용자가 제공한 전체 실행 결과는 **`1378 passed in 552.08s (0:09:12)`**다. 이 재판정에서는 pytest를 다시 실행하지 않고 지정된 `summary.json`을 직접 읽고 진단 도구의 현재 소스와 대조했다.

- 증거: `C:\Users\USER\AppData\Local\Temp\journeymap-checkpoint-probe-c3teu4nr\summary.json`
- SHA-256: `e4df2a7c4ec571538e5ac9069967a89d0e776fc7944bbefe7d3ecb7ebdf42cdb` (643 bytes).
- Python: 3.12.10, pytest: 8.4.2, Windows: 10.0.19045.
- executable: `C:\Users\USER\JourneyMap-Actual\.venv\Scripts\python.exe`.
- cwd: `C:\Users\USER\JourneyMap-Actual`.
- checkpoint module: `C:\Users\USER\JourneyMap-Actual\src\journeymap\experiments\checkpoint.py`.
- nodeid: `tests/test_life_checkpoint.py::test_speed_and_view_polling_preserve_entire_stream`.
- `target_outcome="passed"`, `snapshot_calls=164`, `pytest_exit_code=0`.

`summary.json`가 직접 뒷받침하는 사실은 해당 대상 테스트의 call 단계 통과, snapshot 관측 횟수 및 그 pytest 실행의 정상 종료다. **전체 1378개와 552.08초는 사용자 제공 실행 결과이며 이 파일의 필드에는 없다.** summary에는 실행 인자, 수집 목록, 전체 pass/skip/xfail 수, 소요 시간, 실행 당시 HEAD/source hash도 없다. 증거 폴더를 숨김 항목 포함 재귀 조회한 결과 파일은 `summary.json` 하나였다. 따라서 이 폴더만으로 전체 실행의 수집 범위·시간·당시 source identity를 독립 재구성했다고 주장하지 않는다. 사용자 제공 결과와 summary의 성공 상태는 서로 모순되지 않는다.

실패 snapshot, `failure.txt`, 변경 경로 및 실패 후 source inventory는 이번 폴더에 없다. 현재 진단 도구는 실패했을 때만 이를 저장하므로, 대상 테스트가 통과한 이번 결과와 일치한다. 파일이 없다는 사실은 최초 실패의 원인을 설명하지 않는다.

### 진단 도구를 붙인 통과의 해석

기존 작업으로 존재하던 `scripts/diagnose_checkpoint_view.py`를 읽었으며 수정하거나 실행하지 않았다. 현재 파일 SHA-256은 `a59602b4685828b1bd17c40cb74579ce940e7c69353ddf2885520d866e50fcf4`다. 실행 당시 도구 hash가 summary에 기록되어 있지 않아, 이것은 재판정 시점의 도구 검토다.

현재 소스의 `:74`–`:88`은 원래 `snapshot(session)`을 한 번 호출해 동일 payload를 반환하고, 마지막 두 sample을 보관한 뒤 원래 함수를 복원한다. 기존 `checkpoint_digest`, 테스트 assertion, fixture를 바꾸거나 실패를 성공으로 바꾸는 코드는 없다. `:126`–`:129`는 pytest에 전달한 인자를 실행하고 그 종료 코드를 summary에 저장한다. 따라서 이번 증거는 대상 테스트의 실제 통과를 지지한다.

동시에 wrapper는 payload 참조의 수명과 처리량을 바꾸므로 타이밍·GC 등 실행 조건에 전혀 영향을 주지 않았다고 증명할 수 없다. 이것은 진단 도구가 원인이거나 실패를 숨겼다는 발견이 아니다. **진단 부착 실행의 통과만으로 비부착 실행의 기존 실패 원인까지 설명할 수 없다는 검증 한계**다. 최초 digest 불일치가 canonical 상태, driver/기록, source compatibility 또는 다른 요소에서 나왔는지는 여전히 식별되지 않았다.

### 기존 통과 조건과의 대조

| 기존 요구·제한 | 현재 증거 | 충족 여부와 판정 영향 |
| --- | --- | --- |
| F1의 실패 원인을 확인 | 새 실행에서는 대상 테스트 통과, 최초 실패의 필드별 before/after 증거 없음 | **미충족**. “이번 전체 실행에서 미재현, 원인 미확정”으로 유지 |
| 원인에 맞는 필요한 조치를 하고 검증 | 원인 특정·조치 필요성 판단·조치 효과 검증 자료 없음. 현재 소스·기존 테스트는 대상 HEAD와 동일 | **미충족**. 통과만으로 수정 완료를 선언하지 않음; 불필요한 소스 수정을 요구하지도 않음 |
| 같은 노트북의 전체 pytest gate 회복 | 사용자 결과 1378 passed / 552.08초, summary의 pytest exit 0 및 대상 passed 확인 | **진단 부착 실행의 성공 증거 추가**. 전체 상세 로그·수집 정보는 폴더에 없고 비부착 전체 재검사 및 F1 원인 설명을 대체하지 못함 |
| Ruff lint/format, strict mypy, diff gate | 최초 직접 검사 통과 기록 유지. 후속 작업에서는 문서 변경 검증과 소스 보존 확인만 수행 | 최초 근거 유지. 이번에 lint/mypy 전체를 다시 돌렸다고 기록하지 않음 |
| 과거 실제 4096 원본 report의 동결 소스 replay | 이번 제공 폴더에도 해당 원본 없음 | **미검증 유지**. 최초 문서에서 별도 제한으로 둔 범위이며 새 pytest 성공으로 채워지지 않음 |

8192 계획·연구용 4096 상한·승인/회계/replay에 대한 최초 검토의 긍정적 근거는 유지된다. 새 성공 결과는 전체 suite와 F1의 재현성 평가에 유의미한 긍정적 증거다. 그러나 최초 통과 조건에 명시했던 **원인 확인**이 충족되지 않아 “통과” 또는 “조건부 통과”로 전환하지 않는다. 최초 실패가 계속 발생한다고 주장하는 것도 아니다.

### 다음에 필요한 구체적 증거

1. **이번 성공 실행의 전체 로그와 실행 식별 정보**: 실제 명령·pytest 인자, stdout/stderr, 종료 코드, 수집된 nodeid 목록 또는 JUnit 결과, pass/skip/xfail 수, 소요 시간, 당시 HEAD·패키지 source hash·진단 도구 hash. 이것이 있으면 사용자 제공 1378개·552.08초와 summary를 같은 전체 실행으로 연결할 수 있다. API 키나 전체 환경변수 덤프는 필요하지 않다.
2. **원래 비부착 전체 실행과의 대조**: 같은 HEAD·Python·도구 버전·계정에서 진단 없이 전체 pytest를 새 임시/cache 경로로 실행한 로그를 보존하고, 진단 부착 결과와 비교한다. 계정, 명시적으로 설정한 `PYTHONHASHSEED`, 실행 인자와 경로 차이는 비밀값 없이 기록한다. 비부착 실행도 통과하더라도 그것만으로 F1 종결이나 환경 원인 확정을 선언하지 않는다.
3. **실패가 다시 발생할 때의 실제 불일치 근거**: 원래 assertion이 사용한 연속 before/after payload와 그 digest, tick·session·speed·조회 actor·호출 순서, 타입·컨테이너 순서를 보존한 변경 경로를 확보한다. source compatibility가 다르면 각 경계의 소스 파일 목록·hash·읽기 오류를 추가 확인한다. 현재 도구의 실패 후 inventory는 나중 시점의 자료이므로 과거 snapshot 경계의 파일 상태로 소급 해석하지 않는다. 이 자료로 원인과 영향 범위를 특정하고, 필요한 최소 조치 또는 최초 실패 관측의 대체 설명을 검증한다.
4. **과거 실제 기록 검증을 닫으려면 별도 원본**: 보존된 4096 실제 report와 hash를 확보해 동결 커밋 `b7717c2112c076b6f194d26ca0507d13b5b8a4d1`에서 Provider/key/socket/DNS 차단 replay를 실행한 결과, 동일 원본을 새 소스에서 mismatch로 거부한 결과가 필요하다. report 변환·재봉인·임의 재구성은 증거가 아니다.

성공 횟수를 임의로 늘려 통과 기준을 대신하지 않는다. 추가 실행이 실패를 포착하지 못하면 F1은 미재현·미확정 상태로 남고, 원인 특정 또는 동등하게 검증 가능한 설명이 확보되기 전에는 해결됨으로 표시하지 않는다.

### 보존과 금지 작업 준수

후속 시작 상태는 브랜치 `codex/actual-output-budget-8192`, HEAD `739f08d34325106cf2eaaacc3fadcdecd0858ddd`였고, 미추적 파일은 기존 검토 문서와 `scripts/diagnose_checkpoint_view.py` 두 개였다. 두 기존 작업을 보존했다. 후속 시작 시 검토 문서 hash는 `465a09b6772322f15305605c5044523606f20aede1de6a14fb27bc80f4cc76a4`로 최초 저장본과 같았다. 이 절은 그 원문 뒤에만 추가한다.

현재 패키지 source hash는 최초 검토와 같은 `f29e9631f80b19d832e83ce7969318bae061a88b543424ed7254499aca91f973`다. `git diff --exit-code -- src tests`로 추적 소스·기존 테스트의 무변경을 확인했다. 이 후속 작업에서 pytest 재실행, 실제 API 호출, 키 조회, 승인 파일 생성, commit/push/merge는 하지 않았다. summary와 증거 폴더도 수정하지 않았다.

**현재 “수정 필요” 판정 및 향후 검토 통과는 유료 실행 승인을 의미하지 않는다.**
