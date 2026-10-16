# Phase 4.4 오프라인 구현과 검증 보고서

2026-10-16 KST. 기준 커밋은 동결 백업 `b7717c2112c076b6f194d26ca0507d13b5b8a4d1`,
작업 브랜치는 `codex/actual-output-budget-8192`다. 실제 데스크톱 작업 트리에는 접근하거나
변경하지 않았다. 기존 main과 연구 브랜치, 4,096 백업 브랜치도 보존한다.

## 변경 결과

생활 실행의 요청당 출력 상한을 4,096에서 8,192로 늘렸다. 고정 모델, high/default,
Ada/seed42/최대 24시간, 호출 32회, 재시도 없음, timeout 120초, 누적 토큰과 USD 1.00 예산은
유지한다. 실제 모델이 새 한도에서 완주하는지는 검증하지 않았다.

공통 연구용 요청 검증의 4,096 상한은 그대로 두고 생활 전용 body 검증을 분리했다.
paid/preflight protocol은 v2로 구분하며 새 plan/source/protocol binding을 요구한다.
replay는 현재 계획의 값을 사용한다. 과거 report는 동결된 4,096 소스에서만 재생한다.

주요 runtime 변경은 다음 5개 파일이다.

- `src/journeymap/adapters/life_llm.py`: 생활 전용 allowlist와 8,192 정수 상한.
- `src/journeymap/adapters/life_responses.py`: 고정 8,192 계획과 preflight v2.
- `src/journeymap/adapters/life_admission.py`: paid protocol v2와 기존 승인 결합 유지.
- `src/journeymap/adapters/life_http.py`: 고정 계획과 요청 출력 한도 일치 검사.
- `src/journeymap/experiments/paid_replay.py`: 현재 계획의 출력/timeout을 이용한 분류 재검증.

기존 관련 테스트 2개 파일의 wire와 예약 금액 기대값을 새 계획에 맞췄고,
`tests/test_life_output_budget.py`에 22개 경계 테스트를 추가했다. 과거 보고서는 수정하지 않았다.
노트북 안내, API/테스트 전략/로드맵, 새 계약 문서와 이 보고서를 갱신했다.

## 검증 증거

- 새 경계 테스트: **22 passed**.
- 기존 Responses/paid/audit/response 감사 테스트: **107 passed**.
- 전체 회귀: **1,378 passed in 229.31s** (기존 1,356 + 신규 22).
- Ruff lint: `All checks passed!`.
- Ruff format: `225 files already formatted`.
- strict mypy: `Success: no issues found in 170 source files`.
- `git diff --check`: 통과.

기존 4096 계획을 JSON으로 복구한 digest가 과거 plan digest와 같음을 확인했다.
즉 실행 계획의 값은 max_output_tokens만 바뀐다. 프로토콜 버전 변경은 이 새 고정 계획을
구분하고 과거 승인을 차단하기 위한 기록 계약 변경이다.

합성 usage 6,000 및 8,192의 정상 응답은 행동 완료와 replay를 통과한다.
8,192 INCOMPLETE는 usage를 정산한 뒤 행동 없이 정지하며, 8,193은 예약 초과로 정지하고
336,864,000 nano-USD 예약을 유지한다. 추론 토큰을 별도로 더하지 않는다.
16회 최대 출력 후 누적 출력 한도와 잔여 8,192/8,191 경계도 검증했다.

오프라인 preflight CLI 결과는 **12회, tick 24, 171 events, pending 3개**다.
모든 wire가 출력 한도 8,192를 사용했고 recorded response 및 live input replay가 통과했다.
이 숫자는 모의 fixture 결과이며 실제 모델의 24시간 완주 근거가 아니다.

## 과거 실제 기록 보존

동결 백업 소스에서 기존 실제 report를 새 Python 프로세스의 CLI로 재생했다.
Provider.generate, API 키 환경 조회, socket/DNS를 차단한 상태에서 다음 결과를 얻었다.

```json
{"provider_recalled":false,"engine_equal":true,"journal_checked":true,"calls_checked":5,"audit_verified":true,"snapshot_state":"FINISHED","outcome_certain":true}
```

동일 원본을 새 8,192 소스에 전달하면 `paid replay source/protocol/plan mismatch`로 거부한다.
과거 기록이나 binding을 수정하지 않았다. FINISHED/회계 확정은 기존 tick 9 중단을
24시간 완주로 바꾸지 않는다.

- 과거 source identity: `b0cd3e00bc45b27bc7e8ffe63bc3481e4ff23d8eb8c61e48ea46fb02f66d5a06`.
- 과거 report SHA-256: `3f252e939c7578839697962c887ebdfb265fc02eb1e5ad3aa2f23b3f0e0a413d`.
- 새 source identity: `f29e9631f80b19d832e83ce7969318bae061a88b543424ed7254499aca91f973`.
- 새 plan digest: `b262f9f8c03d06a6b59acc08732d57965295b1141a2af4ef0fd13414552cbc91`.
- 새 protocol digest: `9dfd86a62f3c45570b86ad016b7a14a5fbbba71257cf8b76efe99b88b8eef970`.

## 다음 단계와 제약

실제 API 호출, 실제 키 조회와 실제 실행용 승인 파일 생성은 **0회**다.
테스트에서만 합성 승인과 가짜 키/HTTPS 응답을 사용했다.
독립 검토 후 새 source/plan/protocol과 새 실행 ID에 대한 명시적 승인으로만 다음 실제 실행을
진행할 수 있다. 과거 사용한 실행 ID나 승인을 재사용하지 않는다.

일반적인 경제 구제, 7일 이후 지속 실행, 새로운 기억 정책, 모델 변경, 실제 실행 재개는
이 변경에 포함하지 않는다. 더 큰 출력 한도에서도 INCOMPLETE나 timeout이 발생할 수 있다.
정확한 계약과 과거 replay 절차는 [PHASE4_4_OUTPUT_BUDGET.md](PHASE4_4_OUTPUT_BUDGET.md),
노트북 개발은 [ACTUAL_START_HERE.md](../ACTUAL_START_HERE.md)를 따른다.
