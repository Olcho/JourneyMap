# MemoryReplica Responses 데스크톱 통합 결과

2026-11-02 KST. 신규 모듈 통합과 오프라인 검증을 완료했다.
**실제 HTTPS·승인·영속 journal·동적 실행 loop 연결은 아직 완료하지 않았다.**
새 리뷰 프레임워크나 별도 회계 체계는 만들지 않았다.

## 작업 경로와 기준

- 통합 위치: `C:\Users\<user>\.codex\worktrees\experiment03-live-preparation\JourneyMap`
- 브랜치: `codex/experiment03-live-preparation`
- HEAD: `557fb3df10149d7b7738137b7ac1038dfba24e84`
- 읽기 전용 Actual 조사: `C:\Users\<user>\Documents\JourneyMap\.worktrees\phase1`,
  `codex/phase1-concurrent-runtime`, HEAD `97a5b6a24079b1f3bf3afb45bac85d30c1468336`.
- ZIP SHA-256: `6febaaac8c479c1fb813a76fa40838b6fe4d9a14c13eafd4ce0d3abc8dc22e5c`.
  Manifest 10개 항목을 검증했다. `files/`의 세 파일만 적용했으며 ZIP patch는 중복 적용하지 않았다.
- Python 3.12.14 번들 runtime과 기존 Documents 프로젝트의 dev packages를 사용했다.
  PYTHONPATH는 연구 worktree의 `src`를 먼저 지정했고 실제 import 경로를 검사했다.

`BASELINE_SHA256.json`의 145개 중 144개는 현재 파일과 일치했다. 차이는
`src/journeymap_memoryreplica/__init__.py` 하나다. ZIP의 기대 hash는 `9698bfd2…`,
실제 파일은 `e8d4a504…`이며 10월 30일 적용 CHANGESET의 hash와 일치한다.
실제 파일은 문서 문자열만 담고 있다. ZIP에 기대한 초기화 파일 원문이 없어 정확한
텍스트 차이는 확정할 수 없다. 이 차이를 숨기거나 기준 hash를 맞추려고 덮어쓰지 않았다.
E04 140개와 MemoryReplica의 나머지 구현 4개는 제공 기준과 일치한다.

## 실제 변경

신규 파일 세 개를 추가했다:

- `src/journeymap_memoryreplica/responses.py`: 순수 wire 생성, 응답·usage 분류, 일회성 inert transport.
- `src/journeymap_memoryreplica/responses_probe.py`: 실제 기존 Engine을 사용하는 합성 probe와 비실행 pilot draft.
- `tests/test_memoryreplica_responses.py`: 제공 20개 검사와 추가 경계 회귀 2개.

관련 문서는 새 `docs/MEMORYREPLICA_RESPONSES_REPORT_2026-11-02.md` 하나를 추가했다.
기존 소스·테스트·문서·실험 기록은 수정하지 않았다. 특히 `paired.py`의 exact
`OfflineReplies` 제한이나 E04의 source gate를 완화하지 않았다.

제공 코드에 적용한 수정은 신규 파일 안에 한정된다:

1. 프로젝트 Ruff가 요구하는 import 그룹을 정리했다.
2. JSON의 task kind 또는 output item type이 배열/객체일 때 set membership에서
   `TypeError`가 나던 문제를 재현했다. task는 ValueError로 전송 전에 거부하고,
   응답은 `UNEXPECTED_OUTPUT_ITEM`으로 기록하며 text를 Engine에 전달하지 않도록 했다.
3. 위 두 반례 검사와 strict mypy에 필요한 테스트 변수 타입을 추가했다.

## 수행한 검증

| 항목 | 실제 결과 |
| --- | --- |
| 제공 unittest 최초 실행 | 20 passed |
| 수정 후 신규 unittest | 22 passed (제공 20 + 추가 2) |
| 관련 pytest | 62 passed, 실패/오류/skip 0 |
| pytest 구성 | 기존 MemoryReplica 39 + 신규 22 + 기존 E04 source gate 회귀 1 |
| 신규 파일 Ruff / format | 3개 통과 |
| 신규 파일 strict mypy | 3개 통과; follow-imports=silent로 우회하지 않음 |
| CLI source fingerprint | 현재 소스·설정 147개, 실행 전후 동일 |
| 실제 import | 요청한 연구 worktree의 기존 E04와 신규 두 모듈로 확인 |
| socket / HTTPS / credential guard | 실행 중 접근 시도 각각 0 |

전체 1,646개 회귀는 이번에 다시 실행하지 않았다. 기존 E04 및 Actual 소스가 바뀌지 않았고,
새 모듈의 직접 영향 범위와 기존 E04 source gate를 검사했다. 과거 전체 통과 기록과 이번
62개 결과를 합쳐 새 전체 회귀 통과라고 표시하지 않는다.

실행한 CLI는 다음 명령과 같은 모듈·인자를 사용하며, 별도 process의 guard launcher에서
네트워크·키 조회를 차단했다. 출력 파일은 새 경로에 exclusive creation으로 생성했다.

```text
python -m journeymap_memoryreplica.responses_probe --source-root <연구 worktree> --output <새 경로>/responses-mock.json
```

모의 정상 3조건(none/recent/full)은 모두 A→S→B로 B에 4tick 도착했다. 합성 응답으로
지정한 경로이며 LLM의 기억 효과나 행동 품질을 관찰한 결과가 아니다.
첫 행동 실패 5종(INCOMPLETE, REFUSAL, 불완전 framing, usage 누락, 잘못된 JSON)은 모두
Engine 행동 0개, A 유지, 경과 0tick이다. 재구성 응답은 첫 행동 wire로 공유되지 않는다.
생성한 증거에서 source hash 필드만 제외하면 ZIP의 `MOCK_EVIDENCE.json`과 정확히 같다.

보존 inventory의 기존 연구 파일 **35,995개가 모두 바이트 동일**하다. 기존 소스 144개,
테스트 파일 47개, 실험 기록 35,768개를 포함한다. 읽기 전용으로 조사한 Actual 소스·문서
187개도 모두 동일하다. cache, pytest 임시 디렉터리, bytecode, DB와 `.env`는 inventory에서
제외했다. 운영 DB를 열어 상태를 재검증한 결과나 전체 파일시스템의 불변 보장은 아니다.

## 기존 실제 실행 체계에 연결할 최소 범위

읽기 전용으로 확인한 결과, **transport 교체만으로 연결할 수는 없다.** 현재 준비 경로의
`exchange()`는 exact `InertTransport`만 허용하고 `responses_probe.run_mock_pair()`는
두 MOVE를 지정한 합성 probe다. 실제 동적 loop나 영속 발송·정산 기능으로 사용하면 안 된다.

### 1. 기존 전송·진단을 재사용하는 얇은 연결

Actual의 [HTTPProbeTransport](C:/Users/<user>/Documents/JourneyMap/.worktrees/phase1/src/journeymap/experiments/paid_probe.py:63)는
고정 endpoint, 무재시도, 수신 크기·완료 시간·framing·secret echo 검사를 제공한다.
새 HTTPS 구현 대신 이 경계를 재사용할 수 있다. 다만 현재 pure 모듈의 `InertTransport`
제한은 유지하고 별도 승인된 실행 composition에서만 호출해야 한다.

`WireReply`의 원문·HTTP 상태·elapsed_ms·error·diagnostic을 신규 decoder에 연결하는 얇은
adapter가 필요하다. `Frame.complete`는 JSON 파싱 성공이 아니라 실제
`TransportDiagnostic.framing_complete`를 따라야 한다. 원문/상태가 없는 불확정 응답에
정상 Frame이나 진단을 합성해서는 안 된다. canonical body bytes와 예약·journal의
body digest가 실제 전송 bytes와 같은지도 검사해야 한다.

현재 두 worktree는 서로 다른 `journeymap` 패키지다. 연구 worktree에는 Actual의
`paid_probe`/lineage 모듈이 없고, Actual에는 E04 `route_update` 모듈이 없다.
PYTHONPATH에 두 `src`를 나열하면 한 패키지 루트가 선택되므로 정상적인 공유 구성이
되지 않는다. 미래 연결 시 필요한 transport/journal의 좁은 공유 지점을 하나의 검토된
실행 source composition으로 연결해야 한다. Actual 전체 복사나 혼합 import로 우회하면 안 된다.

### 2. 같은 회계·승인 체계 안의 MemoryReplica 전용 결합

[candidate_binding](C:/Users/<user>/Documents/JourneyMap/.worktrees/phase1/src/journeymap/adapters/probe_continuation.py:144)은
72개 corpus와 source, authority checkpoint, 과거 미확정 요청, exact plan을 고정한다.
현재 continuation 승인이나 72회 plan을 MemoryReplica의 최대 63회 요청에 재사용할 수 없다.
MemoryReplica task/profile/schema, 양쪽 source inventory, 9개 case 순서·기억 정책,
재구성/행동 한도, 출력 경로, endpoint, 시간·토큰·예산 후보를 묶는 명시적인 버전 분기가 필요하다.
새로운 유료 실행 정책·예산에 대한 결정과 정확한 사람 승인은 별도다.

회계는 기존 [Ledger](C:/Users/<user>/Documents/JourneyMap/.worktrees/phase1/src/journeymap/adapters/probe_execution.py:208)의
예약→시도→정산·미확정 유지와 nano-USD 단위를 재사용하는 방향이어야 한다.
`responses.usage_record()`의 Decimal 환산값은 진단이며 새 권위 원장이 아니다.
현재 Ledger/FollowupLedger의 plan은 72회 등 기존 계약에 고정돼 있어, 새 MemoryReplica
plan을 지원하려면 기존 버전을 보존하는 검증된 분기가 필요하다. 캐시 정보가 없을 때의
상한 추정을 정확한 비용처럼 정산해서는 안 되며, reasoning을 출력에 다시 더하지 않는다.
새 decoder만으로는 run 전체 response ID 중복도 차단하지 않으므로 기존 중복 ID 검사와
journal의 연결 검증을 유지해야 한다. `$22`는 제공 draft의 미승인 후보 값이다.

### 3. 고정 corpus 대신 Engine 결과로 이어지는 전용 coordinator

[paid.run_admitted](C:/Users/<user>/Documents/JourneyMap/.worktrees/phase1/src/journeymap/experiments/paid_probe.py:243)는
미리 고정된 `corpus.requests`를 순회한다. [continued_probe.execute](C:/Users/<user>/Documents/JourneyMap/.worktrees/phase1/src/journeymap/experiments/continued_probe.py:55)도
`life_reasoning_probe.prepare()`의 고정 corpus를 생성한다. 그대로 호출해서 MemoryReplica를
실행할 수 없다. 새 일반 framework 대신 MemoryReplica 전용 coordinator 한 경로가 필요하다.

각 case는 동일한 scripted 준비 경험에서 시작하고 두 첫 요청을 응답 전 독립적으로 고정한다.
이후 실제 ActionRequest를 GamePort에 제출한 결과와 archive로 다음 context를 만들며 같은
기억 정책, 6행동/24tick 한도를 계속 적용한다. 요청마다 기존의 승인·원장 최신성 확인,
RESERVED(키 조회 전), ATTEMPTED(전송 전), RESPONSE 원문 저장, SETTLED가 확정된 뒤에만
TEXT_READY를 의미 검증과 Engine으로 전달해야 한다. JSON 실패·Engine 거부·미도달·운영 실패는
각각 보존하고 retry/resume/fallback을 추가하지 않는다.

### 4. 기존 journal·무재호출 감사의 새 protocol 분기

[reduce_with_ledger](C:/Users/<user>/Documents/JourneyMap/.worktrees/phase1/src/journeymap/adapters/probe_journal.py:38)는
`requests[ledger.attempts]`와 본문을 대조하고, [check_snapshot](C:/Users/<user>/Documents/JourneyMap/.worktrees/phase1/src/journeymap/adapters/probe_lineage.py:314)과
`continued_probe.make_report()`는 현재 고정 `probe.prepare()`와 전체 corpus를 대조한다.
따라서 MemoryReplica를 위한 동적 요청/Engine step 기록 및 replay 분기가 필요하다.
같은 journal·권위 원장을 확장하는 범위이며 별도 원장이나 저장 요약만 믿는 감사가 아니다.

감사는 당시 동결 source/runtime에서 저장 원문과 Action stream으로 입력·wire·기억 선택·
Engine 결과·종료·예약을 새 process에서 재계산해야 한다. 승인·source·request/body digest·
응답 ID와 사용량의 연결도 검사한다. Provider 재호출은 금지한다. 과거 protocol reader와
당시 동결 runtime 감사는 그대로 보존하고 과거 report를 새 형식으로 재봉인하지 않는다.

### 운영 이력과 F1의 경계

이 조사는 source/docs만 읽었으며 운영 registry나 계정은 열지 않았다. 문서에 기록된
기존 UNCERTAIN, 미확정 예약, 소비 ID와 F1의 조사 종료·미재현·원인 미확정 상태를
수정하거나 해소했다고 판정하지 않았다. 현행 continuation은 지정 이력의 후속 한 번에
한정돼 있으므로 새 DB나 빈 이력으로 MemoryReplica를 시작하는 우회는 허용되지 않는다.
필요한 운영 인수와 새 task의 승인 결합 범위는 실제 실행 준비 단계에서 별도로 결정해야 한다.

이번 작업에서 위 연결 변경은 구현하지 않았다. 실제 서비스 schema 수락, 모델 접근,
현재 단가·청구, durable HTTP 결합 및 실제 MemoryReplica LLM 행동은 미검증이다.
첨부 모델·설정·rate card는 동결 후보로만 보존했으며 현재 서비스 정보로 재확인하지 않았다.

## 검증 자료

`C:\Users\<user>\Documents\JourneyMap\artifacts\memoryreplica-responses-20261102`

- `baseline-check.json`, `before-files.json`, `actual-before-files.json`: 기준·보존 inventory.
- `unittest-initial.log`, `malformed-initial.log`: 제공 검사와 재현한 문제.
- `unittest-final.log`, `pytest-focused.log`, `pytest-focused.xml`: 실제 검사 결과.
- `mypy-final.log`, `ruff-final.log`, `format-final.log`: 신규 세 파일 정적 검사.
- `cli-final.log`, `responses-mock.json`, `evidence-summary.json`: 새 process의 합성 증거.
- `preservation-result.json`: 기존 파일 hash 대조 결과.
- `CHANGESET.json`, `implementation.patch`, `final-files/`: 이번 적용 변경만 담은 검토 자료.

실제 API 호출·키 조회·계정 접근·승인 파일 생성·운영 DB 변경·commit/push/merge는 하지 않았다.
