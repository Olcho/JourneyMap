# JourneyMap Actual Phase 4.4 독립 검토

검토일: 2026-10-16 KST. 구현 보고서를 증거의 출발점으로만 사용하고, Git 비교·현재 소스·노트북의 실제 검사 결과로 판단했다.

## 대상과 보존

- 위치: `C:\Users\USER\JourneyMap-Actual`
- 시작 브랜치: `codex/actual-output-budget-8192`
- 시작 HEAD: `739f08d34325106cf2eaaacc3fadcdecd0858ddd`
- 비교 기준: `b7717c2112c076b6f194d26ca0507d13b5b8a4d1`
- 시작 `git status --short`: 출력 없음. 브랜치 전환·reset·commit·push·merge를 하지 않았다.
- 대상 소스와 기존 테스트는 수정하지 않았다. 이 검토 문서만 대상 저장소에 추가한다. 추가 검사 파일과 합성 증거는 `C:\Users\USER\Documents\JourneyMap\review-temp`에 분리했다.
- 실제 API 호출, 실제 키 조회, 실제 승인 파일 생성, canonical 실행 registry 생성·초기화는 하지 않았다. 기존 테스트 및 추가 검사의 메모리 내 합성 grant와 임시 SQLite는 실행 승인이 아니다.

대상 저장소의 `AGENTS.md`, `README.md`, `docs/JOURNEYMAP_0.1_SPEC.md`, `docs/ARCHITECTURE.md`, `docs/API.md`, `docs/ERD.md`, `docs/TEST_STRATEGY.md`, `docs/ROADMAP.md` 및 지정된 `ACTUAL_START_HERE.md`, Phase 4.4 계약·보고서를 확인했다. 회계·replay의 의미는 Phase 4.3.1/4.3.2 감사 계약도 대조했다.

## 최종 판정

**수정 필요 — 전체 회귀 gate의 실패 원인 확인 및 회복이 필요하다.** 8192 변경 자체에서 새로운 승인·회계·replay 경계 결함은 발견하지 못했고, 관련 기존 107개와 신규 22개 및 추가 오프라인 30개는 통과했다. 그러나 요청한 전체 pytest는 1377 passed / 1 failed였으므로 보고된 1378개 전체 통과를 독립 확인했다고 판정할 수 없다. 실패는 기준 커밋에서도 관측됐으며, Phase 4.4가 새로 유발한 결함으로 단정하지 않는다.

통과 판정으로 전환하려면 F1의 원인을 확인하고 소스 또는 테스트·실행 환경에 필요한 조치를 한 뒤 같은 노트북의 전체 gate를 다시 충족해야 한다. 과거 실제 원본 replay는 별도의 미검증 범위로 남는다. 이 검토에서는 문제를 수정하지 않았다.

**이 검토의 통과 또는 조건부 통과는 실제 키 조회·승인 파일 생성·유료 실행 승인을 의미하지 않는다.**

## 심각도별 발견 사항

### P0 / P1: 발견 없음

검토 범위에서 유료 실행 승인 우회, 기존 연구 4096 상한 확대, 추론 토큰 중복 과금, 8192 초과 usage의 확정 가격 승격, 새 소스의 과거 binding 수락은 발견하지 못했다. 이것이 모든 입력에 대한 결함 부재를 증명하는 것은 아니다.

### F1 · P2: 전체 회귀에서 조회 전후 checkpoint 불변식 실패

- 위치: `tests/test_life_checkpoint.py:188`; 검증 대상 읽기 경로 `src/journeymap/experiments/live.py:101`; snapshot/digest 경로 `src/journeymap/experiments/checkpoint.py:267`, `:521`.
- 재현: 대상 저장소에서 `.venv\Scripts\python.exe -m pytest`. 전체 1378개 수집 후 해당 테스트만 실패했고 종료 코드는 1이었다.
- 관측: `live.view(None)` 및 actor별 조회를 한 뒤 `checkpoint_digest(live.session)`가 조회 직전 값과 달랐다.

```text
tests/test_life_checkpoint.py:188
assert checkpoint_digest(live.session) == before
before: b29a617885e779681f138447952f8a5a11482e4a43970602d6d1c030dfdc1a7f
after:  e257e1e1526dceceade3ab42f6206d80f25557f01946471b6af5c5a5682c5401
1 failed, 1377 passed in 695.96s
```

영향: UI 조회가 continuation 상태에 영향을 주지 않는다는 기존 연구 불변식의 전체 회귀 gate가 충족되지 않는다. checkpoint digest에는 canonical 상태 외에 source compatibility와 driver/기록 상태도 포함되므로, 이 실패만으로 World Truth mutation 또는 8192 경계 훼손이 발생했다고 단정할 수는 없다. 원래 실패 snapshot의 상세 필드 차이는 저장되지 않아 근본 원인은 미확정이다.

독립 대조 결과:

1. 같은 테스트의 현재 소스 단독 실행은 `1 passed in 68.44s`였다.
2. 별도 snapshot 관측 wrapper를 붙인 현재 소스 단독 실행도 `1 passed in 50.96s`였다. 실패를 재현하지 못했으므로 필드 차이를 얻지 못했다.
3. 기준 커밋을 `git archive`로 `review-temp\frozen-base`에 분리하고 같은 기존 테스트를 대조 실행한 결과는 **1 failed in 48.00s**, 같은 `:188` 불변식 실패였다. pytest cache 경로의 계정 간 ACL 경고 2개도 있었지만 실패 assertion은 cache write가 아니라 digest 비교다. 원래 소스와 현재 소스의 해당 테스트, `live.py`, `checkpoint.py`는 Git blob이 동일하다.

즉 현재 환경에서 실패와 단독 통과를 모두 관측했고, 기준 소스에도 실패가 존재한다. 새 출력 한도 변경의 회귀라고 지목하거나 테스트를 완화해 덮을 근거는 없다. 실패 원인을 조사해 기존 gate를 회복해야 한다. 검토 대상 파일은 수정하지 않았다.

재현·대조 명령:

```powershell
& .\.venv\Scripts\python.exe -B -m pytest tests/test_life_checkpoint.py::test_speed_and_view_polling_preserve_entire_stream -q --tb=short -o cache_dir=C:\Users\USER\Documents\JourneyMap\review-temp\pytest-cache --basetemp=C:\Users\USER\Documents\JourneyMap\review-temp\checkpoint-probe
& .\.venv\Scripts\python.exe -B C:\Users\USER\Documents\JourneyMap\review-temp\inspect_view_invariant.py
& .\.venv\Scripts\python.exe -B C:\Users\USER\Documents\JourneyMap\review-temp\inspect_view_invariant.py --baseline
```

전체 실행·기준 커밋 대조는 호스트 계정에서, 현재 소스의 단독 대조는 격리 계정에서 실행됐다. 계정과 임시 파일 ACL의 차이도 남은 조사 변수이며, 이를 원인으로 확정하지 않았다. 재실행 시 새 임시·cache 경로를 선택한다.

### P3: 별도 코드 결함 발견 없음

과거 실제 report 원본 미제공은 아래의 검증 범위 제한이며 코드 결함으로 세지 않았다. Ruff format 파일 수의 차이도 통과 결과를 바꾸는 결함으로 판단하지 않았다.

## 실제 관측한 검사 결과

실행기는 모두 `C:\Users\USER\JourneyMap-Actual\.venv\Scripts\python.exe`다. Python 3.12.10, pytest 8.4.2, Ruff 0.17.1, mypy 1.20.2를 관측했다. import 경로도 대상 저장소의 `src\journeymap`으로 확인했다.

| 검사 | 이번 검토의 관측 결과 | 구현 보고서의 수치와 구분 |
| --- | --- | --- |
| 전체 pytest | **1 failed, 1377 passed in 695.96s**, exit 1 | 보고서의 1,378 passed / 229.31초를 이번 전체 실행에서 재현하지 못함 |
| Ruff lint | `All checks passed!` | 직접 재실행 |
| Ruff format | `226 files already formatted` | 보고서의 225와 이번 도구 출력은 다름; 성공 여부는 동일 |
| strict mypy | `Success: no issues found in 170 source files` | `--strict`를 명시해 직접 재실행 |
| `git diff --check` | 성공, 출력 없음 | 작업 트리 및 기준 커밋→대상 HEAD 두 범위 모두 검사 |
| 별도 추가 검사 | `30 passed in 24.51s` | 기존 1,378개에 합산하지 않음 |
| 오프라인 preflight | 12회 / tick 24 / 171 events / pending 3 | 보고서 숫자를 직접 재관측; 실제 모델 결과 아님 |

전체 실행에서 `test_life_output_budget.py` 22개, `test_life_responses.py` 24개, `test_paid_life.py` 48개, `test_paid_audit.py` 28개, `test_paid_audit_response.py` 7개는 모두 통과했다(합계 129 = 기존 107 + 신규 22). F1 단독 재검사는 전체 1378개 통과의 대체물이 아니다.

preflight의 모든 wire 출력 한도는 8,192였고, replay 결과는 `provider_recalled=false`, `live_inputs_equal=true`, `recorded_responses_checked=12`였다. socket/DNS와 `OPENAI_API_KEY` 조회를 막은 추가 검사 안에서 실행했다.

주요 실행 명령:

```powershell
Set-Location C:\Users\USER\JourneyMap-Actual
& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\python.exe -m ruff check --no-cache .
& .\.venv\Scripts\python.exe -m ruff format --check --no-cache .
& .\.venv\Scripts\python.exe -m mypy --strict --cache-dir C:\Users\USER\Documents\JourneyMap\review-temp\mypy-cache
git diff --check
git diff --check b7717c2112c076b6f194d26ca0507d13b5b8a4d1 739f08d34325106cf2eaaacc3fadcdecd0858ddd
& .\.venv\Scripts\python.exe -B -m pytest C:\Users\USER\Documents\JourneyMap\review-temp\test_phase44_independent.py -q -s -o cache_dir=C:\Users\USER\Documents\JourneyMap\review-temp\pytest-cache --basetemp=C:\Users\USER\Documents\JourneyMap\review-temp\probe-data-final
```

`--no-cache` 및 별도 cache 경로는 검토 산출물을 분리하기 위한 옵션이며 검사 규칙을 완화하지 않는다. 추가 검사의 `--basetemp`는 재실행 시 새로운 경로를 사용한다.

## 중점 항목별 소스·실행 근거

### 1. 계획 값·모델·프롬프트·세계 정책

`life_responses.py:26`의 `LifeExecutionPlan`을 기준 커밋의 실제 Git blob에서 AST로 추출한 기본값과 비교했다. 차이는 정확히 `max_output_tokens: 4096 → 8192` 하나였다. 생성자는 기본값과 값·타입이 다른 계획을 거부한다(`life_responses.py:49`). 모델 `gpt-5.4-mini-2026-03-17`, high/default, Ada, seed 42, horizon 24는 그대로다.

`git diff --name-only BASE HEAD -- src`의 변경은 `life_llm.py`, `life_responses.py`, `life_admission.py`, `life_http.py`, `paid_replay.py` 다섯 개뿐이다. `PROMPT`, `life_response_format`, 세계·정책·handler·Core·기존 연구 adapter는 변경되지 않았다. protocol/preflight 문자열의 v2 변경은 계획 값 외의 기록 계약 변경이며 숨겨진 모델 설정 변경이 아니다.

### 2. 연구용 4,096 상한과 생활 전용 확장

기존 `parameters_v1`은 여전히 정수 16–4096만 받는다(`adapters/llm.py:98`, `:104`). 기존 `request_body`는 이를 그대로 호출한다(`openai_provider.py:19`, `:32`). 생활 `life_body`만 별도 허용 키·high·내장 int 16–8192를 검증한다(`life_llm.py:110`). 모의 기본 설정은 1024다(`life_llm.py:195`).

별도 검사에서 16/1024/4095/4096의 생활 body와 기존 연구 builder의 body가 같고, 4097/8191/8192는 생활만 허용하며 연구 경로는 거부함을 확인했다. 0/15/8193/16384/bool/float/string/null도 거부했다. 기존 신규 테스트는 reasoning 변경·추가 tools·reasoning 누락도 거부한다(`tests/test_life_output_budget.py:64`, `:80`).

### 3. config → preflight → HTTPS 검증 → replay

생활 config 상한은 `life_llm.py:227`, preflight 계획 일치는 `life_responses.py:62`, paid request 생성은 `life_http.py:97`, paid wire 재검사는 `life_http.py:193`에 있다. 정상 paid session과 decider가 모두 plan의 출력·timeout 값을 사용한다(`paid_life.py:29`, `:133`). replay의 요청/body 재구성과 실패 분류도 현재 plan을 사용한다(`paid_replay.py:89`, `:110`).

4096/16384 요청은 실제 연결·키 조회 전 거부한다(`tests/test_life_output_budget.py:95`). 6000/8192 정상 합성 응답의 실제 FakeHTTPS body, 행동 완료, 회계와 replay를 기존 신규 테스트가 검사한다(`tests/test_life_output_budget.py:170`). preflight 전체 흐름도 직접 재검증했다.

### 4. 누적 상한·timeout·재시도

`life_responses.py:29`의 고정 계획과 `:123`의 reserve를 대조했다. 입력 500000, 출력 131072, 최대 32회, USD 1.00, 요청당 입력 예약 400000, timeout 120초가 유지된다. `LifeHTTPProvider.generate`는 연결 한 번·POST 한 번이며 재시도 loop가 없다. 응답 수신 loop는 읽기 loop다. owner timeout 뒤 세계를 진행하거나 새 요청을 시작하는 경로도 추가되지 않았다(`life_decisions.py:118`, `:211`, `paid_life.py:61`).

### 5. 정확한 경계·초과 usage·INCOMPLETE·late 회계

예약은 `400000 × 750 + 8192 × 4500 = 336864000 nano-USD`다. 추가 검사에서 기존 입력 100000 / 출력 122880 / 비용 663136000 / attempts 31일 때 각각 다음 예약을 허용하고, 각 값에 1을 더하면 해당 제한으로 거부함을 확인했다. 최대 출력 16회 뒤 출력 131072 소진도 기존 신규 테스트에서 확인한다(`test_life_output_budget.py:137`).

usage의 bool·음수·부분집합·합 검사는 `life_responses.py:83`, 예약 초과 정지는 `:148`이다. 비용은 `(input-cached)×750 + cached×75 + output×4500`이며 reasoning을 별도로 더하지 않는다. paid path는 model/tier 확인 후 가격을 정산한다(`life_http.py:309`).

추가 late 검사 3개에서 timeout을 먼저 확정하고 가짜 응답을 늦게 회수했다. 출력 8192의 completed/incomplete는 각각 37479000 nano-USD로 정산되고 예약은 해제됐으며, 세계 tick 0과 행동 없음이 유지됐다. 출력 8193은 비용 확정 없이 예약 336864000을 유지했다. 각 저장 보고서의 data-only replay가 성공했고 실제 전송 모의 횟수는 1회였다. 기존 정상/INCOMPLETE/초과 usage 테스트 및 timeout 정각·전후 테스트도 대조했다(`test_paid_life.py:319`, `:351`, `:451`, `:472`).

### 6. 과거 승인 binding 거부

`ExecutionApproval.validate`는 전체 bound 일치를 요구한다(`life_admission.py:69`). `RunJournal.__init__`는 승인·현재 binding 확인 후에만 디렉터리/DB를 만든다(`:130`). 각 전송 전 `assert_current`도 다시 확인한다(`:156`, `life_http.py:190`). 과거 source/plan/protocol digest를 하나씩 넣는 테스트는 registry 파일·credential·connection이 생기기 전에 거부함을 검사한다(`test_life_output_budget.py:126`).

registry 경로 `artifacts/phase4_3/runs.sqlite3`, run/approval ID UNIQUE 소비 정책은 유지된다. 기기 간 누락된 registry를 자동 복원하거나 이미 사용한 ID를 전역적으로 찾아주는 기능은 아니다. 과거 사용한 `86212ada-c33b-4813-94a9-2fdf4a32774d`를 재사용할 근거로 해석하면 안 된다.

### 7. 과거 기록 정책과 identity

`paid_replay.py:38`은 현재 source/protocol/plan 세 identity를 요구하고, 불일치를 엔진 재생 전에 거부한다. 기존 기록의 자동 변환·재봉인 경로는 없다. 추가 검사에서 기준 커밋의 모든 패키지 Python blob으로 source hash를 독립 계산했으며 문서의 과거 identity와 일치했다.

| identity | 직접 계산한 값 |
| --- | --- |
| 기준 source | `b0cd3e00bc45b27bc7e8ffe63bc3481e4ff23d8eb8c61e48ea46fb02f66d5a06` |
| 현재 source | `f29e9631f80b19d832e83ce7969318bae061a88b543424ed7254499aca91f973` |
| 현재 plan | `b262f9f8c03d06a6b59acc08732d57965295b1141a2af4ef0fd13414552cbc91` |
| 현재 protocol | `9dfd86a62f3c45570b86ad016b7a14a5fbbba71257cf8b76efe99b88b8eef970` |

합성 보고서에 과거 binding을 넣으면 mismatch로 거부되는 것은 확인했다. **과거 실제 실행 원본 report는 현재 검토 폴더에 제공되거나 발견되지 않아, 해당 원본의 SHA-256 및 동결 소스에서의 실제 5회 기록 replay는 직접 재검증하지 못했다.** 구현 보고서의 `calls_checked=5`와 과거 report hash는 보고된 주장으로만 남긴다. 실제 원본을 임의로 재구성하지 않았다. 노트북 전체의 비공개 경로까지 탐색해 원본 부재를 증명한 것은 아니다.

### 8. 테스트의 반례 검출 능력

신규 22개는 단순 기본값 확인 외에도 잘못된 타입·상한·allowlist·옛 binding·전송 전 거부·회계·replay를 검사한다. 기존 테스트의 변경은 출력 및 예약에 따른 기대값 전환으로, 기존 assertion 삭제나 skip 추가는 없었다.

기존 `test_paid_audit.py:184`, `:196`은 calls/journal/usage 삭제와 재봉인한 누락·중복·여분·completion 삭제를, `test_paid_audit_response.py:28`, `:64`는 raw 보관 누락과 READY/ERROR 상태 우회를 검사한다. data-only CLI도 Provider·키·socket·DNS 차단 상태에서 실행한다.

추가 검사에서는 새 8192 정상 보고서를 만든 뒤 다음을 변조하고 hash chain·snapshot을 다시 봉인했다. 모두 ValueError로 거부됐다.

- reasoning 8191개를 별도로 더해 중간·최종 비용을 동시에 부풀림.
- PREPARED 예약을 옛 4096 / 318432000으로 바꿈.
- 성공 회수 응답의 call.raw만 삭제함.
- raw/envelope/call.raw를 함께 맞춘 뒤 usage를 float로 바꿈: `INVALID_USAGE`.
- raw/envelope/call.raw/USAGE_CHECKED를 함께 맞춘 뒤 출력 8193으로 바꿈: `paid audit reservation exceeded`.

별도 검사는 실제 기준 커밋 AST 및 blob hash 비교, wire 경계 16개, 예약 경계 4개, 큰 late usage 3개, 변조 5개, guarded preflight 1개를 포함해 총 30개다. 구현과 같은 상수를 가져다 기대값으로만 사용하는 검사에 의존하지 않았다. 감사는 내부 일관성 검증이며, 외부 서명이나 악의적인 전체 기록 재작성의 진본 인증은 기존 계약 밖이다.

## 확인하지 못한 범위

- 과거 실제 report 원본 및 그 원본의 동결 소스 replay. 새 소스의 거부 정책과 합성 반례 검증을 이 실제 기록 검증의 대체물로 취급하지 않는다.
- 실제 서버의 schema 수락, 모델의 8192 사용 양상, INCOMPLETE/timeout 발생 빈도, 실제 청구서·계정의 가격과 한도, 24시간 행동 완주. 모두 이번 오프라인 검토 밖이다.
- 물리 전원 장애·실제 네트워크 단절·노트북 간 registry 보존의 운영 검증. FakeHTTPS와 SQLite 오류 주입은 그 실증을 대신하지 않는다.

실행 관련 후속 작업에는 최신 source/plan/protocol, 새 run ID 및 별도의 명시적 사용자 승인이 필요하다.


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
