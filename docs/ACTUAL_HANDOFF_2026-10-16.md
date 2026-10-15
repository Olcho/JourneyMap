# JourneyMap Actual 인수인계

기록 기준: 2026-10-16 KST. 이 문서는 새 기능 구현이나 새 실행 승인이 아니라
Phase 4.3.2 검토 소스의 GitHub 백업과 노트북 개발 인수인계다.

## 구현 상태

Phase 1의 동시 행동에서 Phase 2 생활 순환, Phase 3 관찰 UI와 직접 체크포인트,
Phase 4 모의 Provider, Phase 4.1 오류/timeout 수정, Phase 4.2 Responses 사전 검증,
Phase 4.3 승인/HTTPS/journal, Phase 4.3.1 및 4.3.2 기록 감사 보완까지 완료했다.
전체 1,356개 테스트와 Ruff/mypy 통과는 제출된 Phase 4.3.2 검증 기록의 수치다.
백업을 만든다는 이유로 이 과거 검증을 새 기기에서 실행한 결과라고 표시하지 않는다.

전원 규칙 생활 실행은 5명, 최대 168시간이다. 원료나 임금 부족에 대한 일반적 조달/구제,
168시간 이후 장기 순환, 기억 선택 효과는 구현/입증 완료 범위가 아니다.
E03/E04 연구 실험 및 05A/05B 설계와 Actual 생활 본체의 완료 근거를 분리한다.

## 완료한 실제 실행

- Run ID: `86212ada-c33b-4813-94a9-2fdf4a32774d` (사용 완료, 재사용 금지).
- 모델: `gpt-5.4-mini-2026-03-17`, reasoning `high`, service tier `default`.
- Ada, seed42, 목표 최대 24시간, Responses 최대 32회, 재시도 없음.
- 요청당 출력 4,096, 누적 입력 500,000, 누적 출력 131,072, 코드 예산 USD 1.00.
- 실제 Responses 5회, MOVE 2회와 WORK 2회 완료. 5번째는 행동 출력 없이 INCOMPLETE.
- 종료 tick 9, 상태 PAUSED_ERROR/INCOMPLETE. 24시간 완주 실패.
- 입력 5,871, 출력 14,017, 그중 reasoning 13,893. cached input 0.
- 고정 단가 계산 USD 0.06747975. 실제 청구서 확인과 구분한다.
- 무재호출 replay와 감사 통과. 회계 확정과 registry FINISHED는 목표 완주를 뜻하지 않는다.
- Ada 지갑 4 → 24, 구매/소비/휴식 완료 없음. 최종 hunger 38, fatigue 27.

5번째 응답은 `incomplete_details.reason=max_output_tokens`, 출력 4,096 중 추론 4,096이었다.
의도했던 행동과 숨겨진 추론 내용은 알 수 없다. 공개 백업에는 실제 raw response,
승인 파일 및 registry를 넣지 않았다. 데스크톱의 원본과 검토 ZIP을 별도 보존한다.

## 다음 후보: high 유지, 요청당 출력 8,192

아직 코드에 적용하지 않았다. 4,096 → 8,192로 한 변수만 변경하여 출력 한도 효과를 확인한다.
이는 OpenAI의 필수 권장값이 아니라 이번 실행 결과를 바탕으로 한 점진적 비교안이다.
한도를 높여도 완주, 정상 행동 출력, timeout 회피를 보장하지 않는다.

| 요청당 출력 한도 | 출력 전부 사용 시 출력 요금 | 비고 |
| --- | --- | --- |
| 4,096 | USD 0.018432 | 기존 |
| 8,192 | USD 0.036864 | 우선 후보 |
| 16,384 | USD 0.073728 | 후속 검토 |

입력 요금은 별도다. 고정 단가는 입력 USD 0.75/백만, 출력 USD 4.50/백만이다.
누적 입력 500,000과 출력 131,072를 유지할 때 계산상 최대는 USD 0.964824다.
8,192를 매번 모두 쓰면 누적 출력 상한은 16회 분량이다. 최대 32회는 보장 횟수가 아니다.
매 요청 입력 예약 400,000과 출력 예약, 잔여 예산 검증에 의해 더 일찍 종료될 수 있다.
reasoning 토큰은 출력 토큰에 포함하며 중복 계산하지 않는다.

근거: [Reasoning guide](https://developers.openai.com/api/docs/guides/reasoning),
[GPT-5.4 mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini).

### 함께 검토해야 할 구현 경계

- `adapters/life_llm.py`: LifeLLMConfig의 4,096 상한.
- `adapters/life_responses.py`: LifeExecutionPlan 기본값과 고정 wire 검증.
- `adapters/life_http.py`: 실제 HTTPS 요청의 고정 파라미터 검증.
- `adapters/life_admission.py`: plan/source/protocol 결합, 새 승인과 registry.
- `experiments/paid_life.py`, `paid_replay.py`, `paid_audit.py`: 실행/기록/replay 호환.
- `tests/test_life_responses.py`, `test_paid_life.py`, `test_paid_audit*.py` 및 관련 경계 테스트.

현재 replay는 source identity를 대조하고 4,096 설정을 전제한다. 단순히 숫자 하나를 바꾸지
않고 새 버전과 과거 기록 처리 정책을 함께 정한다. 원래 동결 소스로 과거 report를 재생할
경로를 항상 유지한다. 기존 journal, 원문, 회계 및 실패 상태를 성공처럼 변환하지 않는다.
추가 유료 실행에는 새 계획, 새 실행 ID와 새 명시적 승인이 필요하다.

## 기준점과 백업 검증

- 시작 HEAD: `97a5b6a24079b1f3bf3afb45bac85d30c1468336`.
- 데스크톱 브랜치: `codex/phase1-concurrent-runtime`, 미커밋 작업은 이 채팅에서 변경하지 않았다.
- 검토 ZIP: `JourneyMap_Phase4_3_2_review_2026-10-15.zip`.
- ZIP SHA-256: `84ae6f83d3d366f6b7800e999fbbd426a9cd9b443d3c64d7eb613f5a86c2aba0`.
- source identity: `b0cd3e00bc45b27bc7e8ffe63bc3481e4ff23d8eb8c61e48ea46fb02f66d5a06`.
- plan digest: `89447928f2357efef57f0f9cbc9ce8a53beced16cdb7a5f6136301d34b95380d`.
- protocol digest: `fe36110945c5b323c583268e067fb73e462ad004ffc87f07117d30f4f5d8b322`.

`scripts/verify_actual_backup.py`는 검토 소스 224개 파일의 LF 정규화 hash와
모든 package Python 파일의 source identity를 확인한다. 추가 인수인계 파일은 이 원본
manifest에 포함하지 않는다. source identity는 경로와 Python 내용에 기반하므로
절대 설치 경로와 Windows/Linux 줄바꿈 차이의 영향을 받지 않는다.

실행법은 [ACTUAL_START_HERE.md](../ACTUAL_START_HERE.md)를 따른다.
이번 백업은 실제 모델 호출이나 키 조회를 수행하지 않는다.
