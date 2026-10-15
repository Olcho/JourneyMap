# JourneyMap Phase 3 완료 보고서

완료일: 2026-10-15. Phase 2 독립 검토의 다음 단계 권고에 따라 로컬 관찰 UI와
정확한 LifeSession checkpoint 저장/복원을 구현·검증했다.

## 완료 범위

`C:\Users\user\Documents\JourneyMap\.worktrees\phase1`의 미커밋 Phase 1+2를 그대로 이어갔다.
branch는 `codex/phase1-concurrent-runtime`, HEAD는
`97a5b6a24079b1f3bf3afb45bac85d30c1468336`이며 commit/push/merge하지 않았다.
개발 시작 시 171개 source/config/test/doc 전체를 phase3-start-source.zip으로 보존했다.

LifeSession을 단일 시간 주체로 유지하며 시작/정지/한 시간 단계/0.5·1·4·16배속을 연결했다.
실행 중인 세션임을 LIVE_SESSION으로 표시한다. 연구자 전체 보기에는 위치/이동/현재 행동,
욕구/자원/선택 근거/전역 사건을 표시한다. 인물 보기에는 허용된 perception 미리보기와
실제 마지막 Observation의 ID/tick, 해당 인물의 선택 및 접수 기록만 표시한다.
조회는 Observation/요청 ID나 정책 상태를 소비하지 않는다.
168시간에서는 HORIZON을 명시하고 시작/단계 버튼을 비활성화한다.

체크포인트는 JSON으로 세계/pending/예약 heap과 insertion 순서/RNG/priority cursor,
정책 blocked routes와 retry 상태/Observation history와 ID index/요청 cache/기록 cursor를 저장한다.
factory로 callable과 서비스를 재구성하고 상태를 직접 주입한다. 처음부터 replay하지 않으며
timing을 재준비하거나 needs/closure를 다시 예약하지 않는다. 저장 후 새 process에서도 이어진다.
저장은 정지된 성공 경계에서 atomic replace한다. 복원은 일시 정지 상태로 열린다.

## 검증 결과

환경: Windows, Python 3.12.10, 기존 프로젝트 venv, PYTHONPATH를 개발 작업 트리 src로 고정.

| 검사 | 실제 결과 |
|---|---|
| 전체 offline pytest | **1,208 passed**, 248.02초, 실패/오류/skip 0 |
| checkpoint + life + concurrent + architecture 관련 검사 | **100 passed**, 142.72초 |
| 실행 진입을 추가 차단한 직접 복원 검사 | **1 passed**, 1.25초 |
| Ruff check / format | 통과 / 177 Python 파일 formatted |
| strict mypy | 151 source/test 파일 통과 |
| git diff --check | 통과 |

기존 1,188개 검사를 유지하고 checkpoint/live 검사 20개를 추가했다. 전체 gate 후 직접 복원
검사의 차단 범위를 accept_action/advance_to까지 강화하여 해당 한 검사를 별도 재실행했다.
runtime source는 전체 gate와 후속 검사 사이에 변경하지 않았다.
tests/test_life_checkpoint.py는 socket/DNS를 차단한다. 새 process 테스트의 worker에도 socket
audit hook을 설치했다. 브라우저 검증의 loopback HTTP 트래픽은 별도다.

### 실제 새 프로세스 재개

| 저장 경계 | 진행 중 행동 | 재개 결과 |
|---|---|---|
| 2시간 | MOVE | 168시간 전체 상태/입력/선택/회계 동일 |
| 8시간 | WORK | 168시간 전체 상태/입력/선택/회계 동일 |
| 30시간 | REST | 168시간 전체 상태/입력/선택/회계 동일 |

원 세션을 종료하고 별도 Python process로 복원하여 168시간까지 실행했다.
세 결과 모두 연속 실행과 같은 checkpoint digest
`cc221bc6bbe9d78359c048cb6ef24dd78f0720eb683521f3d53c5aecb1830f90`이며 Event 1,187개,
pending 4개다. checkpoint 전체 비교에는 world 외에도 application/policy/recording 상태가 포함된다.
이전 Event ID의 중복이 없고 inputs/decisions/report도 동일하다.
추가 검사에서 RNG 다음 draw, 폐쇄 학습과 부족 재시도 상태, 완료 전후 동일 요청 재시도와
ID 충돌, 저장 실패의 이전 파일 보존, 손상/버전/형식/호환성 거부를 확인했다.
1×/16×의 가상 40시간 결과와 직접 실행의 전체 checkpoint가 같고 전 인물/전체 화면 조회도
그 digest를 변경하지 않았다. 속도나 화면 상태는 canonical checkpoint에 섞지 않는다.

### Phase 2 회귀 대조

normal/stockout/no-money/no-materials/no-wages/closure 여섯 조건을 다시 168시간 실행했다.
각 조건의 summary, execution, events, results, attempts, inputs, decisions를 기존 Phase 2 파일과
구조로 대조하여 모두 일치했다. summary의 현실 실행 시간 두 필드만 제외했다.
각 실행의 engine replay와 live-input replay도 모두 통과했다.
기존 부족 조건의 정체/위험, 경로 폐쇄 후 우회, 정상 통화256/원료129/빵90도 유지된다.
scripts/verify_phase3.py는 현재 checkout의 기존 Phase 2 baseline artifact를 사용해 이 대조와
별도 process 예제를 재실행하는 보조 도구다. ZIP 추출본에서는 pytest와 CLI를 직접 실행할 수 있다.

### 실제 브라우저 확인

Codex in-app browser에서 로컬 서버의 실제 세션을 조작했다.
단계 진행, 2시간 이동 중 저장→3시간 진행→2시간 복원, 시작→RUNNING→정지→PAUSED,
배속 변경, Ada 관찰 전환, 실제 Observation ID/tick, 168시간 HORIZON과 버튼 차단을 확인했다.
연구자/인물/종료 화면을 JPG로 보존했고 해당 탭의 console error 기록은 0개였다.
시각 검증은 데스크톱 viewport에서 수행했으며 모바일 실제 기기 검증은 주장하지 않는다.
검증용 서버는 작업 종료 시 중지한다. 실행 명령으로 다시 시작할 수 있다.

## 보존과 산출물

기존 보호 대상 870개 파일의 크기/해시를 다시 대조하여 변경·누락 0개다.
Phase 1/2 계약·보고서와 기존 concurrent/life 테스트는 시작 ZIP과 byte-identical이다.
이전 Phase 2 검토 ZIP SHA는
`63c307cf3e5e5ca07e334501f32aff50fbd2b1bfd6e4d92096b9593acbd1dbba`로 유지됐다.
전체 74,942파일을 이번에 재검사했다고 주장하지 않는다.
원본 프로젝트/과거 실험 작업 트리에 쓰지 않았다. 외부 LLM/API 호출과 API key 조회는 없다.

- 실행법/저장 계약: docs/PHASE3_LIVE_CHECKPOINT.md
- 검토 ZIP: artifacts/phase3/JourneyMap_Phase3_review_2026-10-15.zip 및 .zip.sha256
- 검증 기록: artifacts/phase3/full-final.xml, pytest-final.log, targeted-final.xml,
  restore-guard.log, Ruff/mypy/diff logs, protection-summary.json
- 예제/비교: artifacts/phase3/final/verification.json, 여섯 조건의 export,
  checkpoint-move/work/rest.json 및 resumed-move/work/rest.json
- 화면: artifacts/phase3/ui-researcher.jpg, ui-actor.jpg, ui-horizon.jpg

검토 ZIP에는 완전한 최종 source tree, HEAD 대비 tracked patch와 신규 파일,
Phase 3 시작 source ZIP, Phase 3 전용 변경 목록, 검증 기록과 실행 예제를 담는다.
HEAD archive + patch + 신규 source + 명시적 줄바꿈 복원으로 최종 byte 동일성을 확인하고,
MANIFEST.json은 자기 자신을 제외한 모든 payload의 크기/SHA-256을 기록한다.
최종 ZIP SHA는 별도 sidecar에 기록하여 자기 참조 해시를 만들지 않는다.

## 한계

5명/최대 7일의 고정 LifeSession composition용 기능이다. checkpoint는 로컬 신뢰 파일이며
서명 인증이나 모든 재봉인된 경제 의미 변조의 검증기가 아니다. Python source/module version
불일치 시 거부하고 자동 migration하지 않는다. custom subscriber/contributor, handler 중간
저장, indeterminate 요청 복구, 무한 실행은 지원하지 않는다. 기록은 전체 보존하여 메모리와
파일 크기가 증가한다. 연구자 UI는 다중 사용자 권한 서버가 아니다.
건강/사망/원료 조달/고용 확대/사회 관계/LLM/기억 선택은 추가하지 않았다.
정책/모의 검사는 correctness 근거이며 실제 LLM 효과나 장기 경제 안정성의 증거가 아니다.
