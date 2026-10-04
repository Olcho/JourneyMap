# Experiment 03 — 코드 기준선과 수령처 안내 진단

2026-10-04 구현 계약. Distinct Places v2와 별도로 코드 기준선, 과거 경험 의존 진단,
Full prefix 정보충분 대조조건을 오프라인으로 검증한다. 성공은 fixture와 기록 경계의
correctness이며 실제 LLM 효과, 장기기억 일반, 학습, 내부 추론 또는 특정 k의 우위를
입증하지 않는다. 실행 증거는 [완료 기록](EXPERIMENT03_OFFLINE_REPORT_2026-10-04.md)에 있다.

현행 Notion의 Experiment 03/Research Objective/Working Conventions/Handoff/Preflight를
읽고 첨부 구현 지시문과 충돌하지 않음을 확인했다. 수령처 시나리오의 구체적인 세부값은
첨부 지시문에서 정한 계약이며 Notion에 동일한 세부 명세가 있었다는 주장은 아니다.

## 1. 기존 방문 과제의 코드 기준선

`adapters.horizon_baseline.PreviousPlaceProvider`는 인스턴스 상태가 없는
`fake / memory-horizon-previous-place-fixture-1`이다. 현재 semantic Observation의
장소/local exits와 직전 **1개 closed Event Trace의 행동 전 장소**만 사용한다.

| 현재 장소 | 직전 행동 전 장소 | 선택 |
| --- | --- | --- |
| Inn / Bakery / Well | 무관 | Square MOVE |
| Square | 경험 없음 | Inn MOVE |
| Square | Inn | Bakery MOVE |
| Square | Bakery | Well MOVE |
| Square | Well / Square | unit WAIT |

목적지에 맞는 route ID는 현재 local exits에서 선택한다. cursor, 방문 집합,
phase/counter, clock, tick, provenance ID, condition/k 또는 연구자 진행도를 읽지 않는다.
전제나 필요한 유일 route가 없으면 raw response metadata의
`incomplete_details.reason=BASELINE_INPUT_PRECONDITION`으로 명시한다. 기존 Controller가
실패를 보존하고 제출하지 않으며 기존 failure bound와 audit 의미는 그대로다.

기존 `run_trial`, v2 profile, Recency k=1, export schema 3와 offline audit를 재사용한다.
CLI의 `--fixture previous-place`는 `--memory recency-k1`을 요구한다. 기존 stateful
`HorizonFakeProvider`는 그대로 남는다. 이 기준선은 No Event Memory의 무기억 성공도,
다섯 번째 LLM 기억 조건도 아니다.

정상 경로는 6 MOVE로 tick 12에 첫 완료, 이후 unit WAIT 12회로 tick 24에 종료한다.
18 decisions/submissions, coverage 3, repeat 0, strict success true다.
전체 decision 수 18을 완료 효율 점수로 사용하지 않는다.

## 2. 버전과 상태 소유권

| 대상 | identity |
| --- | --- |
| scenario | `alderwick / pickup-cue-diagnostic-1` |
| prompt | `alderwick-pickup-cue-decision-1` |
| offline protocol | `alderwick-pickup-cue-offline-1` |
| audit | `pickup-cue-offline-correctness-1` |
| export / reader | `pickup-cue-offline-record-1` |
| model configuration | `pickup-cue-offline-configuration-1` |
| recording fixture | `pickup-cue-recording-fixture-1` |
| Full prefix | `Pickup diagnostic Full prefix / pickup-cue-full-prefix-1` |
| matrix / audit | `pickup-cue-matrix-1 / pickup-cue-matrix-audit-1` |

새 진단의 초기 상태에 scenario-owned `pickup_cue: {pickup_location: inn|bakery}`를 둔다.
실행 중 이 값은 변하지 않는다. 기존 Alderwick 지도, route ID, 이동 비용 2를 재사용하고
stranger는 Square에서 시작한다. MOVE/WAIT와 movement/knowledge만 조립하며 ScheduledEvent,
자율 NPC, survival/social/trade/inventory 기능은 활성화하지 않는다.

trusted perception이 Well에 있는 actor에게만 안내 값을 투영한다. contributor에는 허용된
`PerceptionContext`만 제공하며 전체 world를 주지 않는다. section/module/contributor identity는
A/B에서 같다. payload는 `{"pickup_notice":{"pickup_location":"inn"}}` 또는 bakery이며,
Well 밖에서는 `{"pickup_notice":null}`이고 숨은 보조 필드가 없다.

빈 Knowledge는 이 진단의 정보 통제 조건이다. No Event Memory가 프로젝트 전체의
Agent Knowledge를 제거한다는 뜻은 아니다. 안내를 Knowledge, 마지막 receipt 또는 방문
요약에 복사하지 않는다. 실제 물품이나 수령 action은 없다.

## 3. 준비 경험과 기억 조건

trusted orchestration이 다음 prefix를 실제 GamePort에서 수행한다. 각 행동 전에 얻은
Observation과 실제 submitted ActionRequest, actor-visible receipt를 정식
`EventTraceArchive.begin/close`로 연결한다. 준비 중 Provider 호출은 0회다.
기존 `LLMController.close_event_trace`나 private archive는 변경하지 않는다.

| d | Well 방문 전 WAIT | Square→Well→Square | 귀환 후 WAIT | cue trace sequence | 평가 tick |
| --- | --- | --- | --- | --- | --- |
| 1 | 3 | 2 MOVE | 0 | 5 | 7 |
| 2 | 2 | 2 MOVE | 1 | 4 | 7 |
| 3 | 1 | 2 MOVE | 2 | 3 | 7 |
| 4 | 0 | 2 MOVE | 3 | 2 | 7 |

모든 정상 prefix는 성공 제출 5회, MOVE 2회, unit WAIT 3회다. 안내는 Well에서 출발하는
MOVE의 행동 전 Observation에 한 번만 있다. 평가는 모두 Square/tick 7이며 current
semantic input은 같다. OBSERVE는 read이므로 그 자체가 경험을 만들지 않는다.
준비 실패 시 나머지 prefix와 평가를 중단하고 실제로 남은 원본만 보존한다.

| d | No Event Memory | k1 | k2 | k3 | Full prefix |
| --- | --- | --- | --- | --- | --- |
| 1 | 없음 | 있음 | 있음 | 있음 | 있음 |
| 2 | 없음 | 없음 | 있음 | 있음 | 있음 |
| 3 | 없음 | 없음 | 없음 | 있음 | 있음 |
| 4 | 없음 | 없음 | 없음 | 없음 | 있음 |

이는 **입력에서 안내를 볼 수 있는지**의 표이며 LLM 성공률 예측이 아니다. 선택 count는
0/1/2/3/5이며 가장 오래된 것부터 원본 순서대로 전달한다. Full prefix는 정확히 5개 준비
경험을 전달하는 이 진단 전용 policy다. 기존 Recency k=3, 방문 실험의 네 조건 또는 live
12-allocation 계획으로 재분류하지 않는다.

## 4. 입력·프롬프트·응답 경계

`diagnostic_input`은 빈 Knowledge, contributor 목록, 허용된 semantic/local route 필드와
Well 전용 notice 경계를 독립 검사한 뒤 기존 semantic projection의 detached JSON 투영을
재사용한다. 허용하지 않은 contributor/필드는 거부한다.

모델 입력은 현재 semantic Observation, 선택된 과거 semantic Observation,
action type/payload, public receipt status/reason으로만 구성한다. raw timestamp,
sequence, provenance ID/digest, d, condition/k, target label, trial 순서, source/runtime은
researcher-only다. stable location/route/contributor ID와 이동 비용은 의미 있는 관찰이므로
유지한다. 평가 뒤 새로 닫힌 경험은 그 평가 입력에 포함되지 않는다.

`adapters/pickup_cue_prompt.py`의 instructions는 첨부 지시문 영문을 그대로 보존한다.
LF 줄바꿈과 마지막 LF 1개, UTF-8 **1027 bytes**, SHA-256은
`a250e897c928f06d25939700c6f65e4895a3729fd38a355186208ebd5e88f1b4`다.
rendering은 instructions + `\nINPUT_JSON\n` + canonical JSON이다. ProviderRequest의
model은 `pickup-cue-fixture-1`, parameters는 모든 case에서 `{}`다.

A/B cue가 범위 밖이면 **전체 rendered prompt와 ProviderRequest 설정**이 같다.
범위 안이면 과거 notice의 pickup_location 하나만 다르다. 모든 조건에서 event_memory를
제외한 current input은 같다.

평가 응답 시도는 최대 1회, Engine 제출도 최대 1회다. 기존 strict JSON/parser/binding과
MOVE/unit-WAIT validator를 재사용한다. duplicate keys, nonfinite/invalid UTF-8, authority
field, 다른 action, bool/2 duration은 제출 전에 거부한다. 없는 route는 Engine에 제출해
실제 REJECTED를 기록한다. 합법적인 다른 목적지 MOVE도 그대로 실행한다. repair, retry,
fallback, conversation history, previous_response_id는 없다.

## 5. 오프라인 fixture와 lifecycle

runner는 정확한 concrete `Fixture` 데이터만 받는다. 임의 Provider/transport/factory와
caller label을 fixture로 바꾼 live 구현, subclass는 호출 전에 거부한다. fresh kernel,
application, archive, local decision/binding 상태와 Provider를 case마다 만든다.
이는 trusted Python composition 경계이며 악의적인 monkeypatch를 막는 sandbox는 아니다.

`notice` fixture는 실제 rendered input의 과거 notice를 읽고 현재 local route로 MOVE한다.
cue가 없으면 WAIT한다. 이 WAIT 규칙은 fixture 구현이며 prompt의 필수 행동이나 미래 모델
예측이 아니다. fixed/refusal/incomplete/provider-error/transport-error fixture는 실패 검증용이다.
CLI는 구체적인 fixture 종류만 선택하며 API key 탐색, live 토글, network transport가 없다.

40-case matrix는 2 target × 4 d × 5 condition의 계약 검증 묶음이다. 모든 조합이 한 번씩
포함되는지, record seal과 case binding이 맞는지 검사한다. 누락·중복·다른 case 연결은
거부한다. 40은 통계 표본 수나 유료 호출 승인 수가 아니다.

## 6. 평가와 실패 기록

기존 Distinct Places의 8지표와 `primary_success`는 수정하지 않는다. 진단은 다음을 분리한다.

- input: retrieved 원본 IDs/count, cue 포함 여부, current/input canonical 문자열·hash,
  전체 prompt bytes/hash와 ProviderRequest.
- choice: raw response/metadata, candidate, parser outcome, 제출 요청, route/destination,
  `choice_withheld`(WAIT).
- execution: NOT_SUBMITTED / SUCCEEDED / REJECTED / FAILED / UNKNOWN.
- goal: 평가 MOVE의 성공 Event와 실제 최종 위치가 모두 target일 때만 `task_success=true`.
- failure: REFUSAL / INCOMPLETE / INVALID_OUTPUT / PROVIDER_ERROR / TRANSPORT_ERROR /
  OBSERVATION_ERROR / ENGINE_ERROR / RECORDING_ERROR 및 별도의 preparation failure.
- integrity: engine replay, input reconstruction, evaluation recomputation, INCLUDED/EXCLUDED.

다른 목적지 SUCCEEDED, WAIT, 검증 가능한 refusal/parser failure/REJECTED/FAILED는 false다.
실행이나 원본이 확인 불가하면 null과 integrity 상태를 쓴다. audit가 손상을 발견하면 저장된
success flag와 관계없이 audit.task_success는 null이다. 확인 가능한 Engine 결과와 기록
무결성은 별개다. 실패 주입으로 scenario 규칙과 다른 Engine 결과를 만들면 원본의
FAILED/false는 보존하지만 정상 versioned scenario replay에는 포함되지 않는다.

응답을 받은 refusal/incomplete/parser failure/WAIT/오답도 behavioral denominator에 남는다.
응답 전 infrastructure와 integrity exclusion은 별도로 집계하고 원 case를 삭제·교체하지
않는다. matrix audit는 integrity 제외 수와 포함된 응답 분모/응답 전 infrastructure 수를
따로 반환한다. 무기억 WAIT나 추측 실패를 비합리적 판단으로 해석하지 않는다.
통계 분석과 자동 replacement는 없다.

## 7. 새 export와 독립 audit

새 output directory의 `case.json`은 immutable `Record` snapshot이며 기존 schema 3와
다른 identity를 가진다. `matrix.json`은 40개 조합과 seal을 연결한다. 기존 directory/파일은
덮어쓰지 않는다. 쓰기 실패는 RECORDING_ERROR로 종료하며 durable journal, 중단 복구 또는
automatic resume를 제공하지 않는다.

case는 source/branch/HEAD/source digest/runtime, 버전, researcher-only 설정/초기 상태,
준비·평가 원본 Observation/Request/receipt/EventTrace, action traces와 Engine results/events,
평가 직전 state/digest/tick, exact input/ProviderRequest, raw response/실패, 최종 Engine
report/state/digest/time, 평가 및 integrity를 보존한다. raw invalid UTF-8 응답도 JSON escape로
보존하고 reader를 decision parser와 구분한다. exception message/credential/환경변수를 수집하지 않는다.

PR CI와 같은 detached HEAD 실행은 `source.branch = null`로 기록한다. branch 필드는
필수이며 값은 null 또는 비어 있지 않은 이름이다. HEAD commit과 working source digest는
계속 필수다. 브랜치 이름이 없다는 이유로 정상 실행을 제외하지 않는다.

audit는 Provider/Controller를 생성하거나 호출하지 않고 다음을 수행한다.

1. versioned 초기 world, seed, 저장된 ActionRequest stream을 ReplayHarness로 재생한다.
2. 새 application에서 prefix를 실제 수행하고 관찰을 다시 얻어 archive/선택/projection/
   전체 prompt와 설정을 재구성한다. 저장된 Observation JSON은 비교 대상일 뿐이다.
3. 응답을 다시 parse/bind하고 실제 Engine 결과, 성공 이동 Event와 최종 위치로 평가를 재계산한다.

success flag, prompt/hash, 선택 trace/count, cue/Observation digest, receipt, checkpoint,
distance, response와 model 설정을 바꾸고 재봉인해도 모순을 거부한다. 모든 원본과 외부 기준을
동시에 일관되게 재작성한 악의적 위조를 증명할 수 있다는 주장은 아니다. 일관된 다른 정식
case는 유효하다. 외부 장애의 완전한 재구성이 안 되면 EXCLUDED로 남는다.

## 8. 정확한 오프라인 실행

정상 Python 3.12 개발 환경에서 저장소 root를 작업 디렉터리로 사용한다.

```powershell
python -m journeymap.examples.memory_horizon --fixture previous-place --memory recency-k1 --trial-id experiment03-baseline --output trials/experiment03-baseline
python -m journeymap.examples.memory_horizon --audit trials/experiment03-baseline
python -m journeymap.examples.memory_horizon --replay trials/experiment03-baseline
python -m journeymap.examples.pickup_cue --matrix --output trials/experiment03-pickup-matrix
python -m journeymap.examples.pickup_cue --audit-matrix trials/experiment03-pickup-matrix
python -m journeymap.examples.pickup_cue --case-id pickup-example --target inn --distance 2 --memory recency-k2 --fixture notice --output trials/pickup-example
python -m journeymap.examples.pickup_cue --audit trials/pickup-example
python -m journeymap.examples.pickup_cue --replay trials/pickup-example
```

재실행 때는 **새 output 경로**를 지정한다. audit/replay는 원본 경로를 읽기만 한다.
이번 Windows checkout의 기존 `.venv` launcher는 다른 설치 경로를 참조하므로 수정하지 않고
번들 Python 3.12.14와 기존 개발 패키지를 사용했다. 같은 환경의 명령은 다음과 같다.

```powershell
$journeyPython = 'C:\Users\user\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$env:PYTHONPATH = "$PWD\src;$PWD\.venv\Lib\site-packages"
& $journeyPython -m journeymap.examples.pickup_cue --matrix --output trials/my-new-pickup-matrix
& $journeyPython -m pytest -o cache_dir=trials/my-new-cache --basetemp trials/my-new-test-temp
& $journeyPython -m ruff check .
& $journeyPython -m ruff format --check .
& $journeyPython -m mypy
git diff --check
```

테스트 cache/temp도 historical raw 밖의 새 경로를 사용한다. 테스트는 network socket을 차단한다.

## 9. 후속 범위

실제 LLM pilot 전에 모델/provider·wire schema·drift 허용, reasoning/token/timeout, 반복 수,
randomization, 종료·제외·replacement 기준과 분석 계획을 별도 동결해야 한다.
live 연결에는 별도 검토와 사용자 승인이 필요하다. 실제 live transport, Experiment 04,
Selective Memory, vector DB, reflection, 학습 또는 UI는 이번 변경에 없다.
