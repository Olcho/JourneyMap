# 수령처 안내 연속 행동 구현 완료 보고

2026-10-06. 작업 위치 `C:\Users\<user>\.codex\worktrees\experiment03-live-preparation\JourneyMap`.
HEAD `557fb3df10149d7b7738137b7ac1038dfba24e84`, branch `codex/experiment03-live-preparation`.
첨부 구현 지시문과 저장소 규칙에 따라 구현·오프라인 검증을 완료했습니다.
**이번 작업의 실제 API 호출 0회**, commit/push 0회입니다. 이전 smoke/40회 승인은 복사하지 않았습니다.

## 변경 내용

[구현 계약](EXPERIMENT03_PICKUP_SEQUENTIAL.md)에 새 profile/plan/execution/export/audit/metrics identity를
정의했습니다. 새 Python 소스 7개, 신규 테스트 파일 1개, 계약/보고서 2개와 overview 문서 6개를
추가·갱신했습니다. 기존 미커밋 16개 파일은 초기 원문을 별도 보관했습니다. HEAD 대비 현재 변경
26개(기존 tracked 6개 + untracked 20개)는 검토 ZIP에 전부 원문으로 들어갑니다.

| 신규 구현 | 역할 |
|---|---|
| `adapters/pickup_sequential.py` | 고정 prompt, full-history 정책, current/memory cue, 새 wire, 단회 Provider/transport |
| `experiments/pickup_sequential_plan.py` | mode/source/runtime/config/순서/6회/240회/중단 정책을 묶는 계획 |
| `experiments/pickup_sequential_session.py` | 실제 준비 및 단계별 observe→기억→응답→Engine→closed trace |
| `experiments/pickup_sequential_metrics.py` | 첫/최종/회복 도착, 실제 경로 baseline, 행동 및 tick 비용 |
| `experiments/pickup_sequential.py` | durable 단계 journal, 즉시 종료, 오류 중단, 신규 출력만 허용 |
| `experiments/pickup_sequential_audit.py` | Provider 없는 새 세계 재구성 및 독립 Engine replay |
| `examples/pickup_sequential.py` | 별도 plan/mock/live/audit/inspect CLI |

기존 one-shot 소스·profile·평가·audit·실제 응답을 변경하지 않았습니다. Engine 개편이나 외부 의존성
추가도 없습니다. 실제 prefix 5행동/tick 7/Square와 engine seed 42/allocation seed 6을 유지합니다.
full-prefix의 기존 allocation 위치에 full-history를 대응시켰습니다.

각 기회마다 실제 새 관찰과 조건에 따른 닫힌 경험만 전달합니다. 현재 Well 안내와 기억 속 안내를
분리하고 동일 안내 반복은 허용합니다. 모델에 연구 target/조건/d/단계 번호/개인 목표 저장소를
추가하지 않습니다. 유효한 오답/WAIT/정상 Engine 거부는 다음 기회로 진행하지만 응답 실패는
조합 종료, infrastructure/기록/무결성 오류는 전체 중단입니다. retry/fallback/resume는 없습니다.

첫 점수는 후속 회복으로 수정하지 않습니다. 최종 unknown에는 성공 비용을 부여하지 않고 검증된
첫 점수·소모 비용을 보존합니다. audit는 journal chain 손상 전의 검증된 prefix도 재구성하며
현재 영향을 받는 증거와 이전에 검증된 조합을 구분합니다.

## 최종 검증

검증 원본은 `trials/pickup-sequential-verification-20261006/`입니다. **납품 코드와 일치하는 최종
원본은 `final/` 아래**입니다. 최상위의 이전 mock 원본/gate/`pytest-final`은 개발 중 기록이며
덮어쓰지 않았습니다. 최종 전체 테스트는 `pytest-verified.log`/XML입니다.

- Python 3.12.14; pytest **1,296 passed in 123.91s**, errors/failures/skips 0.
- 신규 sequential 테스트 38개: 즉시 종료, Well 및 오답 수령처 회복, WAIT, Engine 거부,
  refusal/incomplete/invalid output, 메모리 순서와 cue 가시성/반복/충돌, cap, 통신/기록 오류,
  중단 prefix, 재봉인 변조, chain break, Provider 없는 audit, fake HTTP wire 검사.
- Ruff check 성공, format **171 files already formatted**, strict mypy(src+tests) **148 files** 성공,
  `git diff --check HEAD` 성공. 원본 로그와 `static-gate.json`을 포함합니다.
- 테스트 wrapper는 실제 socket 연결을 차단합니다. 기존 HTTP unit test는 child에 fake key만
  설치합니다. 새 offline gate/audit에는 실제 key 조회와 HTTPTransport 생성도 차단합니다.
- `final/matrix-final/`의 42개 원본, 단계별 실제 mock 요청/응답, audit 및 inspect 결과를 보관합니다.
  새 source identity는 `a3f9fe5ac39e87be701ecb578ec342516fd35b41bcf57c535758e55e787c4ec0`입니다.

40개 mock 조합의 plan/attempted/검증된 첫 결과/검증된 최종 결과 분모는 모두 40입니다.
결정 시도 104회, 응답 실패 0, infrastructure 오류 0, 미시도 0, 한도 소진 8개입니다.

| 기억 조건 | 첫 cue 포함 | 첫 성공 | 최종 성공 | 회복 성공 | 결정 시도 |
|---|---:|---:|---:|---:|---:|
| no-event-memory | 0/8 | 0/8 | 0/8 | 0/8 | 48 |
| recency-k1 | 2/8 | 2/8 | 8/8 | 6/8 | 20 |
| recency-k2 | 4/8 | 4/8 | 8/8 | 4/8 | 16 |
| recency-k3 | 6/8 | 6/8 | 8/8 | 2/8 | 12 |
| full-history | 8/8 | 8/8 | 8/8 | 0/8 | 8 |
| 합계 | 20/40 | 20/40 | 32/40 | 12/40 | 104 |

이 표는 정해진 visible-cue mock 규칙의 correctness 증거이며 실제 모델 행동, 통계적 효과 또는
construct validity 증거가 아닙니다. no-event-memory는 Well에서 Square로 돌아오면 안내를 잃습니다.
이 구조를 일반 추론 결함으로 해석하지 않습니다. 최종 도착만으로 안내 활용을 단정하지 않습니다.

별도 회복 fixture는 Well→Square→정답의 3행동/6tick이며 직접 도착 baseline 1행동/2tick 대비
extra_actions=2/extra_ticks=4입니다. 첫 실패=false와 최종 성공=true를 분리합니다. WAIT 6회 실패는
소모 6시도/6tick이고 성공까지 비용은 null입니다. 통신/기록 중단은 첫 실패를 보존하고 최종 unknown,
나머지 조합은 미시도로 남습니다. summary/cost 등을 다시 봉인한 변조도 독립 audit가 EXCLUDED합니다.

`final/historical-gate.json`에서 기존 실제 40회와 smoke의 audit JSON이 과거 보고와 동일함을 확인했습니다.
v1 legacy는 `LEGACY_PLAN_MODE_UNBOUND` 배제를 유지합니다. M8 strict audit는
`NEW_PROVENANCE_REQUIRED` 배제를 유지하며 replay tick=24,
digest=`c8357de86dc6066843a24e047e241b824f565be2d12ba540317e46f639c07733`입니다.
기존 baseline/offline matrix는 INCLUDED입니다. 기존 raw/ZIP/소스 **2,005개 파일**의 SHA-256이
시작 전과 일치합니다. 실제 40회 결과와 이번 mock 결과를 합산하지 않았습니다.

## 다음 실행 검토 자료와 남은 사항

`final/draft-live-plan.json`은 **DRAFT**입니다. digest:
`cefbeb7fe0cfcfa371f19ae89202284a9877ad5669b35f26485951141c633a47`.
`final/candidate-config.json`은 gpt-6.1-sol/low, output 2048, timeout 120,
response bytes 2000000, exact returned_models `[gpt-6.1-sol]`을 명시합니다.
40개 순서와 6회/240회 계획 cap을 저장했습니다. 실제 body는 행동에 따라 각 단계 호출 직전에
기록되므로 240개 실제 body를 사전 확정했다고 주장하지 않습니다.

`final/cost-planning.json`은 2026-10-06 확인한 공개 Standard 단가와 입력 증가를 반영한 민감도입니다.
입력/출력은 $2/$10 per million tokens이고, 출력 2048 × 최대 240회 가정에서 입력 10k/25k/50k
가정은 $9.7152/$16.9152/$28.9152입니다. 모든 입력에 cache write $2.50을 적용한 보수적 시나리오는
$10.9152/$19.9152/$34.9152입니다. [공식 모델/요금 근거](https://developers.openai.com/api/docs/models/gpt-6.1-sol).

mock 표본 최대 요청 13,041 bytes, prompt 10,930 characters는 토큰 수가 아니며 미래 경로 전부의
상한도 아닙니다. 마지막 기회에 닫힌 경험 최대 10개를 고려했습니다. 토큰 실측/Token Count API는
실행하지 않았습니다. 50k는 보수적 계획 가정이며 확정 입력 상한 또는 청구 hard limit이 아닙니다.
이전 실제 비용×6을 최대 비용으로 쓰지 않았습니다.

구현·오프라인 검증의 미해결 차단 요인은 없습니다. **새 실제 실행의 사용자 승인과 예산은 미확정**이며
현재 DRAFT는 live 실행을 거부합니다. 다음 검토 후 새 config/READY/digest를 새 경로에서 만들어야 합니다.
명령 예시는 구현 계약에 있습니다. 기존 승인과 예산을 재사용할 수 없습니다.

## 검토 묶음

별도 `trials/review-bundles/experiment03-pickup-sequential-<UTC>/`에 새 ZIP을 생성합니다.
HEAD/status/전체 tracked diff, 신규 파일 본문을 포함한 변경 원문, 전체 src/tests/docs,
구현 지시문 사본, 계약/보고서, 최종 및 개발 중 mock 원본, DRAFT/비용, scripts/gates/최종 로그,
시작 전 변경 원문을 포함합니다. 파일별 상대 경로·크기·SHA-256은 `MANIFEST.json`에 있으며
manifest 자신의 hash와 ZIP hash는 외부 `.verification.json`/`.zip.sha256`에 있습니다.
ZIP CRC, 안전한 상대 경로, 압축 해제 byte/hash 및 원본 일치, 포장 전후 소스/기존 자료/HEAD/status/diff
불변을 검사합니다. API key/인증 헤더/.env/.venv/.git/환경 덤프는 포함하지 않습니다.
