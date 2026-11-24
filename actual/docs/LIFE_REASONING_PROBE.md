# JourneyMap Actual reasoning probe v1

후속으로 별도 [대응 의사결정 실행 계약](PROBE_EXECUTION.md)이 추가됐다. 아래 내용은 기존 data-only
probe의 계약이며 해당 모듈의 네트워크 기능은 여전히 없다. 유료 조립은 별도 모듈에서 검토한다.

2026-10-19 KST. 이 문서는 오프라인 구현 계약이다. 실제 모델의 토큰 절감이나 행동 개선 결과가 아니다.

## 목적과 비교 조건

현재 실제 high/4096 및 high/8192 실행은 각각 출력 한도에서 중단됐다.
출력 한도 소진은 관측 사실이며 내부 추론 반복은 확인된 원인이 아니다.
먼저 동일한 관측에 대한 한 번의 의사결정을 비교할 준비를 한다.

| 조건 | 모델 | 추론 강도 | 프롬프트 | 출력 상한 |
| --- | --- | --- | --- | --- |
| high_baseline | gpt-5.4-mini-2026-03-17 | high | 기존 원문 | 8192 |
| medium_baseline | 동일 | medium | 기존 원문 | 8192 |
| medium_structured | 동일 | medium | 기존 원문에 짧은 판단 순서 추가 | 8192 |

첫 비교는 effort만 다르다. 두 번째 비교는 판단 순서 문구만 다르다.
service_tier=default, store=false, strict 행동 schema와 관측은 동일하다.
프롬프트 변경본은 현재 자원 확인, 관측된 기회와 자기 직전 결과 확인, 다음 행동 하나 선택을
안내한다. 행동 정답, 임의 임계값, 자원 목표치, 미래 사실을 제공하지 않는다.
이 변경은 Plan-and-Budget에서 착안한 축소 응용이다. 논문의 재현 구현이 아니며
별도 planner, 수치형 soft token budget, memory, 모델 routing은 아직 구현하지 않는다.

참고 연구:

- [Token-Budget-Aware LLM Reasoning, Findings ACL 2025](https://aclanthology.org/2025.findings-acl.1274/)
- [Plan and Budget, ICLR 2026](https://proceedings.iclr.cc/paper_files/paper/2026/hash/ae8d4084f418bb51575c2ca6c658a05b-Abstract-Conference.html)
- [Scaling Small Agents Through Strategy Auctions, ICML 2026](https://proceedings.mlr.press/v306/alazraki26a.html)

## 사례의 출처와 정보 경계

12개 합성 사례에 세 조건, 두 반복의 총 72개 요청 슬롯을 만든다.
각 사례의 두 반복은 같은 요청 body를 사용한다. 조건 순서는 고정 순환한다.
사례별 반복은 독립된 새 세계 표본이 아니며 통계 검정의 독립 표본으로 세지 않는다.

사례는 집 tick 0, 작업장 tick 3, 노동 후 tick 6/9, 품절, 무자금,
임금 부족, 원료 부족, 이미 폐쇄된 경로, 이동 중 폐쇄될 경로,
집에서 대기한 tick 18, 구매 거부 직후다. 마지막 사례의 실패는 자기 공개 결과에 나타난다.
미래 폐쇄 예약은 researcher fixture에만 있고 모델에는 노출하지 않는다.

5명 세계의 다른 인물은 기존 RULE 정책으로 실행한다. Ada는 사례별 literal prefix를 따른다.
이는 과거 실제 run의 actor 순서와 상태를 재현했다는 뜻이 아니다. 과거 tick 6/9와 같은 시각의
사례가 있어도 실제 실패 입력과 동일하다고 주장하지 않는다. 실제 두 원본 요청의 내용 대조는
아직 완료하지 않았다.

모델 body는 기존 life_request의 actor-scoped Observation만으로 만든다.
case_id, prefix, config, source identity, world digest는 연구 기록이며 모델 input에 들어가지 않는다.
corpus.json 전체를 모델 입력으로 사용하면 안 된다. 전송 후보는 requests 항목의 body뿐이다.

## 실행 경계

- 구현 위치: experiments.life_reasoning_probe, examples.life_reasoning_probe.
- 기존 LifeExecutionPlan, life_body, paid runner 및 승인 검증의 high 고정은 유지한다.
- API, 키, 계정, registry 접근이나 Provider 선택 기능이 없다. --execute와 live 명령도 없다.
- 새 모듈 추가로 전체 source identity는 변경된다. 과거 승인이나 paid report를 새 소스에서
  다시 승인하거나 자동 변환하지 않는다. 과거 기록 검증은 기존 동결 소스를 사용한다.
- prepare 출력은 authorized=false, live_capable=false다. 실제 승인 파일 형식이 아니다.
- 원본 DB, 실제 raw, F1 진단 도구 및 기존 검토 판정은 건드리지 않는다.
- 프로젝트 외부 코드를 복사하지 않으며 새 런타임 의존성도 없다.

## 응답 파일과 평가

packets.json에는 version, corpus_digest, evidence_kind, records를 둔다.
각 record는 request_id, body_digest, http_status, raw_response, elapsed_ms를 정확히 가진다.
raw_response는 Responses JSON 원문 문자열 또는 null이다. elapsed_ms는 호출 시작부터
응답 완료까지의 비음수 정수 밀리초 또는 null이다. poll 시각으로 대체하지 않는다.
현재 importer는 이 값들의 진실성, 실제 호출 여부, 승인된 실행 여부를 인증하지 않는다.

evidence_kind는 synthetic 또는 imported-unverified만 허용한다.
실제 기록을 가져와도 이 도구만으로 live 감사 통과라고 표시하지 않는다.
동일 response id를 여러 슬롯에 재사용하거나 request/body 결합이 틀리면 거부한다.
corpus는 현재 source와 fixture에서 재생성해 전체 비교하므로 수정 후 digest만 다시 계산한
관측이나 프롬프트도 거부한다.

성공 응답은 기존 strict life_candidate를 거쳐 새 engine branch에서 행동 하나만 실행한다.
다른 인물은 계속 RULE로 행동하며 완료 시점이나 tick 24 중 이른 시각까지 진행한다.
SUCCEEDED, FAILED, REJECTED, INVALID_CANDIDATE, PENDING_AT_HORIZON을 구분한다.
같은 사례의 동일 후보는 결정적 엔진 결과를 평가 호출 내부에서만 재사용한다.
이 캐시는 LLM 응답 재사용이나 새 반복 생성이 아니다.

INCOMPLETE, refusal, HTTP 오류, 잘못된 usage와 identity는 행동을 접수하지 않는다.
응답 미수신은 TRANSPORT_UNCERTAIN, 기록 미제공은 MISSING으로 구분한다.
늦게 완료한 completed 응답은 TIMEOUT으로 처리하고 usage는 유지한다.
완료 시각을 모르면 null로 보존하며 지연 기준 통과를 인증하지 않는다.

## 지표의 의미

- 예정 슬롯 수와 기록 수를 함께 표시한다. 실패나 누락을 분모에서 빼지 않는다.
- 유효한 usage는 실패 응답도 합산한다. reasoning_tokens는 output_tokens의 부분집합이며
  두 번 더하지 않는다. cached input을 별도로 반영한다.
- 단가는 입력 750, 캐시 입력 75, 출력 4500 nano-USD/token으로 고정한다.
  이는 이전 실행과 같은 단가에 따른 계산이며 청구서 검증은 아니다.
- usage가 없는 슬롯은 total_cost 및 비용/완료 행동을 null로 둔다.
  known_cost는 확인 가능한 부분합일 뿐 전체 비용으로 해석하지 않는다.
- 지연 시간은 값이 있는 기록 수와 평균을 함께 표시한다.
- 엔진 완료는 행동의 실행 가능성이다. 최적 행동이나 생활 유지 성공이 아니다.
  WAIT만 반복한 후보도 engine SUCCEEDED가 될 수 있다.
- behavioral_success_rate와 24h_completion_rate는 null이다.
  token_saving_effect_established와 authorized_live_execution_verified는 false다.
- 모의 usage와 지연은 처리 검증용 상수이며 실제 사용량 예상치가 아니다.

## 실행 방법

프로젝트 Python 3.12 환경과 src import 경로를 사용한다. 아래 출력 디렉터리가 이미 있으면
다른 새 디렉터리를 선택한다. 기존 산출물을 덮어쓰는 옵션은 없다.

```powershell
Set-Location 'C:\Users\<user>\Documents\JourneyMap\.worktrees\phase1'
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m journeymap.examples.life_reasoning_probe prepare --output artifacts/reasoning-probe/prepared-v1
python -m journeymap.examples.life_reasoning_probe mock --output artifacts/reasoning-probe/mock-v1
python -m journeymap.examples.life_reasoning_probe audit artifacts/reasoning-probe/mock-v1
```

위 python은 기존 프로젝트의 Python 3.12 실행 파일을 뜻한다. 새 가상환경 생성은 필수가 아니다.
현재 Windows 호스트에서 위 명령을 직접 실행한 것은 아니다.

외부 기록을 data-only 평가하려면 다음 명령을 사용한다. 네트워크 실행은 없다.

```text
python -m journeymap.examples.life_reasoning_probe evaluate --corpus <corpus.json> --packets <packets.json> --output <new-directory>
```

## 다음 단계

1. 데스크톱 기존 phase1에 변경을 반영하고 관련 오프라인 검증을 수행한다.
2. 실제 요청 원문이 확보되면 두 과거 run의 입력 차이를 대조한다.
3. 별도 유료 비교를 준비할 때 실행 계획, 모든 조건의 총 호출/비용, 사용량 누락 처리,
   deadline, journal, 단일 소비 승인 결합을 먼저 구현하고 검토한다.
4. 짧은 판단 비교에서 남은 후보를 장기 생활 실행으로 평가한다. 이번 도구는 그 실행기가 아니다.
5. 계획 재사용과 난도별 routing은 그 결과에 따라 후속 실험으로 결정한다.

개발용 권고: Sol / High. 실험 모델 후보: 기존 gpt-5.4-mini snapshot의 High 대 Medium.
이번 오프라인 구현 승인은 유료 실행 승인으로 확대하지 않는다.
