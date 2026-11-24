# 수령처 안내 연속 행동 계약

2026-10-06 구현. [one-shot](EXPERIMENT03_LIVE_PREPARATION.md)과 별도 프로토콜입니다.
기존 one-shot prompt/plan/export/evaluator/audit, 실제 40회 원본과 이전 승인 기록을 변경하지 않습니다.
이번 작업은 코드·mock·독립 audit·다음 실행 검토 계획이며 **실제 API 호출 0회**입니다.

## 과제와 정보 경계

inn/bakery × d=1..4 × no-event-memory/recency-k1/k2/k3/full-history의 40조합입니다.
각 조합은 새 Kernel/Application/GamePort/archive/Session으로 시작합니다. 실제 scripted 준비 5행동,
tick 7, Square 출발, 원래 d 정의를 유지합니다. 기본 seed는 Engine 42 / allocation 6입니다.
기존 full-prefix 위치의 allocation을 full-history로 대응시켜 shuffle 순서를 유지합니다.
첫 입력의 semantic 관찰·기억은 one-shot과 같지만 새 profile/instructions/schema name 때문에
전체 wire는 다릅니다. 기존 응답을 새 실험의 첫 응답으로 쓰지 않습니다.

고정 instruction은 현재 위치를 Observation에서 읽고 최대 6번 기회 안에 수령처에 도착하도록 합니다.
별도의 남은 기회 context는 제공하지 않습니다. 모델에 target/d/조건/단계 번호/숨은 tick/연구 ID를
노출하지 않고 현재 관찰과 선택된 닫힌 경험만 전달합니다. Knowledge는 빈 상태입니다.
prompt에는 대화/previous_response_id/요약/자유 텍스트 이력/연구용 목표 저장소가 없습니다.
Provider는 request/config/byte transport만 받고 GamePort와 연구 상태를 갖지 않습니다.
모델 출력은 MOVE/WAIT이며 기존 validator와 권한 envelope, Engine boundary를 그대로 사용합니다.

## 기억과 재확인

no-event-memory는 매 단계 경험 0개, recency-k는 준비 및 후속을 합친 최신 k개 닫힌 경험입니다.
full-history는 닫힌 경험 전체이며 policy identity는 `pickup-sequential-full-history-1`입니다.
one-shot의 FullPrefixMemory와 delivered_cue는 연속 단계의 정책/가시성 계산에 사용하지 않습니다.
준비 orchestration만 기존 trusted PickupSession에서 재사용하고 평가 loop/metrics는 분리했습니다.

current Observation에는 Well에서만 안내가 보입니다. 단계별 `cue_current_present`,
`cue_memory_present`, `cue_any_present`, 기억 내 cue 개수를 별도로 기록합니다.
반복된 동일 cue는 허용하고 서로 상충하는 cue 및 scenario target과의 모순은 무결성 오류입니다.
cue flags와 연구용 cue_target은 모델 입력 밖입니다. 모델 입력에 담긴 합법적인 안내 자체는 허용합니다.

준비 이후 Well에서 얻은 **실제 단계 관찰**에 안내가 있을 때 재확인으로 셉니다. Well MOVE 의도나
도착 사실만으로 세지 않습니다. Well에서 WAIT하여 다음 기회에도 안내를 관찰하면 별도 재확인입니다.
마지막 MOVE 후 후속 관찰이 없으면 새로운 재확인으로 세지 않습니다.
재확인 횟수와 최초 단계는 보존하되 모델의 내적 의도는 추정하지 않습니다.
no-event-memory는 Well에서 Square로 돌아오면 안내를 잃습니다. 이 구조적 제약을 일반 추론 결함으로
해석하지 않습니다. recency-k1 귀환 직후에는 Well의 행동 전 관찰이 닫힌 최신 경험에 남습니다.

## 중단과 지표

한 조합의 결정 기회는 첫 선택 포함 최대 6회, 전체 계획 호출 cap은 최대 240입니다.
별도 tick 제한은 없고 tick은 비용입니다. 실제 정답 도착 직후 종료합니다.
유효한 오답 MOVE/WAIT, 정상 Engine REJECTED/FAILED는 다음 새 관찰로 진행할 수 있습니다.
refusal/incomplete/invalid output은 해당 조합을 RESPONSE_FAILURE로 종료하고 재호출하지 않습니다.
첫 infrastructure/recording/integrity 오류는 전체 실행을 중단합니다. retry/fallback/replacement/
실패 조합 재실행/resume는 없습니다. 전체 cap 소진은 CALL_CAP이며 아직 끝내지 못한 조합은 최종 unknown입니다.

immediate_success는 첫 결정의 실제 결과/ActorMoved/최종 위치로 판정하고 후속 결과로 수정하지 않습니다.
eventual_success는 6번 안의 실제 최초 도착, recovered_success는 첫 실패 뒤 최종 성공입니다.
응답 실패와 한도 소진은 false 및 별도 이유, infrastructure와 미완료는 최종 null 및 category입니다.
정상 Engine SUCCEEDED/REJECTED/FAILED/NOT_SUBMITTED는 과제 성공과 별도로 기록합니다.
첫 점수는 검증됐지만 이후 실패한 경우 첫 점수를 보존합니다. audit의 무결성 오류는 영향을 받는
현재 조합의 이후 최종 증거를 배제하며 이전 조합과 검증된 첫 단계는 유지합니다.

`decision_attempts`, `submitted_actions`, `successful_moves`, `wait_count`, `elapsed_ticks`는 준비 이후만
계산합니다. 미완료 prefix의 durable INTENT도 시도를 소비하지만 아직 제출한 행동으로 세지 않습니다.
성공일 때 steps/ticks_to_success 및 extra_actions/extra_ticks를 기록합니다. 실패의 성공 비용은 null이며
실제 소모된 시도·행동·ticks를 별도로 보고합니다. 준비 실패에는 준비 이후 elapsed_ticks도 null입니다.
extra_actions는 실제 제출 횟수에서 최소 합법 행동 수를, extra_ticks는 elapsed에서 최소 이동 ticks를 뺍니다.
연구용 기준은 실제 유향 route/비용/passability에서 계산하며 이 fixture에서 Square→target은 1행동/2tick입니다.
이 baseline을 모델에 보내지 않습니다. 최종 도착만으로 cue 활용을 단정하지 않습니다.

## 계획과 durable 기록

plan/execution/export/audit identity는 `pickup-sequential-plan-1` / `pickup-sequential-execution-1` /
`pickup-sequential-export-1` / `pickup-sequential-audit-1`입니다.
metrics=`pickup-sequential-arrival-cost-1`, profile=`alderwick-pickup-sequential-1`,
configuration=`pickup-sequential-responses-1`, wire=`pickup-sequential-move-wait-1`입니다.
HTTP adapter는 안전한 기존 one-shot 전송 구현을 재사용하므로 response provenance의 HTTP adapter version은
`pickup-cue-responses-http-1`입니다. 새 mock adapter는 `pickup-sequential-recording-1`입니다.

mode/source/runtime/config/profile/memory semantics/순서/단계 및 전체 cap/중단 정책/metrics를 plan digest에 묶습니다.
live 계획은 기본 DRAFT이고 이전 승인을 복사하지 않습니다. 후보 config는 PENDING_NEW_USER_APPROVAL입니다.
`--reviewed`는 검토 후 READY를 생성하는 옵션이며 사용자 승인을 대신하지 않습니다. PENDING 예산 config는
READY로 실행할 수 없습니다. 실행에는 현재 source/runtime 일치, 새 경로, 명시 enable-live와 승인 digest가 필요합니다.

```text
PLAN → CASE_START → PREPARED(실제 5행동)
  → INTENT(step, 관찰/기억/body/cue) → RESPONSE → STEP_RESULT(실제 Engine/metrics snapshot)
  → 다음 INTENT 또는 export aNNN.json → CASE_END(hash)
  → 다음 CASE_START 또는 END
```

INTENT가 durable하게 남으면 실제 전송 여부를 몰라도 cap을 소비합니다. RESPONSE를 Engine 제출보다 먼저
저장하고 STEP_RESULT에는 재생 가능한 실제 결과를 남깁니다. 매 단계 새 Provider/transport는 단회입니다.
기록 실패 시 가능한 경우 allocation/step/phase를 marker에 남기고 재개하지 않습니다.
key/인증 헤더/원문 exception/HTTP error body는 저장하지 않고 기존 key echo 차단도 유지합니다.

## 독립 audit와 실행 준비

audit/inspect는 Provider/key/socket 및 현재 source/runtime 조회 없이 plan/journal mode/hash/순서/cap을 검사합니다.
새 Session에서 실제 prefix와 각 raw 응답을 적용하여 관찰·기억 선택·cue·wire·receipt·성공/비용을 재구성하고,
매 STEP_RESULT의 ActionRequest stream을 별도 ReplayHarness로 재생합니다. export 요약을 그대로 믿지 않습니다.
정상 complete는 INCLUDED, prefix/torn tail/recording marker는 INCOMPLETE, 완성된 모순은 EXCLUDED입니다.
orphan export는 성공으로 인정하지 않습니다. 계획/시도/첫 점수/최종 결과 분모와 모든 미시도/unknown을 남깁니다.
검증된 STEP_RESULT의 도착 증거는 유지하되 recording marker가 영향을 가리키면 최종 결과는 unknown으로 둡니다.
hash는 내부 일관성 증거이며 OS durability나 외부 실행을 서명하는 장치가 아닙니다.

```powershell
$journeyPython = 'C:\Users\<user>\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$env:PYTHONPATH = "$PWD\src;C:\Users\<user>\Documents\JourneyMap\.venv\Lib\site-packages"
& $journeyPython -m journeymap.examples.pickup_sequential plan --output trials/new-sequential/mock-plan.json
& $journeyPython -m journeymap.examples.pickup_sequential mock --plan trials/new-sequential/mock-plan.json --output trials/new-sequential/mock-run
& $journeyPython -m journeymap.examples.pickup_sequential audit trials/new-sequential/mock-run --expected-plan-sha256 <동결digest>
& $journeyPython -m journeymap.examples.pickup_sequential inspect trials/new-sequential/mock-run --expected-plan-sha256 <동결digest>
```

부모 경로는 미리 만들고 각 plan/output에는 새 경로를 사용합니다. 실제 호출하지 않는 후보 생성은
`plan --mode live --config <새후보config> --output <새draft>`입니다.
검토 뒤 **새 사용자 승인·예산**으로 config를 갱신하고 `--reviewed` READY를 새 경로에서 생성해야 합니다.
향후 live 형식은 `live --plan <새READY> --output <새run> --enable-live --approved-plan-sha256 <승인digest>`입니다.
현재 DRAFT/이전 40회 승인을 그대로 실행에 사용하지 않습니다.

입력은 모델의 실제 행동에 의존하므로 240개 실제 body를 미리 확정하지 않습니다.
최대 10개 닫힌 경험과 현재 관찰로 입력이 커지는 점을 검토하며 토큰 실측은 미완료입니다.
공개 Standard 단가와 10k/25k/50k 입력 가정·출력 상한의 비용 민감도를 산출물에 남깁니다.
이는 승인 금액이나 청구액 hard limit이 아니며 이전 실측 비용×6을 최대 비용으로 쓰지 않습니다.
기존 연구 자료와 충돌하는 새 Notion 요구사항은 이번 작업에 제공되지 않았고 Notion 내용을 임의로 갱신하지 않았습니다.
