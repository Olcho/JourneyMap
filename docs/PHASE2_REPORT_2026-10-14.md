# JourneyMap Phase 2 완료 보고서 — 2026-10-14

## 실제 7일 생활과 자원 흐름

1tick=1시간, seed42, 5명/168시간의 정책 실행이다. 사용자가 시각별 행동을 지정하지 않았다.
정상 실행에서 노동자 전원이 반복 노동·급여·구매·소비·휴식을 수행했고, 욕구 위험 구간은 모두 0시간이었다.
고용주도 자기 욕구를 관찰해 소비17회/휴식13회를 선택했다. 생산과 임금은 노동자가 시작한 WORK의
원자적 완료 규칙이며 고용주의 가짜 행동이나 자동 보조금이 아니다.

| 인물 | 완료 노동 | 받은 임금 | 구매 횟수 | 소비 횟수 | 휴식 횟수 | 최종 지갑/bread | 최종 hunger/fatigue |
|---|---:|---:|---:|---:|---:|---|---|
| 고용주 | 0 | 0 | 0 | 17 | 13 | 226 / 74 | 16 / 22 |
| Ada | 8 | 80 | 8 | 17 | 10 | 10 / 4 | 26 / 54 |
| Bert | 8 | 80 | 8 | 17 | 10 | 10 / 4 | 26 / 54 |
| Cora | 8 | 80 | 8 | 17 | 15 | 10 / 4 | 26 / 30 |
| Dara | 7 | 70 | 8 | 17 | 15 | 0 / 4 | 26 / 26 |

노동7~8회 분포에서 한 인물의 독점은 관측하지 않았다. Dara의 추가 WORK는 167→170으로
진행 중이다. 최종 tick168에는 이 작업과 Bert REST(167→173), Cora REST(167→171),
고용주 REST(167→171)의 **4개 pending**을 보존했다. horizon 밖까지 진행하거나 미리 급여를
지급하지 않았다. 정상 실행의 committed Event는 **1,187개**, 기본 시뮬레이션 경과는 **2.300초**,
engine/live replay 검증을 포함한 경과는 **5.825초**였다. 이는 이 실행의 측정이며 수용 인구 약속이 아니다.

회계는 다음과 같다. 외부 유입/폐기/자동 재고 보충은 0이다.

- 통화: **256 → 256**. 임금 이전 합계310, 구매 대금 이전296이다.
- 원료: **160 − 31 = 129**. flour는 원료 배치 단위다.
- 식량: **8 + 248 − 166 = 90**. 고용주74 + 노동자 각4로 합계90이다.
- 생산31회는 매번 flour 1배치→bread 8개이고, 품목 개수 합을 보존량으로 해석하지 않는다.

Ada의 실제 두 생활 구간에서 선택한 일부 원문 행은 다음과 같다. 전체 중간 행동/효과는
normal/summary.json의 resource_flows/example_life_cycles와 events/results/decisions JSONL에 있다.

| 구간 | tick | 실제 완료 | Ada 지갑/bread | hunger/fatigue |
|---|---:|---|---|---|
| 1 | 7 | 노동·생산·임금10 | 10 / 2 | 34 / 21 |
| 1 | 9 | bread5 구매, 대금10 | 0 / 5 | 18 / 23 |
| 1 | 16 | bread2 소비 | 0 / 3 | 12 / 30 |
| 1 | 28 | 휴식4시간 완료 | 10 / 3 | 36 / 34 |
| 2 | 46 | 노동·생산·임금10 | 10 / 3 | 22 / 44 |
| 2 | 60 | bread5 구매, 대금10 | 0 / 6 | 30 / 40 |
| 2 | 61 | bread2 소비 | 0 / 4 | 12 / 41 |
| 2 | 78 | 휴식6시간 완료 | 10 / 2 | 26 / 44 |

구간1에는 tick8 소비와 tick21 추가 노동, 구간2에는 tick55 휴식/tick56 소비/tick68 추가 노동/
tick71 소비도 실제로 있었다. 위 표를 고정 행동 배열이나 중간 행동이 없는 단일 turn으로 해석하지 않는다.
하루별 5명 상태·성공/실패 행동 집계도 summary.json 및 사람이 읽는 로그에 제공한다.

## 기준선과 구현 범위

작업 경로는 `C:\Users\user\Documents\JourneyMap\.worktrees\phase1`, branch는
`codex/phase1-concurrent-runtime`, 시작/종료 HEAD는
`97a5b6a24079b1f3bf3afb45bac85d30c1468336`이다. branch 이동이나 새 worktree 생성 없이
Phase 1 미커밋 23개 변경(기존14/신규9)을 이어 사용했다. HEAD만으로 시작하지 않았다.

수정 전에 tracked/untracked를 포함한 소스·테스트·설정·문서 **155개**를
artifacts/phase2/phase2-start-source.zip과 phase2-start-files.json에 보존했다.
tracked patch/status도 별도로 보존했다. Phase 1 계약·보고서의 바이트는 시작 ZIP과 같고
기존 검증 출력 경로를 덮어쓰지 않았다. Python3.12.10의 기존 .venv launcher가 정상 동작했고
실제 import가 현재 worktree/src를 가리킴을 확인했다. 환경 재설치는 하지 않았다.

새 LaborModule은 WORK의 고용·자리·장소·욕구·원료·임금 검증을 소유한다. 기존 inventory/trade/
survival helper로 변환·급여·피로를 하나의 TransitionPlan에 넣고 기존 canonical _commit_plan을
사용한다. 원료·임금은 완료 경쟁이며 시작 시 예약하지 않는다. 자리는 진행 중 행동으로 제한한다.
실패 노동은 3시간을 소비하고 별도 보상은 없으며 일부 효과만 적용하지 않는다.

survival 모듈의 유한 needs_schedule과 기존 SurvivalTick handler로 1..168을 갱신한다.
이동·노동·휴식 중에도 욕구가 흐르며 Controller/observe 호출 횟수와 독립이다. REST는 새 opt-in
거처 제약으로 기존 handler를 감쌌고 기존 동기 REST 의미는 유지한다. Core에는 generic
active_actions만 추가했으며 노동/돈/음식/특정 scenario import를 추가하지 않았다.

생활 정책은 자기 상태, actor-owned INITIAL 지도 KnowledgeRecord, 현장 route/job/offer,
자기 공개 결과를 사용한다. 판단 reason/evidence와 자원 흐름을 기록한다. 원격 재고/지갑/폐쇄,
Research/scheduler/RNG를 정책에 제공하지 않는다. 정확한 계약·실패·저장 필드는
[PHASE2_LIFE_LOOP.md](PHASE2_LIFE_LOOP.md)에 있다.

초기 개발 설정(임금6/출력4/구매최대3)은 7일에 부족했으며 배고픔 상한을 관측했다.
그 실행을 정상으로 집계하지 않고 임금10/출력8/구매최대5로 조정했다. 초기 원료160/bread8/
화폐256은 유지했다. 초기 실패 summary도 검토 ZIP에 포함한다. 이는 개발 당시 진단이며
최종 소스로 그 prototype의 bit-identical replay를 주장하지 않는다. 최종 검증은 별도 6조건이다.
숨은 보조금/구조 규칙은 없다.

## 부족·폐쇄·경쟁 결과

모든 조건은 168시간에서 종료했고 통화/원료/식량 보존과 engine/live-input replay가 통과했다.

| 조건 | 실제 결과 |
|---|---|
| stockout: 초기 bread0 | 각 노동자가 품절1회 관측 → 대기 지연/노동으로 생산 → 구매/소비 재개. 생산256/소비168/남은88, 위험0 |
| no-money: 노동자 지갑0 | 노동으로 먼저 임금 획득, 모두 노동8회/구매7회/소비18회. 통화240 유지, 위험0 |
| no-materials: flour0 | 생산0, 초기bread8만 소비하고 재고0. 노동자 모두 위험123시간, 고용주133시간. 대안 없는 정체를 해결했다고 쓰지 않음 |
| no-wages: 고용주 지갑0 | 총통화16 유지. 완료 노동 Ada/Bert/Cora/Dara=0/4/0/1, 노동자 위험123/48/123/98시간. 재원 경쟁과 편중·정체를 보존 |
| closure: tick2 square→shop 폐쇄 | 노동자4명의 이동이 tick3 실패한 뒤 현장/공개 결과로 우회. 노동8/6/6/8회, Bert/Cora 위험 각4시간(최대hunger96), 다른 두 노동자0. 모든 위험을 정상으로 묶지 않음 |

제어된 반례에서는 A의 폐쇄 route와 B의 다른 route를 함께 이동시켜 B만 정상 도착하고 다음
구매를 진행함을 확인했다. 실제 자율 폐쇄 실행과 원격 숨김 변경의 입력/정책 동일성 검사도 별도로 있다.
작업 중/완료 tick에 원료·재원·계약·위치·욕구를 바꾼 12개 반례, 마지막 원료/임금 경쟁,
자리2개 경쟁, 중복 요청에서도 부분 변환·이중 지급·실패자 차감이 없었다.

고용주 private 자금/원료 진단이 기존 BUY용 공개 이유로 통과할 수 있는 경로를 발견해
LaborModule에서 EMPLOYER_ diagnostic으로 구분했다. Research 원인을 보존하며 노동자에게는
ACTION_REJECTED/ACTION_FAILED만 전달한다. 기존 BUY의 자기 자금 부족 이유는 바꾸지 않았다.

## Phase 1 집중 확인과 검증

Phase 1은 effect-free denial/retry를 attempts에 보존했지만 기존 engine replay만으로 그 기록의
동등성을 검증하지 않았다. 기존 executable stream의 제외 정책을 유지하고 별도 live-input replay를
추가했다. 미등록/중복/타 actor conflict/busy를 포함한 5개 attempt와 Observation/공개 receipt/
최종 상태의 동등성을 확인한다. engine replay는 Controller를 호출하지 않는다.

transit 공간 제외는 기존 query를 그대로 노동/휴식/local job에 적용했고 욕구에는 적용하지 않는다.
별도 독립 엔진, actor별 세계 복제, 유료 Provider, 새 runtime dependency는 추가하지 않았다.

| 검사 | 최종 실제 결과 |
|---|---|
| Phase 1 시작 검사 | 33 passed; phase1-start.xml |
| 신규 Phase 2 + Phase 1 + architecture | 80 passed, 19.19초; targeted-final.xml/pytest-targeted.log |
| 전체 offline pytest | 1,188 passed, 136.88초; 기존1,150 + 신규38, 실패/오류/skip0; full-final.xml/pytest-final.log |
| Ruff check/format | 통과, 168 files formatted; ruff-check.log/ruff-format.log |
| strict mypy | 146 source files, 오류0; mypy.log |
| diff/새 파일 whitespace | 통과; diff-check.log 및 changed-files inventory |
| 동일 seed/config의 정책 재실행 | 정상 7일 판단/환경/상태와 73→168 분할 실행 동일 |
| engine/live-input replay | 정상·부족·폐쇄 6조건 모두 동일, effect-free attempt도 별도 검사 |
| 기존 Phase 1 예제 | 시작/종료 JSON 동등, 계약·보고서 불변; phase1-preservation.json |
| 기존 동기 자원 예제 | tick10/replay equality 유지; legacy-resources.log |
| 검토 ZIP | HEAD archive + tracked patch + 모든 신규 소스로 재구성, 최종 source tree 바이트 동등; reconstruction.json |

개발 중 private 이유 변환 누락과 회계 검사의 고용주 잔액 참조 오류를 찾아 수정했다.
고용주는 다른 사람의 후속 작업/구매로도 잔액이 바뀌므로 최종 flow의 employer 잔액을 대조한다.
최종 검사에서는 이 실패들이 남지 않았다. 영속 save/restore, UI/LLM 행동 효과, Linux 별도 실행,
기존 E03/E04 live 작업 트리의 전체1,607개 재실행은 이번 검사에 포함하지 않았다.

## 변경·보호·검토 ZIP

신규 구현은 labor 모듈, survival/schedule.py, adapters/life.py, scenarios/life.py,
bootstrap_life.py, experiments/life.py, examples/life.py, application/concurrent_replay.py,
tests/test_life.py와 scripts/package_phase2_review.py다. 기존 변경은 inventory 변환 helper,
generic active_actions, concurrent 공개 결과 query/intent cache, replay의 선택적 frame capture와
README/API/architecture/ERD/roadmap/test strategy/ignore 문서다.

검토 ZIP의 PHASE2_CHANGES.json은 시작155개 상태 대비 추가/변경 before/after 해시를 구별한다.
HEAD_TO_FINAL.patch는 Phase 1+2 tracked diff이고, source/는 Phase 1의 신규9개까지 포함한
최종 소스/테스트/설정/문서 **171개**다. Phase 2 시작 대비 변경은 **27개**(기존11/신규16),
HEAD 대비 누적 변경은 **40개**다. MANIFEST.json은 자기 자신을 제외한 payload의 크기/SHA-256이다.
LF/CRLF 복원 정책을 적용해 비교했으며 이번 재구성에는 추가 줄바꿈 변환이 필요 없었다.
재구성 확인을 위해 전체 테스트를 또 실행하지 않았다. Git 내부/가상환경/캐시/키/개인 자료/
E03/E04 raw 원본은 ZIP에 없다.

이번 쓰기는 현재 개발 worktree 안에 한정했다. Phase 1 보호 manifest를 이용해 원래 저장소 및
experiment03-live-preparation의 관련 소스/문서/검사369개와 선택된 실제 실행 증거501개,
총 **870개 기존 경로**를 시작/종료에 대조했고 변경·누락0이다. **74,942개 전체를 다시 검증한 것은 아니다.**
Phase 1 보고서·시간 계약도 시작 사본과 바이트 동일하다. 실제 LLM/API 호출·키 조회·유료 호출·
commit/push/merge는 모두0이다. 실험 코드/원본을 이동하거나 가져오지 않았다.

산출물 위치는 다음과 같다.

- 완료 보고서: docs/PHASE2_REPORT_2026-10-14.md
- 계약: docs/PHASE2_LIFE_LOOP.md
- 실행/검사 증거: artifacts/phase2/final 및 artifacts/phase2의 JUnit/로그/변경 inventory
- 소스 검토 ZIP: artifacts/phase2/JourneyMap_Phase2_review_2026-10-14.zip
- ZIP 외부 SHA-256: 같은 파일명에 .sha256 추가
- 재구성 결과: artifacts/phase2/reconstruction.json

## 실행과 다음 단계

```powershell
Set-Location 'C:\Users\user\Documents\JourneyMap\.worktrees\phase1'
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
$pythonExe = 'C:\Users\user\Documents\JourneyMap\.venv\Scripts\python.exe'
& $pythonExe -m journeymap.examples.life --verify-replay
& $pythonExe -m journeymap.examples.life --case stockout
& $pythonExe -m journeymap.examples.life --case closure
& $pythonExe -m pytest tests/test_life.py tests/test_concurrent.py -q --basetemp=artifacts/phase2/user-tests
& $pythonExe -m ruff check .
& $pythonExe -m ruff format --check .
& $pythonExe -m mypy
git diff --check
```

다음 읽기 전용 관찰 UI 및 저장 설계 단계로 넘어갈 수 있다. 기존 JSONL/인물별 하루 집계/실제
자원 흐름이 관찰 UI의 입력이다. 영속 저장·복원은 미구현이며 Job/timing/자리, 다음 욕구 갱신,
Knowledge/공개 결과 cache/정책 retry·경로 상태 등 추가 checkpoint schema를 계약에 명시했다.
원료 조달·고용 확대·구제 제도·건강/사망·영업시간·사회 관계·LLM/기억 선택은 없다. 조건부 부족의
기아/편중을 해결한 상태가 아니며 7일 실행을 장기 지속 가능성 또는 일반 공정성 증거로 확대하지 않는다.
