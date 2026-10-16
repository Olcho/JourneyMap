# Phase 4.4: 생활 실행 출력 한도 8,192

2026-10-16 KST. 기존 실제 실행은 high/4,096에서 다섯 번째 응답이 INCOMPLETE가 되어
tick 9에 중단됐다. 이번 변경은 high를 유지하고 요청당 출력 한도만 8,192로 올리는
오프라인 구현이다. 새 실제 모델 실행 결과나 추가 유료 실행 승인을 뜻하지 않는다.

## 고정 계획

| 항목 | Phase 4.3.2 | Phase 4.4 |
| --- | --- | --- |
| 모델 | gpt-5.4-mini-2026-03-17 | 동일 |
| reasoning / tier | high / default | 동일 |
| 인물 / seed / 목표 | Ada / 42 / 최대 24시간 | 동일 |
| 요청당 max_output_tokens | 4,096 | 8,192 |
| 최대 호출 / 재시도 | 32 / 없음 | 동일 |
| 누적 입력 / 출력 | 500,000 / 131,072 | 동일 |
| 요청당 입력 예약 | 400,000 | 동일 |
| 코드 예산 | USD 1.00 | 동일 |
| timeout | 120초 | 동일 |
| 후보 텍스트 byte 한도 | 16,384 | 동일 |
| preflight 버전 | life-responses-preflight-1 | life-responses-preflight-2 |
| paid protocol | life-paid-responses-1 | life-paid-responses-2 |

LifeExecutionPlan은 여전히 단일 동결 계획이다. 요청당 4,096/16,384 또는 다른 설정을
생성자로 지정하면 거부한다. 모델, 프롬프트, 행동 schema와 세계 정책은 변경하지 않는다.
모의 UI의 기본 1,024 출력 설정도 유지한다. LifeLLMConfig는 16–8,192 정수만 허용한다.

## 요청 검증과 기존 연구 분리

생활 전용 `life_body`가 high와 출력 정수 범위, 허용 파라미터 키를 검증한다.
기존 연구용 `parameters_v1` 및 `request_body`의 4,096 상한은 그대로다.
생활 후보 schema와 기존 4,096 이하 생활 요청 body는 이전 표현과 동일하다.
새 paid/preflight 전송 경로는 고정 계획의 8,192와 정확히 일치하는 요청만 받는다.
임의 도구, 추가 파라미터, 추론 강도 변경, 누락된 설정은 허용하지 않는다.

## 예약과 회계

고정 단가 입력 USD 0.75/백만, cached input USD 0.075/백만, 출력 USD 4.50/백만을 유지한다.
요청당 조건부 최대 예약은 400,000 × 750 + 8,192 × 4,500 = 336,864,000 nano-USD다.
이는 실제 청구가 아니라 전송 전 예약이며 보고된 usage로 정산한다. 추론은 출력에 포함된다.

- 요청당 출력 8,192를 전부 쓸 때 출력 요금은 USD 0.036864, 입력 요금은 별도다.
- 누적 토큰 상한을 전부 사용한 고정 단가 계산은 USD 0.964824다.
- 매번 출력 8,192를 쓰면 16회가 누적 출력 131,072를 소진한다. 최대 32회는 보장 횟수가 아니다.
- 잔여 출력이 정확히 8,192면 예약할 수 있고 8,191이면 전송 전에 막는다.
- 기존 비용이 663,136,000 nano-USD면 다음 최대 예약이 정확히 USD 1.00에 닿는다.
  1 nano-USD라도 더 많으면 전송 전에 막는다.
- INCOMPLETE가 되어도 유효 usage는 정산하고 세계를 정지하며 자동 재시도하지 않는다.
- usage가 예약보다 크거나 확인할 수 없으면 예약을 유지하고 불확정 상태로 정지한다.
- 더 긴 추론은 응답 시간도 늘릴 수 있다. 8,192가 완주 또는 정상 행동을 보장하지 않는다.

## 승인과 과거 기록

새 source/plan/protocol digest로 결합한다. 이전 승인 binding은 registry 생성, 키 조회,
연결 생성 전에 거부한다. 기존 registry 경로와 소비 ID 기록은 유지하며 초기화하지 않는다.
이미 사용한 `86212ada-c33b-4813-94a9-2fdf4a32774d`는 다시 실행하지 않는다.

현재 source identity를 요구하는 기존 replay 정책을 완화하지 않았다. 새 소스는 과거 report를
자동 변환하지 않고 source/protocol/plan mismatch로 거부한다. 과거 실행의 정확한 재생에는
동결된 커밋 `b7717c2112c076b6f194d26ca0507d13b5b8a4d1`을 별도 폴더로 사용한다.
기존 실제 report의 별도 원본 파일도 필요하다. 공개 저장소에는 그 파일이 포함되지 않는다.

```powershell
git clone --branch backup/journeymap-actual-20261016 https://github.com/Olcho/JourneyMap.git JourneyMap-Actual-4096
Set-Location JourneyMap-Actual-4096
git switch --detach b7717c2112c076b6f194d26ca0507d13b5b8a4d1
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e .
& .\.venv\Scripts\python.exe scripts/verify_actual_backup.py
# 실제 원본 report의 로컬 경로를 지정한다. 키와 승인 파일은 필요하지 않다.
& .\.venv\Scripts\python.exe -m journeymap.examples.life_paid --replay C:\private\live-report.json
```

이 경로는 과거 report의 replay용이다. checkpoint의 자동 소스 migration이나 유료 실행의
중간 재개는 추가하지 않았다. old binding의 검사를 건너뛰거나 report를 재봉인하지 않는다.

## 오프라인 검증과 실행

`tests/test_life_output_budget.py`는 다음을 검증한다.

- 과거 계획 JSON을 복구해 digest가 같음을 확인한다. 실제 계획 값 변경은 출력 상한 하나다.
- 기존 연구 상한 보존, 옛 생활 wire 동일성, 정수/상한/파라미터 allowlist.
- 4,096/16,384 요청과 old source/plan/protocol binding을 전송 전에 거부.
- 16회 최대 출력 후 누적 한도 정지와 정확한 잔여 예약 경계.
- 합성 6,000/8,192 출력의 정상 행동, 8,192 INCOMPLETE, 8,193 예약 초과의 회계/replay.

기존 timeout/late 회계, 저장 기록 누락과 상태/회계 변조 반례도 새 계획에서 회귀 검증한다.
테스트의 합성 승인, 가짜 키와 가짜 HTTPS 응답은 실제 실행 증거가 아니다.

```powershell
& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m ruff format --check .
& .\.venv\Scripts\python.exe -m mypy
& .\.venv\Scripts\python.exe -m journeymap.examples.life_preflight --output artifacts/phase4_4/preflight.json
```

preflight 출력 파일은 새 경로여야 한다. `life_paid` 기본 동작은 승인 없는 제안이며,
실제 실행에는 새 실행 ID, 소스와 계획 검토, 사용자 명시적 승인 및 그에 결합된 승인 파일이
필요하다. 이번 작업에서는 실제 키 조회, 승인 파일 생성, 추가 유료 호출을 하지 않는다.
