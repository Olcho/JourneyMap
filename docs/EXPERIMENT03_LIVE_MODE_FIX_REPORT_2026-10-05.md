# Experiment 03 live 준비 F1 수정 — 2026-10-05 완료 보고

첨부 검토에서 지적한 **응답 없는 mock 실패 기록의 live 재분류**를 수정했습니다.
현재 계획 v2는 실행 mode를 plan digest에 포함하고 runner, journal, export를 모두 대조합니다.
실제 API/key 사용과 commit/push/merge는 하지 않았습니다. 실제 서비스 schema 수용과 행동 효과는
계속 미검증이며 최종 모델·비용 설정·실행 승인도 별도입니다.

## 기준, 재현 및 보존

작업 경로는 `C:\Users\<user>\.codex\worktrees\experiment03-live-preparation\JourneyMap`,
브랜치는 `codex/experiment03-live-preparation`, HEAD는
`557fb3df10149d7b7738137b7ac1038dfba24e84`입니다. 기존 미커밋 live 준비 구현 위에서 수정했습니다.
시작 시 변경 원문 15개는 새 검증 경로의 `before-code/`에 보관했습니다.
원본 checkout의 별도 미커밋 기준선/진단 구현에는 손대지 않았습니다.

수정 전에 새 임시 timeout 기록에서 F1을 직접 재현했습니다. 계획과 외부 expected digest를
그대로 둔 채 사본의 journal/export mode만 live로 변경하고 다시 봉인했을 때 v1은
`INCLUDED / live / infrastructure=1 / behavioral_denominator=0`을 반환했습니다.
원래 임시 기록도 별도로 보존했고 기존 원본 trial은 변조하지 않았습니다.
재현 원본은 `f1-before-original/`, 사본은 `f1-before-reclassified/`, 결과는 `f1-before-gate.json`입니다.

이전 ZIP, 압축 해제 검토 자료, v1 계획/matrix/gate/log/script, original checkout의 이전 보존
대상 등 **358개 파일의 SHA-256이 수정 전후 모두 같았습니다**. 이전 live ZIP의 SHA-256은 계속
`3be0f583f473f833e357f906854327fe5301e1e0b931def878a5c837ada1ab44`입니다.
v1 matrix 42개 파일과 그 당시 INCLUDED gate 및 기존 완료 보고서는 덮어쓰지 않았습니다.

## 코드 수정

| 파일 | 수정 내용 |
| --- | --- |
| `experiments/pickup_cue_live_plan.py` | planned mode 필수 필드와 digest binding, plan/protocol/export/audit v2, 순수 `validate_mode_config` |
| `experiments/pickup_cue_live.py` | runner 선택과 계획 mode 불일치를 파일 생성/transport/key 이전에 거부, 기록은 계획 mode 사용 |
| `experiments/pickup_cue_live_audit.py` | 응답 유무와 무관한 계획/journal/export mode 비교, 동일 순수 live config 검사, v1 읽기 전용 한계 명시 |
| `examples/pickup_cue_live.py` | `plan --mode recording-mock|live`, 기본 recording-mock 유지 |
| `tests/test_pickup_cue_live.py` | 무응답·준비 실패·중단·실제 모델 config의 mode 재분류, mismatch gate, 정상 fake HTTP live 실패/unknown, CLI/v1 회귀 |

코드 경로는 `src/journeymap/` 아래입니다. 위 소스 4개와 테스트 1개를 v1 구현에서 수정했습니다.
기존 pickup Session/evaluator/prompt/wire, 일반 LLMController, M8, Memory Horizon mock-only 경로는
바꾸지 않았습니다. 새 의존성/반복 조건/모델/평가 기준도 추가하지 않았습니다.
현재 HEAD에 대한 변경 원문은 README 및 기존 문서 6개와 신규 파일 10개이며 모두 미커밋입니다.

`make_plan(mode="recording-mock")`는 실제 모델용 config도 사용할 수 있습니다. 모델 ID로 mode를
추정하지 않습니다. `mode="live"` READY에는 공식 support review와 검토 예산을 요구하고 mock 전용
model/returned-model/예산 sentinel을 거부합니다. mode 지정만으로 실행 승인이 생기지는 않습니다.
audit에는 source/runtime 현재값 비교, key 조회, transport/network 접근을 가져오지 않았습니다.

## v1 처리 범위

plan/execution/export/audit identity는 각각 `pickup-cue-execution-plan-2`,
`pickup-cue-live-execution-2`, `pickup-cue-live-export-2`, `pickup-cue-live-audit-2`입니다.
configuration/wire/prompt identity는 동일하여 전체 요청 bytes를 유지합니다.

v1은 새 runner에서 실행하지 않습니다. v2 audit/inspect는 v1의 plan seal과 읽을 수 있는
journal prefix, 선언된 allocation을 보고하되 **EXCLUDED / LEGACY_PLAN_MODE_UNBOUND**로 남깁니다.
`legacy_read_only=true`, `execution_mode_verified=false`, `mode=null`이며 mode 선언값은
`reported_journal_mode`일 뿐입니다. v2 Engine/input 인증이나 행동 분모로 승격하지 않습니다.
이는 원본 v1 기록이 변조됐다는 판정이 아닙니다. 당시 결과는 이전 보고서와 gate/ZIP에 보존합니다.
새 계획과 새 출력 경로를 만들었으며 v1 원본을 v2로 재봉인하지 않았습니다.

## 회귀 및 독립 증거

- 수정 후 targeted live 경계 **74 passed in 19.57s**입니다.
- 전체 **1258 passed in 111.90s**, 기존 1238 + 신규/확장 회귀 20입니다. skip/xfail 없음.
- 8개 독립 F1 기록: mock 전용/실제 모델용 config × timeout/transport/준비 실패/INTENT 중단.
  원래 plan digest를 고정하고 journal/export mode를 다시 봉인한 사본은 모두 **EXCLUDED**입니다.
  원래 정상 infrastructure 기록은 INCLUDED, 중단 prefix는 INCOMPLETE로 남았습니다.
- 정상 live 계획의 fake HTTP timeout/transport 실패 2개는 INCLUDED infrastructure, INTENT 중단
  1개는 INCOMPLETE unknown이며 모두 integrity_exclusions=0입니다. fake key는 파일에 남지 않습니다.
- body/응답/성공/계획/할당의 기존 재봉인 거부, 응답 있는 mode 변조 거부, 파일 생성 전 양방향
  runner mismatch 및 live config 모순 검증도 통과했습니다.
- Provider/key/socket 및 audit의 현재 source/runtime 조회를 차단해도 audit가 동작합니다.

새 40-case matrix는 **준비 제출 200, mock 호출 40, 평가 제출 40, 행동 분모 40, fixture 성공 20**입니다.
40개 모두 v2 audit INCLUDED / recording-mock이며 원본 42개 파일의 hash가 audit 전후 같습니다.
모든 outgoing body/wire가 v1 원본의 동일 allocation과 byte 단위로 같았습니다. 테스트의 A/B
무안내 10쌍 동일/안내 10쌍 notice만의 차이도 유지했습니다. 계획 mode는 모델 입력에 들어가지 않습니다.
실제 외부 API 호출은 0회입니다. 오류 증거 scripts/tests의 추가 mock/fake HTTP는 40-call matrix와 별도입니다.

새 working source SHA-256:
`0c46db27408b416cf5083cd900badc1c9a42aefaacf3ef882d9e845f6f0927ee`.
최종 v2 mock plan SHA-256:
`8443bd37bd7ad947a6467e79d4f1c7a1735fc31458248360b823322a1d9b492e`.
기록 source와 실제 최종 source가 일치했습니다. v1 검토 source digest를 새 결과로 재사용하지 않았습니다.

actual M8 원본 17개도 이번 코드에서 읽기 전용으로 replay/audit했습니다. replay equality=true,
tick 24, digest `c8357de86dc6066843a24e047e241b824f565be2d12ba540317e46f639c07733`이며
strict audit는 기존 **EXCLUDED / NEW_PROVENANCE_REQUIRED**입니다. 17개 hash는 불변입니다.
이전 코드 기준선과 offline 진단 matrix audit는 INCLUDED를 유지합니다.

## 새 산출물과 실행 명령

이번 자료의 root는 `trials/live-preparation-mode-v2-verification/`입니다.

| 경로 | 의미 |
| --- | --- |
| `draft-final.json`, `mock-plan-final.json` | 새 v2 계획 |
| `matrix-final/`, `matrix-final-cli.json` | 새 40-case 실행 원본 및 CLI 결과 |
| `f1-after/`, `f1-regression-gate.json` | 8개 원본/변조 사본과 거부 증거 |
| `fake-http-live/` | 정상 live 계획의 fake HTTP 실패·중단 원본 |
| `offline-gate.json` | source/body/독립 audit/hash 비교 |
| `historical-gate.json`, `preservation-gate.json` | actual M8 및 기존 358개 파일의 전후 hash |
| `pytest-final.log`, `pytest-final.xml` | 전체 결과 및 개별 regression JUnit 기록 |
| `capture_before.py`, `f1_regression_gate.py`, `offline_gate.py`, `offline_cli.py` | 사용한 오프라인 scripts |

Python 3.12.14, pytest 8.4.2, Ruff 0.16.6, mypy 1.20.2를 사용했습니다. 원본 `.venv`의 깨진 launcher를
수정하지 않고 번들 interpreter와 기존 개발 패키지 경로를 사용했습니다. 테스트 child process에만
가짜 key를 넣으며 실제 값은 조회하지 않습니다. 나머지 scripts는 실제 key 조회 자체를 차단합니다.

최종 정적 검사도 모두 통과했습니다. Ruff check 정상, Ruff format 162개 파일 형식 일치,
mypy 140개 source 파일 오류 없음, `git diff --check` exit 0입니다. 각 원본 검사 로그와
`static-gate.json`을 같은 산출물 root에 보존했습니다.

```powershell
Set-Location 'C:\Users\<user>\.codex\worktrees\experiment03-live-preparation\JourneyMap'
$journeyPython = 'C:\Users\<user>\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$env:PYTHONPATH = "$PWD\src;C:\Users\<user>\Documents\JourneyMap\.venv\Lib\site-packages"
& $journeyPython trials/live-preparation-mode-v2-verification/offline_cli.py pytest -q --basetemp trials/live-preparation-mode-v2-verification/pytest-final -o cache_dir=trials/live-preparation-mode-v2-verification/pytest-cache --junitxml=trials/live-preparation-mode-v2-verification/pytest-final.xml
& $journeyPython -m ruff check .
& $journeyPython -m ruff format --check .
& $journeyPython -m mypy
git diff --check
& $journeyPython trials/live-preparation-mode-v2-verification/offline_cli.py journeymap.examples.pickup_cue_live plan --output trials/live-preparation-mode-v2-verification/draft-final.json
& $journeyPython trials/live-preparation-mode-v2-verification/offline_cli.py journeymap.examples.pickup_cue_live plan --mode recording-mock --mock-config --output trials/live-preparation-mode-v2-verification/mock-plan-final.json
& $journeyPython trials/live-preparation-mode-v2-verification/offline_cli.py journeymap.examples.pickup_cue_live mock --plan trials/live-preparation-mode-v2-verification/mock-plan-final.json --output trials/live-preparation-mode-v2-verification/matrix-final
& $journeyPython trials/live-preparation-mode-v2-verification/f1_regression_gate.py
& $journeyPython trials/live-preparation-mode-v2-verification/offline_gate.py
```

이미 존재하는 output에는 덮어쓰지 않으므로 재현에는 새 경로를 사용해 주세요.
계약의 일반 CLI 명령과 향후 승인 후 `plan --mode live` 형식은
[수정 계약](EXPERIMENT03_LIVE_PREPARATION.md)에 있습니다.

새 검토 ZIP에는 변경 원문/HEAD/status/full tracked diff/신규 파일, 수정 계약과 이 보고서,
v2 계획/matrix/gate/최종 로그 및 F1 before/after 원본을 포함합니다. manifest, 실제 압축 해제 비교,
원본·기존 ZIP 불변 검증은 새 ZIP 옆 verification JSON에 기록합니다.

## 남은 사항

이번 F1 수정과 offline 회귀의 미해결 blocker는 없습니다. 최종 모델·예산·실제 실행 승인은 계속
미확정이며 실제 서비스 schema/parameter 수용과 LLM 행동 효과는 무호출 검증으로 확정할 수 없습니다.
모든 artifact와 외부 plan digest까지 일관되게 다시 작성한 위조를 hash만으로 탐지하는 보증은 하지 않습니다.
