# Phase 4.4 실제 실행 1회 승인 요청안

작성·공식 자료 확인: 2026-10-17 KST. **미승인 / 실행 준비 미완료 / 이번 작업에서 실행하지 않음.** 이 문서는 사람이 검토할 제안이며 `ExecutionApproval` 파일이 아니다. 키 조회, 계정 접근, 유효한 승인 파일 생성, 실제 API 호출을 허가하는 문서로 사용하지 않는다.

## 사용자 판단이 필요한 미해결 항목

현재 상태는 **“현재 전체 회귀 통과, F1 미재현·원인 미확정, 독립 검토의 기존 수정 필요 판정 유지”**다. F1 조사는 종료됐고, 동일 assertion의 재발 또는 새로운 원인 증거가 없는 한 재조사하지 않는다. 이번 승인안 준비에서 재현 검사나 추가 테스트는 하지 않았다.

사용자는 **독립 검토가 여전히 ‘수정 필요’이며 F1 원인이 미확정임을 알고도 아래 고정 계획의 유료 실행 1회를 진행할지 별도로 판단해야 한다.** 승인안 작성 요청이나 전체 테스트 통과를 그 판단 또는 실행 승인으로 간주하지 않는다. 위험 수용을 결정해도 기존 검토 판정을 변경하거나 F1을 해결됨으로 표시하지 않는다.

또한 과거 실제 4096 실행 원본 report의 hash 및 동결 소스 replay는 독립 검토자가 직접 재검증하지 못했다. 합성 replay와 정상 tick 16 digest 대조를 과거 실제 원본 검증으로 대체하지 않는다. 이 미검증 범위도 함께 알린 상태에서 판단받아야 한다.

별개의 실행 차단 사항으로, **현재 노트북에는 canonical registry와 그 상위 폴더가 없다.** 기기 간 소비 이력 보존을 확인하지 못했으므로 실행 준비 완료로 표시할 수 없다. F1 불확실성을 수용한다는 결정만으로 registry 보존 조건이 충족되지는 않는다.

근거: [현재 독립 검토](C:/Users/USER/JourneyMap-Actual/docs/PHASE4_4_INDEPENDENT_REVIEW.md:342), [F1 보존 증거](C:/Users/USER/JourneyMap-Actual/artifacts/phase4_4/f1-investigation/closed-20261017-013503-KST-2b967cd804/verification.json). 마지막 비부착 전체 실행은 1378 passed / 872.12초였으며 이번 작업에서 재실행하지 않았다.

## 제안 대상과 identity

| 항목 | 이번에 직접 확인한 값 |
| --- | --- |
| 프로젝트 | `C:\Users\USER\JourneyMap-Actual` |
| 브랜치 | `codex/actual-output-budget-8192` |
| HEAD | `739f08d34325106cf2eaaacc3fadcdecd0858ddd` |
| source identity | `f29e9631f80b19d832e83ce7969318bae061a88b543424ed7254499aca91f973` |
| plan digest | `b262f9f8c03d06a6b59acc08732d57965295b1141a2af4ef0fd13414552cbc91` |
| protocol | `life-paid-responses-2` |
| protocol digest | `9dfd86a62f3c45570b86ad016b7a14a5fbbba71257cf8b76efe99b88b8eef970` |
| 새 run ID (제안만) | `abf0e374-6ac2-4a76-86da-fcefc8b9470b` |
| canonical registry | `C:\Users\USER\JourneyMap-Actual\artifacts\phase4_3\runs.sqlite3` |

run ID는 이번 작업에서 새 UUID로 생성했다. 아직 registry에 예약·소비하지 않았다. 이전 사용 ID와 다르지만, 누락된 registry 및 다른 기기의 상태까지 검사해 전역 미소비를 입증한 것은 아니다.

identity는 현재 소스의 `source_identity()`, `LifeExecutionPlan().digest`, `protocol_identity()`, `binding(new_run_id, REGISTRY)`로 계산했다. source identity는 패키지 Python 파일을 대상으로 하며 HEAD 또는 문서 hash와 같은 값이 아니다. **HEAD는 실행 전 운영 확인 조건이고 코드의 binding 필드는 run/source/plan/protocol/registry다.** 실행 전 HEAD와 이 tuple을 다시 대조하고, 변경되면 새 검토·승인 범위를 정하기 전 전송하지 않는다.

시작 시 미커밋 작업은 기존 독립 검토 문서와 `scripts/diagnose_checkpoint_view.py` 두 파일이었다. 소스·기존 테스트·기존 진단 도구·검토 문서·F1 보관 증거를 보존한다. 승인안 문서 추가는 패키지 source identity를 바꾸지 않는다.

## 고정 실행 계획

**‘실제 실행 1회’는 하나의 run/session이다. HTTPS 호출 1개를 뜻하지 않으며 이 run 안에서 최대 32번까지 전송할 수 있다.** 실패 후 같은 run을 다시 실행하거나 다른 run으로 자동 재시도하는 승인은 포함하지 않는다.

| 항목 | 고정값 |
| --- | --- |
| 모델 snapshot | `gpt-5.4-mini-2026-03-17` |
| reasoning / service tier | `high` / `default` |
| actor / seed | Ada (`ada`) / 42 |
| 가상 목표 | 최대 24시간, 1 tick = 1 가상 시간 |
| 요청당 max_output_tokens | 8192 |
| 최대 요청 | 32, 자동 재시도 없음 |
| 누적 입력 / 출력 | 500000 / 131072 토큰 |
| 코드 예산 | 1000000000 nano-USD = USD 1.00 |
| timeout | 요청/decision당 120초 |
| 요청당 입력 예약 | 400000 토큰; API 입력 토큰 수를 강제로 400000으로 만드는 설정은 아님 |
| 요청 body / 후보 텍스트 byte 한도 | 65536 / 16384 |
| 반복 engine REJECTED/FAILED 제한 | 3회 연속 (`invalid_limit=3`) |
| 모델 입력·세계 정책 | 현재 생활 prompt/schema/Observation-only 정책 유지 |
| 전송 | `POST https://api.openai.com/v1/responses`, `store=false`, 추가 tools 없음 |

CLI가 `LifeExecutionPlan`을 직접 사용하므로 이 값들을 명령행에서 임의 변경하지 않는다. 모델 교체, reasoning 변경, 예산 확장, memory 추가, 중간 유료 재개는 승인 범위 밖이다.

## 공식 공개 자료 확인과 비용

공식 [GPT-5.4 Mini 모델 문서](https://developers.openai.com/api/docs/models/gpt-5.4-mini)에서 지정 snapshot, `high`, Responses 및 structured outputs 지원을 확인했다. 공개 제한은 context 400000 / 최대 출력 128000이며 계획의 요청당 출력 8192는 공개 출력 제한 이내다. 모델이나 reasoning을 대체할 필요가 있는 공개 불일치는 발견하지 못했다.

같은 공식 모델 문서의 텍스트 토큰 단가는 다음과 같고 현재 코드 rate card와 일치한다. [Responses API reference](https://developers.openai.com/api/reference/python/resources/responses/methods/create)는 명시적 `service_tier=default`를 표준 가격·성능으로 설명한다. 반환 tier는 요청과 다를 수 있어 코드가 응답 model/tier를 다시 확인한다.

| 100만 토큰당 USD | 공식 공개 단가 | 코드의 토큰당 nano-USD |
| --- | --- | --- |
| 입력 | 0.75 | 750 |
| cached input | 0.075 | 75 |
| 출력 | 4.50 | 4500 |

[공식 reasoning 문서](https://developers.openai.com/api/docs/guides/reasoning)에 따라 reasoning은 출력 과금·출력 한도에 포함된다. 코드도 `output_tokens` 요금에 reasoning을 다시 더하지 않는다. 더 큰 출력 한도가 INCOMPLETE 방지나 24시간 완주를 보장하지 않는다.

고정 단가 계산:

- 요청당 전송 전 조건부 예약: `400000×750 + 8192×4500 = 336864000 nano-USD` = USD 0.336864.
- 출력 8192를 모두 쓸 때 출력 요금만 USD 0.036864; 입력 요금은 별도다.
- 누적 상한의 입력을 모두 uncached로 계산하면 `500000×750 + 131072×4500 = 964824000 nano-USD` = USD 0.964824.
- 매 요청 출력 8192를 모두 쓰면 16회로 누적 출력 상한에 닿는다. 32회·24시간은 보장 결과가 아니다.

이것은 공개 표준 단가에 따른 코드 예약·정산이며 실제 청구서 또는 계정 전체 USD 1.00 보증이 아니다. 공식 문서는 regional processing에 10% 추가 요금을 안내하며 본 경로는 고정 global endpoint를 쓴다. 실제 계정의 접근 권한·잔액·rate limits·프로젝트 정책·추가 요금 적용 여부는 계정에 접근하지 않아 확인하지 않았다. 가격·응답 identity/tier·usage를 확정할 수 없으면 정산을 추정해 계속하지 않는다. [공식 가격 안내](https://developers.openai.com/api/docs/pricing)

## registry 보존과 이전 ID 재사용 금지

호스트 계정에서 다음을 읽기 전용으로 확인했다.

| 확인 대상 | 관측 결과 |
| --- | --- |
| `artifacts\phase4_3\runs.sqlite3` | 없음 |
| `artifacts\phase4_3` 상위 폴더 | 없음 |
| registry의 `-wal`, `-shm`, `-journal` sidecar | 발견되지 않음; 상위 폴더도 없음 |
| 새 run ID의 예상 report 파일 | 없음 |
| 이전 기기의 registry 원본·보존 snapshot·소비 이력 | 이번 작업에서 제공되거나 확인되지 않음 |

`ACTUAL_START_HERE.md`는 registry DB가 노트북 백업 범위 밖임을 명시한다. 이전 실제 실행 ID **`86212ada-c33b-4813-94a9-2fdf4a32774d`는 사용 완료로 기록되어 있으며 어떤 경우에도 재사용하지 않는다.** 이 사실은 인수인계 기록에서 확인했고, 현재 노트북 DB로 재확인한 것은 아니다. 이전 승인 파일, 과거 제안 run ID, old source/4096 plan/protocol binding도 재사용하지 않는다.

`RunJournal`은 같은 DB에서 run ID PRIMARY KEY와 approval ID UNIQUE를 보존하고 소비된 ID를 다시 열지 않는다(`life_admission.py:130`). 그러나 **DB가 없으면 승인 통과 후 새 DB를 자동 생성하는 코드이므로, registry 누락 자체를 코드가 실행 전에 차단해 준다고 오해하면 안 된다.** 새 run ID만 정했다고 기기 간 소비 이력이 복원되는 것도 아니다.

실행 준비의 선행 조건은 원래 registry 또는 일관된 보존본의 위치·원본 hash·소비 이력과 기기 간 이전 상태를 확인하고, 기존 행/상태를 잃지 않는 보존·이전 방식을 별도로 확정하는 것이다. 이 작업에서는 복원·생성·초기화·기존 행 편집을 하지 않았다. 과거 run의 source/plan/protocol/registry binding을 새 값으로 재봉인하지 않는다. registry 부재를 숨기거나 다른 빈 경로로 우회하지 않는다. **이 조건이 해결되지 않은 현재는 아래 유료 명령을 실행하면 안 된다.**

## 정확한 대상 경로와 명령 — 현재 실행 금지

| 대상 | 경로 |
| --- | --- |
| 작업 디렉터리 | `C:\Users\USER\JourneyMap-Actual` |
| Python | `C:\Users\USER\JourneyMap-Actual\.venv\Scripts\python.exe` |
| 승인 파일 예정 위치 | `C:\Users\USER\JourneyMap-Actual\artifacts\phase4_3\approval-human-abf0e374-6ac2-4a76-86da-fcefc8b9470b.json` — **생성하지 않음** |
| canonical DB/journal | `C:\Users\USER\JourneyMap-Actual\artifacts\phase4_3\runs.sqlite3` |
| CLI가 쓰는 실제 report | `C:\Users\USER\JourneyMap-Actual\artifacts\phase4_3\abf0e374-6ac2-4a76-86da-fcefc8b9470b.json` |

Phase 4.4여도 paid CLI의 canonical 저장 경로는 기존 `phase4_3`다. 임의로 저장 경로를 바꾸어 소비 기록을 분리하지 않는다. 아래 코드는 검토용 정확한 명령이며 이번 작업에서 실행하지 않았다.

```powershell
# 현재 실행 금지. 아래 선행 조건과 별도 사용자 승인이 모두 충족된 후에만 실행.
Set-Location 'C:\Users\USER\JourneyMap-Actual'
& 'C:\Users\USER\JourneyMap-Actual\.venv\Scripts\python.exe' -B -m journeymap.examples.life_paid --execute --run-id 'abf0e374-6ac2-4a76-86da-fcefc8b9470b' --approval 'C:\Users\USER\JourneyMap-Actual\artifacts\phase4_3\approval-human-abf0e374-6ac2-4a76-86da-fcefc8b9470b.json'
```

전송 전에 필요한 확인:

1. 사용자가 F1 미확정·기존 수정 필요 판정·과거 실제 원본 replay 미검증을 알고 진행할지 명시적으로 판단한다. registry 보존 문제도 별도로 해소됐음을 확인한다.
2. HEAD와 source/plan/protocol/registry tuple, 모델·단가·고정 한도 및 새 ID의 미소비 상태를 다시 대조한다. 확인 불가 또는 불일치는 전송 중단이다.
3. 기존 registry와 필요한 sidecar를 일관된 상태로 보존한다. 기존 소비 행을 삭제·초기화하거나 과거 ID를 사용하지 않는다. registry 부재 상태에서 실행을 강행하지 않는다.
4. report 예정 경로가 비어 있음을 전송 전에 확인한다. CLI는 report 저장 시 `open("x")`를 쓰지만 그 검사는 유료 요청 이후이므로 덮어쓰기 방지만 믿고 전송하지 않는다.
5. 이후 **별도의 명시적 사용자 승인**을 받은 경우에만 새 approval ID와 정확한 bound를 갖는 유효한 승인 파일을 만들 수 있다. 실제 실행 시 키 읽기와 이 run의 유료 요청을 허용할 범위는 `read-openai-api-key`, `paid-responses`이며, 현재는 그 승인·파일 모두 없다.

인간 승인 없이 digest에서 권한을 자동 유도하지 않는다. 이번 문서에는 실행 가능한 유효 grant나 실제 키를 포함하지 않는다.

## 중단 조건과 timeout 후 회계

- 가상 horizon 24 도달: 새로운 세계 진행·의사결정을 종료하고 남아 있는 in-flight 행동을 기록한다. 모든 행동 완료를 주장하지 않는다.
- 요청 32회, 누적 입력/출력 또는 USD 1.00 코드 예약 한도: 다음 전송 전에 중단한다. 다음 요청은 입력 400000/출력 8192/비용 336864000 nano-USD를 전부 예약할 수 있어야 한다. 잔여 출력 8192는 허용, 8191은 거부다. 기존 비용 663136000 nano-USD는 정확한 다음 예약 경계다.
- 승인·binding·run 소비 상태 불일치, journal 오류, 요청 body 초과: 전송하지 않고 중단한다. 계정/키 관련 확인 실패도 임의 대체·재시도하지 않는다.
- non-200 HTTP(redirect·429 포함), 불확정 transport, 잘못된 JSON/응답/schema, model/tier mismatch, usage 부재·불일치·예약 초과: 더 보내지 않는다. 확정할 수 없는 usage/금액은 예약·불확정 상태로 보존한다.
- INCOMPLETE 또는 REFUSAL: 유효 usage는 정산하되 행동을 제출하지 않고 세계를 정지한다. 모델/추론 강도/출력 한도를 바꿔 재호출하지 않는다.
- 120초 decision timeout: 세계를 정지하고 late 응답은 행동에 사용하지 않는다. 취소·미청구가 증명된 것으로 취급하지 않는다. 유효 late usage가 있으면 회수·정산하고, 없으면 불확정 예약을 보존한다.
- malformed candidate 및 정보/권한/schema 위반은 오류 정지다. engine REJECTED/FAILED는 기존 정책의 연속 3회 제한을 적용하며 자동 후보 수정·fallback은 없다.
- 동일 F1 assertion이 실제로 재발하거나 새로운 원인 증거가 생기면 기록을 보존하고 재조사 조건 충족을 보고한다. 단순 INCOMPLETE/timeout이나 기존 미확정 상태 자체를 이유로 F1 반복 검사를 시작하지 않는다.

120초는 요청/decision 한도이며 전체 run의 wall-clock 한도가 아니다. `life_paid`는 종료 전 `session.drain()`으로 late 회계 회수를 최대 추가 120초 기다릴 수 있다. 이는 새 요청·재시도 또는 세계 진행의 승인이 아니다. 중단·저장 오류나 불확정 종료 뒤 같은 run 명령을 다시 실행하지 않는다.

## 종료 후 보존·무재호출 replay·회계 감사

종료 후 원래 registry, 필요한 SQLite sidecar, raw report, 실행 명령·HEAD/identity·stdout/stderr·종료 코드와 파일 목록·SHA-256을 별도 새 보관 폴더에 복사한다. 원본·기존 파일은 덮어쓰거나 삭제하지 않는다. API 키/Authorization header는 기록하지 않는다. 실제 실행 원본과 승인 파일은 공개 GitHub에 올리지 않는다.

원래 report를 그대로 두고 같은 동결 source identity에서 다음 **data-only** 명령으로 replay/회계 감사를 수행한다. 이 명령도 이번 작업에서는 실행하지 않았다.

```powershell
Set-Location 'C:\Users\USER\JourneyMap-Actual'
& 'C:\Users\USER\JourneyMap-Actual\.venv\Scripts\python.exe' -B -m journeymap.examples.life_paid --replay 'C:\Users\USER\JourneyMap-Actual\artifacts\phase4_3\abf0e374-6ac2-4a76-86da-fcefc8b9470b.json'
```

`replay_paid_report`는 Provider를 호출하지 않고 기록된 engine 입력·호출·journal·회계를 공통 감사한다. 확인할 항목은 `provider_recalled=false`, `engine_equal=true`, `journal_checked=true`, `audit_verified=true`, calls_checked 및 snapshot_state/outcome_certain이다. usage·cached input·출력/추론의 단일 과금·예약·정산·불확정 금액도 함께 기록한다. 실패하면 원본을 재봉인하거나 변조해 통과시키지 않는다.

CLI exit 0, FINISHED 또는 `engine_equal=true` 하나만으로 24시간 완주·행동 성공·회계 확정·감사 통과를 선언하지 않는다. tick, pending 행동, 오류 상태, audit 결과와 certainty를 분리한다. CLI가 replay를 `INCOMPLETE_JOURNAL_OR_RESPONSE`로 표시하거나 report를 남기지 못한 경우에도 DB·로그를 보존하고 유료 재실행으로 복구하지 않는다.

과거 4096 실제 기록은 원본과 동결 커밋 `b7717c2112c076b6f194d26ca0507d13b5b8a4d1`을 별도로 확보해야 한다. 새 소스로 재생하도록 binding을 바꾸지 않는다. 이번 새 run의 replay 성공이 과거 원본의 미검증 범위를 해소하지 않는다.

## 현재 결정 상태

| 항목 | 상태 |
| --- | --- |
| 공식 공개 모델·high·표준 단가 vs 계획 | 확인, 공개 자료와 코드 rate card 일치 |
| 현재 HEAD / source / plan / protocol | 확인, 기존 검토 tuple 유지 |
| registry 보존·기기 간 소비 이력 | **미확인 / canonical registry 부재 — 실행 차단** |
| 계정 접근 권한·잔액·quota·실제 서버 수락 | 미확인, 이번 작업에서 접근 금지 |
| F1 미해결을 알고 진행할지의 사용자 판단 | **별도 판단 대기** |
| 과거 실제 원본 replay 미검증의 수용 판단 | **별도 판단 대기** |
| 실제 실행 승인 / 유효 승인 파일 | 없음 / 생성하지 않음 |
| 최종 준비 상태 | **승인 요청안 작성 완료, 실제 실행 준비 미완료** |

사용자가 검토할 결정은 ‘보류’ 또는 ‘미해결 항목을 알고도 이 고정 계획의 1회 실행을 별도로 승인할지’다. 어느 판단도 자동으로 적용하지 않으며, 후자를 택하더라도 registry 보존 등 미충족 선행 조건을 해결하기 전 실행하지 않는다.
