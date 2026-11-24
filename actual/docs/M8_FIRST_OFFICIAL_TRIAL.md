# M8 first official live trial — 2026-09-12

`m8-first-official-sol` 한 번을 사용자 승인 범위에서 실행했다. **COMPLETED / HORIZON**, tick **0 → 24**, wall **38.063초**. 추가 유료 trial, retry/repair/fallback 추가, protocol 수정, commit/push는 하지 않았다.

## 실행 identity와 경계

- OpenAI Responses API, requested/returned model 모두 `gpt-5.6-sol`; 더 구체적인 snapshot identity는 응답에 없었다.
- reasoning `medium`, max_output_tokens `4096`, max_decisions/provider_calls `24/24`, wall timeout `240초`, provider socket timeout `20초`.
- prompt `alderwick-decision-2`, strict `json_schema` `decision-candidate-2`, protocol `alderwick-24h-2`, scenario `social-resources-24h-1`, seed `42`, NoMemory.
- 실행 소스 SHA-256: `1956884605da892413274879be11e90955ee5246f4dc7da5ca32c7bf4e4c5a84`; HEAD `7c94a3c4ff7e55db0a6c40b4f4f8181edf68d664`, working tree dirty.
- UTC 시작 `2026-09-12T05:55:37.100724+00:00`, 종료 `2026-09-12T05:56:15.088541+00:00`.
- 전송 body는 현재 Stranger의 actor-scoped Observation을 담은 versioned prompt, schema, 모델 설정 및 `store=false`였다. World Truth/Research/Debug 전체, future schedule, RNG, 다른 actor의 private 자원은 body에 넣지 않았다. API key는 HTTPS 인증 header에만 사용했다.
- raw output → parsed wire candidate → decoded DecisionCandidate → trusted Controller가 authority envelope를 구성한 ActionRequest를 별도 보존했다. 기존 M7 validation을 통과한 요청만 submit했다.
- REQUEST의 arbitrary object는 `request_payload_json` string codec을 사용하는 기존 schema 그대로다. API가 전체 strict schema를 수락했지만 이번 모델은 REQUEST를 선택하지 않았으므로 이 branch의 실제 생성은 이번 live run에서 검증되지 않았다.

## 호출, 사용량, 비용

Provider **10회**, 응답 10개 모두 `completed`, response ID 모두 다름. invalid output **0**, provider failure **0**, parser failure **0**, refusal/incomplete **0**, raw output 누락 **0**. 자동 retry/repair 없음.

| 항목 | 실제 usage 합계 |
|---|---:|
| input tokens | 16,052 |
| input 중 cache write | 16,022 |
| input 중 cached read | 0 |
| output tokens | 1,204 |
| output 중 reasoning tokens | 843 |
| output 중 나머지 | 361 |
| total tokens | 17,256 |

reasoning은 output에 포함되며 중복 합산하지 않는다. [공식 Sol 가격](https://developers.openai.com/api/docs/models/gpt-5.6-sol)의 input $4/1M, cache write 1.25배($5/1M), cached input $0.40/1M, output $20/1M을 적용하면 `(30×4 + 16,022×5 + 0×0.40 + 1,204×20)/1,000,000` = **$0.104310**이다. 이는 반환 usage와 공개 단가로 계산한 비용이며 billing invoice 실측값은 아니다. export의 원래 `metrics.cost=null`은 수정하지 않았다.

## Decision별 action과 결과

각 행 사이에는 기존 protocol의 NPC activation이 들어간다. tick 간격은 의도한 action duration이다.

| # | tick | action / payload | 결과 |
|---:|---|---|---|
| 1 | 0 → 2 | MOVE west-gate → village-square | SUCCEEDED |
| 2 | 3 → 5 | MOVE village-square → bakery | SUCCEEDED |
| 3 | 5 → 6 | BUY edwin-bread, quantity=2 | SUCCEEDED |
| 4 | 8 → 9 | CONSUME bread, quantity=1 | SUCCEEDED |
| 5 | 10 → 12 | MOVE bakery → village-square | SUCCEEDED |
| 6 | 13 → 14 | ASK hugh, east-bridge, predicate=bridge_status | SUCCEEDED; 답변 없음 |
| 7 | 15 → 17 | MOVE village-square → east-road | SUCCEEDED |
| 8 | 18 → 19 | CONSUME bread, quantity=1 | SUCCEEDED |
| 9 | 20 → 22 | MOVE east-road → village-square | SUCCEEDED |
| 10 | 23 → 24 | ASK hugh, east-bridge, predicate=condition | SUCCEEDED; horizon에서 종료 |

LLM action 성공/거부/실패 **10/0/0**. NPC 포함 전체 19개는 **18/1/0**. 유일한 거부는 tick 5 Thomas의 ASK에 대한 `OUT_OF_RANGE`다. 전체 action types는 MOVE 6, ASK 4, BUY 1, CONSUME 2, INFORM 1, WAIT 5다. ScheduledEvent는 이 action 수에 포함하지 않는다. SurvivalTick 1–24와 bridge collapse tick 3, 총 25개 system outcomes를 기록했다.

## 최종 상태, Knowledge, Observation

Stranger 최종 **hunger 48 / fatigue 34 / wallet 6 / inventory {bread: 0} / location village-square**.

Bridge는 tick 3에 기존 schedule대로 붕괴했다. Hugh는 tick 3 직접 관찰, Thomas는 tick 13 Hugh의 INFORM으로 collapsed를 전달받았으며 기존 intact 기록도 보존했다. Stranger는 tick 13 `bridge_status`를 질문했지만 Hugh의 `condition` 지식과 predicate가 다르고 답변 Event도 없었다. ASK의 SUCCEEDED는 질문 제출 성공이며 정보 획득 성공을 뜻하지 않는다. Stranger는 tick 17 east-road 도착 시 `condition=collapsed`를 직접 관찰했다. 이후 east-bridge 진입 action은 없고 tick 22 마을로 돌아왔다. tick 23에는 `condition`을 질문했으나 tick 24 horizon으로 다음 NPC 답변 기회 없이 끝났다. 이 행동의 동기나 모델 내부 판단은 raw JSON만으로 단정하지 않는다.

전체 Knowledge **6 → 9**: INITIAL 6, DIRECT_OBSERVATION 2, INFORMED 1. Stranger의 최종 기록은 초기 travel 3개와 bridge 직접 관찰 1개다.

Observation은 총 19개, max **3,599 bytes**, mean **2,952.736842 bytes**. 실제 LLM에 전달한 Stranger Observation 10개는 max **3,599**, mean **3,080.8 bytes**. 이는 canonical Observation content bytes이며 HTTP payload/token 길이와 다르다. 65,536-byte 상한 overflow **0**, 관찰 절단 없음. full history는 이 실행의 활동량에서 상한 내에 머물렀으며 장기 실행에 대한 상한 증명은 아니다.

## Replay와 fake 비교

최종 state digest:

```text
c8357de86dc6066843a24e047e241b824f565be2d12ba540317e46f639c07733
```

실행 중 ReplayHarness 비교와 export 재읽기, 별도 `--replay trials/m8-first-official-sol` 검증 모두 **equality=true**. 초기 scenario/seed, 명시적 schedule, 기록된 ActionRequest stream, advance_to만 사용했으며 Provider 호출은 0회다. 파일 SHA-256과 전체 engine report(ActionResults, Events, system outcomes, final state/digest/time, RNG draw count)를 비교했다.

| 지표 | fake m8-fake-24h-v2 | official live |
|---|---:|---:|
| 종료 tick | 24 | 24 |
| Provider calls | 12 | 10 |
| LLM 성공/거부/실패 | 11/1/0 | 10/0/0 |
| 전체 성공/거부/실패 | 20/2/0 | 18/1/0 |
| hunger / fatigue | 58 / 25 | 48 / 34 |
| wallet / bread | 6 / 1 | 6 / 0 |
| 전체 Observation max / mean | 3,875 / 3,058.09 | 3,599 / 2,952.74 |
| Knowledge 수 | 10 | 9 |
| overflow / invalid / provider failure | 0 / 0 / 0 | 0 / 0 / 0 |
| replay equality | true | true |

Live는 빵 2개를 모두 소비하고 REST/REQUEST/INFORM을 선택하지 않았다. fake의 의도된 action coverage와 모델의 자발적 행동은 목적이 다르다. live의 전 action 성공률도 유용한 정보 획득이나 계획 품질을 보장하지 않는다. 이번 한 trial로 통계적 우월성은 판단할 수 없다.

## Export와 exit criteria

`trials/m8-first-official-sol/`의 원본 export 17개를 보존한다:

```text
manifest.json
metrics.json
decisions.jsonl
activations.jsonl
observations.jsonl
observation_attempts.jsonl
action_requests.jsonl
action_traces.jsonl
action_results.jsonl
events.jsonl
system_event_outcomes.jsonl
knowledge.jsonl
initial_knowledge.jsonl
initial_state.json
final_state.json
replay_input.json
engine_report.json
```

보조 console 출력은 `trials/first-official-sol-console.txt`, 이 보고서는 `docs/M8_FIRST_OFFICIAL_TRIAL.md`다. Research/export의 전체 canonical state와 타 actor 기록은 로컬 연구용이며 Provider payload가 아니다.

**M8 exit criteria 충족**: 승인된 실제 LLM 24h 실행, Provider 없는 engine replay, 후속 trial을 비교할 versioned configuration/raw/parsed/action/result/usage/provenance 기록이 생성됐다. 복수 live trial의 통계 비교 자체는 아직 수행하지 않았다. 기존 offline gate **770 tests**, Ruff/format/mypy 및 fake/replay 결과를 보존했으며 이번 실행 중 runtime 코드는 바꾸지 않았다. 후속 유료 trial은 별도 승인 전까지 실행하지 않는다.
