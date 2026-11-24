# Experiment 03 수령처 진단 — 실제 LLM 연결 준비 계약

2026-10-05 v2 mode binding 수정. 이번 범위는 코드와 오프라인 검증입니다. 실제 key를 읽거나 외부 LLM을 호출하지
않았습니다. 기존 [오프라인 진단](EXPERIMENT03_BASELINE_DIAGNOSTIC.md), Memory Horizon mock-only
preflight, M8 historical schema는 유지합니다. 결과는 [완료 보고](EXPERIMENT03_LIVE_PREPARATION_REPORT_2026-10-05.md)에 기록합니다.

F1 수정과 새 gate는 [mode 수정 완료 보고](EXPERIMENT03_LIVE_MODE_FIX_REPORT_2026-10-05.md)에
기록합니다. 기존 v1 완료 보고와 실행 자료의 당시 결과는 그대로 보존합니다.

## 실험 의미와 권한

inn/bakery × d=1/2/3/4 × `no-event-memory`, `recency-k1`, `recency-k2`, `recency-k3`,
`full-prefix`의 40개 조합을 유지합니다. 준비 행동 5회는 실제 Engine에서 scripted로 수행하고
Provider를 호출하지 않습니다. tick 7에서 마지막 평가를 한 번 호출하며 제출도 최대 한 번입니다.
기존 semantic projection, raw 경험 선택, 고정 instructions와 실제 도착 성공 판정을 재사용합니다.
Knowledge는 비어 있습니다. 실제 준비 중 LLM이 안내를 읽고 기억하는 실험이 아니라,
**과거 관찰을 마지막 호출 입력에 전달하는 교정 과제**입니다.

allocation마다 새로운 Kernel/Application/GamePort/archive/Session/Provider/transport를 소유합니다.
Session은 trusted 실험 orchestration이고, Provider에는 request/config와 byte transport만 제공합니다.
응답에서 행동을 검증하고 권한 envelope를 생성하는 기존 순수 함수에 현재 Observation만 전달합니다.
Provider/모델에는 target, d, condition, 연구 ID, hidden clock, checkpoint, raw trace ID가 전달되지
않습니다. Well에서 지각한 과거 안내 내용은 의도된 정보입니다. 모델의 내부 추론은 수집하지 않습니다.

## 식별자

| 계약 | identity |
| --- | --- |
| 계획 | `pickup-cue-execution-plan-2` |
| 실행 / export / audit | `pickup-cue-live-execution-2` / `pickup-cue-live-export-2` / `pickup-cue-live-audit-2` |
| configuration / wire | `pickup-cue-responses-1` / `pickup-cue-move-unit-wait-1` |
| HTTP adapter / mock | `pickup-cue-responses-http-1` / `pickup-cue-http-recording-mock-1` |
| 기존 prompt | `alderwick-pickup-cue-decision-1` |
| 재사용 journal envelope | `memory-horizon-attempt-journal-1` |

mode는 동결 계획의 필수 필드로 `recording-mock` 또는 `live`이며 plan digest에 포함합니다.
runner 선택, journal PLAN, export mode가 모두 계획과 같아야 합니다. 응답 유무와 무관하게 검사합니다.
실제 모델용 config로도 `recording-mock` 계획을 만들 수 있으며 모델 이름으로 mode를 추정하지 않습니다.
raw response가 있으면 provider/adapter identity도 일치해야 합니다.
mock artifact를 live로 재분류하거나 기존 offline fixture를 live 증거로 인증하지 않습니다.
HTTP double로 live 코드 분기를 테스트한 결과도 실제 서비스 증거가 아닙니다.

### v1의 읽기 전용 범위

v1 계획에는 mode가 없어 원래 digest를 외부에서 고정해도 mode 재분류를 막을 수 없습니다.
v2 runner는 v1 계획을 실행하지 않습니다. v2 audit/inspect는 v1 원본의 plan seal과 journal prefix를
읽고 `EXCLUDED / LEGACY_PLAN_MODE_UNBOUND`, `legacy_read_only=true`,
`execution_mode_verified=false`, `mode=null`을 반환합니다. journal의 선언값은
`reported_journal_mode`에만 남깁니다. 호출 prefix 수와 allocation 목록은 보고하되 v2 행동 분모에는
넣지 않고 새 Engine/input 인증도 수행하지 않습니다. v1 JSON/plan seal 손상은 일반 무결성 거부로
처리하고, journal 상태는 읽을 수 있는 검증된 prefix 범위만 보고합니다. v1 mode는 어떤 경우에도 인증하지 않습니다.
이는 과거 정상 recording-mock 원본이 변조됐다는 판정이 아니라 v2의 mode 보증에 필요한 provenance가
없다는 뜻입니다. v1 당시 INCLUDED 결과는 원본 gate/이전 ZIP에 유지하며 v1을 v2로 재봉인하지 않습니다.

## 요청과 응답

전체 요청은 `model`, 기존 고정 prompt를 담은 `input`, `store=false`, strict `text.format`,
`max_output_tokens`, 선택적인 `reasoning.effort`만 허용합니다. instructions를 별도로 덧붙여
기존 프롬프트를 바꾸지 않습니다. metadata, tools, browsing, conversation, previous_response_id는
허용하지 않습니다. root object에 MOVE/WAIT와 payload union을 두고 모든 object의 additionalProperties를
false로 합니다. MOVE/route_id, WAIT/integer 1의 정확한 조합은 기존 local validator가 다시 검사합니다.
일반 DecisionCandidate schema는 바꾸지 않습니다. 전용 schema는 동일한 MOVE/unit-WAIT 구조의
기존 순수 schema builder를 재사용하고 독립 wire identity와 response format name으로 묶습니다.

body 객체, canonical UTF-8 문자열, byte 수와 SHA-256을 intent/export에 기록합니다. 기록한
body를 전송하고, transport byte 경계에서 받은 값과 저장값의 일치를 통합 테스트로 검증합니다.
audit는 저장 body를 예상값으로 복사하지 않고 새 세계의 실제 관찰에서 다시 생성합니다.
HTTP는 표준 라이브러리의 고정 `api.openai.com:443 /v1/responses` 한 번만 사용합니다.
redirect, proxy 환경변수, retry, fallback, 모델 자동 교체는 없습니다. timeout은 HTTP socket
작업 timeout이고 전체 실행의 비용/벽시계 보장은 아닙니다. 최대 응답 byte 수를 넘으면 중단합니다.

응답은 model ID, response ID, status, refusal, incomplete_details, usage와 출력 text를 보존합니다.
returned model은 계획의 **명시적인 exact allowlist**에 있어야 합니다. alias/snapshot 자동 허용은
없습니다. 인증 헤더, key, 환경변수 전체, 원문 exception, 오류 HTTP body/응답 헤더는 기록하지
않습니다. decoded response 안의 key echo도 차단합니다. parsing은 Engine 제출보다 먼저 수행합니다.

공식 문서는 2026-10-05에 확인했습니다.

- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs): root object 제약,
  required/additionalProperties 및 strict schema를 확인했습니다.
- [Responses 전환 가이드](https://developers.openai.com/api/docs/guides/migrate-to-responses):
  `text.format`과 명시적인 `store=false`를 확인했습니다.
- [Reasoning 가이드](https://developers.openai.com/api/docs/guides/reasoning):
  출력 한도에 reasoning token도 포함되며 incomplete가 생길 수 있음을 확인했습니다.

이는 최종 모델의 모든 parameter 지원이나 실제 서비스 schema 수용을 확정하지 않습니다.
최종 모델이 정해지면 해당 모델 문서를 검토해 `model_support_review`에 공식 URL을 동결해야 합니다.
현재 validator는 제한된 문법만 검사하며 모델별 지원을 추측하지 않습니다.

## 계획과 호출 gate

기본 `plan`은 model/returned_models/output 한도/timeout/response 크기/지원 검토/예산 검토를
null로 두는 DRAFT입니다. reasoning null은 DRAFT에서는 미확정, READY에서는 parameter 생략을
명시적으로 선택한 값입니다. READY 생성에는 별도 config 파일의 모든 필수 값을 요구합니다.
`--mock-config`는 가상의 `pickup-recording-mock`만 지정하며 live 계획/실행/audit에서 거부됩니다.
`plan --mode`는 실행 의도를 명시하며 기본값은 `recording-mock`입니다. `--mode live`만으로 실행이
승인되지는 않습니다. live READY 계획은 mock 전용 model/returned model/예산 sentinel과 미검토 support
설정을 허용하지 않습니다. 이 검사는 runner와 audit가 공유하는 순수 `validate_mode_config`에 있습니다.

source에는 commit, working source digest와 **필수 branch 필드**를 기록합니다. detached HEAD는
명시적인 null이고 빈 문자열/필드 생략은 허용하지 않습니다. source/runtime/config/protocol 및
정렬된 allocation 목록을 계획 digest로 묶습니다. Engine seed와 allocation seed는 별도입니다.
`random.Random(allocation_seed).shuffle`로 40개 조합 순서를 한 번 만들며 목록을 저장하고 audit에서
재계산합니다. 기본 42/0은 검토용 초안값이며 실험의 승인된 seed로 자동 확정하지 않습니다.
`allocation_count` 1..40으로 사전 순서의 prefix만 선택할 수 있어 별도 1-call smoke 계획도 만들 수
있습니다. 임의 반복/성공 시까지 재시도는 없습니다. count ≤ call_cap ≤ 40을 요구합니다.

실행은 계획 mode와 runner 선택 일치, 현재 source/runtime 일치, READY/digest/allocation/call cap과 새 출력 경로를 모두 검사한
후에만 파일을 만들고 이후 durable intent 뒤에 transport를 만듭니다. key 조회는 그 뒤 send에서만
합니다. live는 `--enable-live`와 `--approved-plan-sha256`도 요구합니다. 이 옵션은 **사람의 명시적
실행 승인을 대체하지 않습니다**. `budget_review`는 승인 예산과 계산 근거를 기록하는 필드이며
실제 통화 사용량을 원격으로 감시하는 한도 장치가 아닙니다. 금액을 임의 추정하지 않습니다.
plan 작성 뒤 코드가 바뀌면 새 plan을 생성해야 합니다. 기존 output 재사용과 자동 resume는 없습니다.

## durable 기록과 audit

새 디렉터리에서 `plan.json`을 exclusive/fsync 저장하고 다음 journal 상태를 연결합니다.

```text
PLAN → ALLOCATION → INTENT(준비 경험/관찰/request/body)
     → RESPONSE(raw 또는 안전한 실패) → aNNN.json(export) → RESULT(hash)
     → 다음 ALLOCATION 또는 END
```

준비 실패는 `PREPARATION_FAILED`에 가능한 partial snapshot을 남기며 호출하지 않습니다.
INTENT가 남으면 실제 네트워크 진입 여부를 몰라도 호출 한도를 소비합니다. RESPONSE를 export보다
먼저 durable하게 저장합니다. RESULT까지 없으면 응답/실행 성공을 추정하지 않습니다.
응답 전 timeout/connection/credential/size/HTTP 오류는 infrastructure입니다.
refusal/incomplete/invalid JSON/action, 실제 REJECTED/FAILED 및 잘못된 목적지/WAIT는 검증 가능한
behavioral outcome에 포함합니다. 응답 identity 모순/불완전 Engine·receipt 증거는 integrity exclusion입니다.
첫 infrastructure/기록/무결성 오류 뒤에는 추가 호출을 하지 않습니다.

fsync 실패가 읽을 수 있는 줄을 남길 수 있습니다. executor는 실패를 기억하여 INCOMPLETE를
반환하고 가능한 경우 별도 `recording-failure.json`을 exclusive/fsync 기록합니다. 그 표식이 가리키는
allocation은 UNKNOWN_RECORDING으로 보고합니다. 표식 저장까지 실패하면 반환값은 실패를 알지만,
사후 파일만으로 모든 OS durability 실패를 복원할 수는 없습니다. 파일 hash는 fsync의 물리적
성공이나 외부 서비스 실행 사실을 증명하지 않습니다.

`audit`와 `inspect`는 같은 읽기 전용 전체/prefix 검사입니다. Provider 생성, key, socket 없이
계획의 mode/config 순수 검증·journal/export mode 일치·순서·호출 상한·hash chain·export 연결, 새 실제 준비 관찰, semantic/prompt/전체 body,
raw response parse와 평가, 별도 ReplayHarness의 Engine replay를 검사합니다.
모든 allocation을 behavioral, infrastructure, unknown, integrity exclusion, not-attempted로
보고합니다. 실패나 미시도 allocation을 목록에서 삭제하지 않습니다. END 없는 prefix는 INCOMPLETE,
완성된 모순은 EXCLUDED입니다. INCLUDED는 기록의 일관성이며 behavioral 성공/실제 LLM 효과를 뜻하지
않습니다. `mode`와 allocation별 category/denominator를 함께 보셔야 합니다.
원본을 수정하지 않으며 torn final line만 prefix로 보고 완전한 corrupt 줄은 거부합니다.
RESULT 이전 orphan export는 성공으로 인정하지 않습니다.
audit에서 현재 source/runtime 일치나 사용자 실행 flag, key/transport/network를 검사하지 않습니다.
mode는 researcher-only 계획 정보이며 요청 body와 모델 입력에는 추가하지 않습니다.

외부에 보관한 plan digest를 audit의 `--expected-plan-sha256`으로 대조할 수 있습니다.
모든 파일과 hash를 동시에 일관되게 위조한 경우까지 탐지하는 서명 시스템은 아닙니다.

## 실행 명령

현재 작업 worktree에서 다음 환경을 사용합니다. 원본 `.venv` launcher를 복구/변경하지 않았습니다.

```powershell
Set-Location 'C:\Users\<user>\.codex\worktrees\experiment03-live-preparation\JourneyMap'
$journeyPython = 'C:\Users\<user>\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$env:PYTHONPATH = "$PWD\src;C:\Users\<user>\Documents\JourneyMap\.venv\Lib\site-packages"
New-Item -ItemType Directory -Force trials/new-pickup-session | Out-Null
& $journeyPython -m journeymap.examples.pickup_cue_live plan --output trials/new-pickup-session/draft.json
& $journeyPython -m journeymap.examples.pickup_cue_live plan --mode recording-mock --mock-config --output trials/new-pickup-session/mock-plan.json
& $journeyPython -m journeymap.examples.pickup_cue_live mock --plan trials/new-pickup-session/mock-plan.json --output trials/new-pickup-session/mock-run
& $journeyPython -m journeymap.examples.pickup_cue_live audit trials/new-pickup-session/mock-run
& $journeyPython -m journeymap.examples.pickup_cue_live inspect trials/new-pickup-session/mock-run
```

이미 존재하는 plan/output에는 덮어쓰지 않으므로 재실행에는 새 경로를 사용해 주세요.
CLI 무인수는 도움말만 출력합니다. `mock`과 `live`는 별도 명령이며 `execute` 기본값은 offline입니다.

최종 승인 후의 명령 형식은 아래와 같습니다. 이번 작업에서는 실행하지 않았습니다.
먼저 아래 표의 모든 값을 채운 config JSON을 `trials/approved-pickup-config.json`에 준비해야 합니다.
config에는 `provider, model, returned_models, reasoning_effort, max_output_tokens, timeout_seconds,
max_response_bytes, model_support_review, budget_review`만 포함합니다.

```powershell
& $journeyPython -m journeymap.examples.pickup_cue_live plan --mode live --config trials/approved-pickup-config.json --allocation-count 1 --call-cap 1 --engine-seed 42 --allocation-seed 0 --output trials/approved-smoke-plan.json
$approvedDigest = (Get-Content trials/approved-smoke-plan.json -Raw | ConvertFrom-Json).plan_sha256
# 사용자에게 이 plan/digest와 1회 비용 범위를 제시하고 명시적 승인을 받은 뒤에만 실행합니다.
& $journeyPython -m journeymap.examples.pickup_cue_live live --plan trials/approved-smoke-plan.json --output trials/approved-smoke-run --enable-live --approved-plan-sha256 $approvedDigest
& $journeyPython -m journeymap.examples.pickup_cue_live audit trials/approved-smoke-run --expected-plan-sha256 $approvedDigest
```

## 승인 전에 채울 실행 설정표

| 항목 | 현재 상태 / 확정할 내용 |
| --- | --- |
| provider/model | 경로는 OpenAI Responses, 실험 model ID는 미확정 |
| mode | 기본은 recording-mock, live 계획에는 명시적으로 live를 동결해야 함 |
| returned_models | 허용할 실제 반환 ID들의 exact 목록 미확정 |
| reasoning_effort | 미확정, READY의 null은 의도적인 생략 |
| max_output_tokens | 미확정, reasoning token을 포함한 상한 |
| timeout_seconds / max_response_bytes | 둘 다 미확정 |
| model_support_review | 선정 모델의 공식 지원 문서 URL 미확정 |
| engine_seed / allocation_seed / 순서 | 초안 42 / 0, 확정된 plan 목록 검토 필요 |
| allocation_count / call_cap | 검토 초안 40 / 40, 최소 smoke 별도 1 / 1 승인 필요 |
| budget_review | 승인 통화·최대 금액·단가/입력 및 출력 산정 근거 미확정 |
| 승인 범위와 plan digest | 승인자·대상 plan·실행 범위 미확정, flag만으로 승인되지 않음 |

승인 후 최소 smoke는 한 번만 호출해 schema 수용, model/response identity, usage, 요청 본문,
실제 receipt, journal과 Provider-free audit를 확인합니다. HTTP/schema 거부, timeout, identity 차이,
credential echo, 기록 오류, audit 모순이면 즉시 중단합니다. refusal/incomplete/invalid action도
그대로 기록하고 성공으로 교체하지 않습니다. smoke 한 번의 task 성공 여부로 다음 40회를 자동
승인하거나 memory 효과를 주장하지 않습니다. 후속 실행은 결과 검토 후 새 계획·경로·승인으로 분리합니다.
