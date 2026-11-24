# Experiment 04: 공식 경로 변경 정보, 오프라인 구현 계약

2026-10-08 구현, 2026-10-09 목표 계약 보완(v2). 상태는 **오프라인 검토 / protocol draft**다. 이 문서는 실제 API 실행 승인이나 본실험 모델·표본 수·순서의 동결이 아니다. fake 결과는 프로그램 correctness 자료이며 LLM 행동 효과의 증거가 아니다. 원본 구현 지시문은 `trials/experiment04-offline-verification-20261008/implementation-contract.md`에 보존한다.

## 독립된 시나리오와 시간

한 인물 traveller가 S에서 G로 이동한다. S-A=2, S-B=2, A-C=2, A-D=4, B-D=4, B-C=6, C-G=2, D-G=2, C-D=4를 양방향으로 생성한다. 총 18개의 방향 Route다. 장소명 치환과 다른 비용 지형은 구현하지 않았다.

초기 지도는 작성된 tick 0의 AUTHORED_INITIAL KnowledgeRecord다. 현재 World Truth로 지도를 재생성하지 않는다. 초기 provider 결정도 observe → 입력 기록 → provider 응답 기록 → 의미적 의도 결합 → 실제 GamePort submit → receipt 및 닫힌 경험 순서다. S-to-A/B가 실제 성공하여 tick 2에 도달한 경우만 정상 prefix다. 원본 응답, 요청, Observation, receipt, application history와 checkpoint를 저장한다. 다른 초기 행동, 응답 실패와 infrastructure 실패는 원본 시도로 남기고 분기나 재호출을 만들지 않는다.

각 정상 prefix에 새 kernel/application/authority/archive/provider를 세 번 만든다. 초기 의미적 의도를 각 새 Observation에 결합해 실제 제출하고 경험을 닫는다. 다른 run의 ActionRequest를 사용하거나 초기 provider를 재호출하지 않는다. 부모 원본의 record digest, run ID와 response digest를 연구 기록에 별도로 연결한다. checkpoint는 개입 전 tick 2의 state/position/digest와 닫힌 경험 1개다.

| 조건 | 개입 | tick 3에서 받은 추가 정보 |
|---|---|---|
| unchanged (①) | 없음 | 없음 |
| closed-unannounced (②) | C-to-G와 G-to-C 원자적 폐쇄 | 없음 |
| closed-notified (③) | 같은 폐쇄 후 수신자 공식 통보 | OFFICIAL_NOTICE |

모든 조건은 외부 kernel.advance_to(3)로 진행한다. 인물이 WAIT한 경험을 합성하지 않는다. 폐쇄는 tick 3 priority 0, 통보는 같은 tick priority 1이다. 기존 Movement의 close_routes와 deterministic system transaction 경계를 사용한다. NoticeHandler는 대상 엔티티와 이미 완료된 양방향 폐쇄를 검증한다. 일반 INFORM, Core, application 권한 검증과 기존 03 구현을 변경하지 않았다.

## 정보 경계와 전체 wire

원래 지도는 보존한다. 공식 통보는 수신자만 받으며 source kind, learned_at, 원본 사건 참조를 갖는 별도 KnowledgeRecord다. 현장 지식은 C/G 도착의 실제 ActorMoved 사건 또는 C/G에 있는 인물이 폐쇄 사건을 목격할 때 DIRECT_OBSERVATION으로 투영한다. 이는 이 시나리오의 명시적인 국소 지식 취득 규칙이다. C의 현재 Observation은 실제 출구 상태도 제공한다. A/B는 원격 C-G 상태를 볼 수 없다. 출구는 현재 위치의 route만, 안정된 순서와 고정된 필드로 재구성한다. 실제 엔진이 반환한 거부/실패 receipt는 다음 닫힌 경험에만 나타난다.

04 전용 `adapters/route_update.py`의 엄격한 semantic allowlist는 현재 및 모든 과거 경험의 Observation을 재귀적으로 투영한다. 허용되지 않은 section/field/schema, 미래 지식과 닫히지 않은 경험은 실패한다. run/actor/observation/knowledge-record/source-ref/권한 식별자는 중첩 기록에서도 제거하고 의미적인 장소·route·출처 종류·학습 시점·receipt reason/tick은 보존한다. 원본과 의미 입력을 나란히 저장한다. 새 profile identity는 `experiment04-route-change-decision-2`다. 기존 v1은 원본 기록의 재감사를 위해 정확한 문구로 보존한다.

전체 모델 wire에는 완전한 profile+semantic input, JSON action schema, model identity, store=False가 포함된다. canonical UTF-8 본문, 길이와 SHA-256을 기록한다. 첫 provider 호출 전에 같은 prefix의 ①② 전체 wire를 byte equality로 검사한다. ③은 기존 경험·국소 관찰·설정·schema가 같고, tick 3 공식 통보 한 개만 지식에 추가되는지 검사한다. 조건명, 연구 정답, run ID, HTTP 헤더와 previous_response_id는 모델 입력에 없다. HTTP 계층은 이번 구현에 없다.

모든 닫힌 경험을 항상 제공한다. 선택·요약·검색·reflection·고정 기억 예산과 다른 인물 정보를 추가하지 않는다. FakeProvider는 불변 Script와 호출 위치만 갖고 World Truth, GamePort, ResearchView를 받지 않는다. fixture가 소진되면 synthetic transport failure로 종료하며 WAIT나 응답을 대신 생성하지 않는다. 그 실패도 fake임을 plan과 audit에 표시한다.

## v2 목표와 적용 단계

초기 tick 0의 실제 provider 선택은 기존 도착 목표와 S→A/B 선택 계약을 유지한다. 초기 선택에는 새 시간 최소화 목적을 적용하지 않는다. prefix를 scripted preparation으로 바꾸거나 목적지를 고정하지 않는다.

후속 tick 3 이후의 모든 조건에는 같은 목적을 완전한 profile로 제시한다: G에 도착하되, 현재 결정 이후의 남은 simulated travel time을 실제 받은 경로 정보에 근거해 최소화한다. 6결정/24tick은 상한이지 더 긴 경로를 선택할 이유가 아니다. 오래된 정보보다 받은 통보와 직접 관찰을 적용하고, 보지 못한 World Truth 접근을 가정하지 않는다. 단계는 이미 제공하는 Observation 시점으로 구분하며 새로운 조건명/정답/목적지 라벨을 추가하지 않는다. MOVE/WAIT JSON, 비용·시간 한도·출처·전체 경험 정책은 유지한다. ①② 전체 첫 wire의 동일성과 ③의 합법적인 통보 차이는 v2에서도 동일하게 검사한다.

## 첫 이동 기준선과 연구 판정

코드 정책은 tick 3, A/B에서의 **첫 이동만** 비교한다. `OLD-MAP`은 작성된 지도를 사용하고, `RECEIVED-UPDATE`는 받은 공식 통보만 적용한다. 실제 세계나 조건별 closure를 직접 읽지 않으며 현장 지식을 공식 통보처럼 적용하지 않는다. 입력과 결과를 함께 보존한다. 모든 최적 첫 route를 남기고 단일 선택은 route_id의 사전순 최소로 정한다.

| 출발 | OLD-MAP ①②③ | RECEIVED-UPDATE ①② | RECEIVED-UPDATE ③ |
|---|---|---|---|
| A | A-to-C | A-to-C | A-to-D |
| B | B-to-D | B-to-D | B-to-D |

별도 연구 평가기는 실제 위치와 실제 passability로 최소 잔여 비용 및 모든 최적 첫 MOVE를 계산한다. A/open=4/C, A/closed=6/D, B/open=6/D, B/closed=6/D다. 그래프의 순수 최단경로 연산만 공유하며, 지식 기반 정책 입력과 truth 기반 연구 판정은 분리한다. 별도 동률 그래프 테스트로 모든 최적 첫 간선 보존을 검사한다.

첫 이동의 true는 **실제 세계 상태에서 남은 시간 최적인 첫 MOVE인가**를 뜻한다. G에 도착했는지는 별도의 eventual_arrival이다. 폐쇄가 없는 A에서 A→D→G는 6tick에 G에 도착하고 한도도 지키지만, A→C→G=4tick보다 길어 첫 점수는 false다. 이는 도착 실패나 목표 오해 판정이 아니다. ②처럼 변경 정보를 받지 못한 조건의 false는 정보 접근 제약을 포함한 시스템 결과이며 일반 추론 능력 실패로 단정하지 않는다. 지식 기반 두 코드 기준선은 이 해석을 보조한다.

주 지표는 `first-move-appropriateness-after-change-information`, 같은 A-prefix의 ③ 대 ②다. B-prefix, 초기 실패와 A 부족은 별도로 남긴다. A를 얻을 때까지 다시 표집하지 않는다. A-to-D/C, B-to-D/C는 실제 후속 목적지 범주이며 내부 전체 경로 계획 또는 계획 수정의 증거라는 명칭을 쓰지 않는다. 필요한 변경 정보가 전달되지 않은 ②의 부적절한 이동을 일반 추론 결함으로 해석하지 않는다.

## 종료·실패·분모

현재 공개 오프라인 protocol은 후속 6결정, 24tick(평가 tick 3 이후 deadline 27)으로 고정한다. 초기 1결정은 별도다. 계약 소요 시간으로 deadline을 넘길 요청은 엔진에 제출하지 않고 TIME_BUDGET_NOT_SUBMITTED로 끝낸다. 목표의 실제 Engine MOVE 완료, receipt, ActorMoved 및 최종 위치 G가 모두 맞으면 즉시 종료한다. 유효 WAIT와 엔진 REJECTED/FAILED는 실제 결정/행동/시간을 기록하고 한도 안에서 진행한다. 시작 거부는 0tick일 수 있다.

refusal/incomplete/malformed는 RESPONSE_FAILURE로 해당 초기 시도/분기를 끝낸다. fake TRANSPORT_ERROR는 INFRASTRUCTURE로 끝내며 모델 오답과 합치지 않는다. metadata 불일치 등 INTEGRITY는 전체 실행을 즉시 중단한다. 기록 예외도 전체 중단하며 안전한 recording-failure marker와 durable prefix만 남긴다. 자동 재시도, 응답 수리, fallback, 실패 재실행, resume는 없다.

첫 적절성은 형식 실패·미제출·부재에서 null이고 유효 제출 행동에서는 boolean이다. WAIT/부적절 MOVE는 false다. 후속 회복이 첫 점수를 덮어쓰지 않는다. 최종 도착은 완료된 실패 false, infrastructure/integrity/미완성 null이다. 완료 비용/초과 tick은 실제 도착에서만 계산하며 실패 비용은 null, 실제 소비 tick은 별도다. 초기 이동 2tick과 외부 진행 1tick을 후속 비용에 더하지 않는다.

audit는 planned initial denominator, initial decision intents, 정상 A/B prefix, 생성/예정/미시도 분기, 첫 응답 상태, 판정 가능한 첫 행동, 적절 MOVE, 최종·회복 도착, 종료 이유, 불완전 짝의 조건별 이유를 기록한다. 적절성 분모는 판정 가능 첫 행동, 운영상 도착률 분모는 생성 분기다. 확인 가능한 최종 결과 분모도 따로 있다. durable INTENT는 결정 요청이 기록된 증거이고, 기록 오류 직후 provider 실행 여부를 무조건 확정하는 증거는 아니다. recording marker의 attempted count와 phase를 별도로 남긴다. fake API usage는 null/NOT_APPLICABLE_FAKE이며 실제 비용을 생성하지 않는다.

## 기록 재구성 audit의 범위와 실행

새 plan은 source/runtime identity, graph, profile/schema, 조건, seed와 한도를 기록하고 digest를 갖는다. 이는 fake-only PROTOCOL_DRAFT이며 실험 모델·파라미터·금액 예산·mirror·본실험 순서는 null이다. 첫 요청 이전 INTENT, 반환 RESPONSE, 실제 결과 RESULT를 append-only hash-chain journal에 기록하고 원본 export digest를 연결한다. 내부 해시는 일관성 증거이며 외부 서명이나 물리적 crash durability의 증명은 아니다.

audit/inspect는 provider 생성/호출, 키 조회, 네트워크 없이 새 **전체 application**에서 초기 의도 및 시스템 사건/행동을 재실행한다. Observation, EventTraceArchive, authority, receipt, Events, 위치, tick, knowledge, 입력 wire, 실제 평가와 baseline을 다시 계산하여 journal과 export 모두에 대조한다. metrics 요약만 읽어 승인하지 않는다. journal/export/plan 변조는 EXCLUDED, torn/durable partial은 INCOMPLETE다. 이미 검증된 첫 점수는 유지하되 해당 분기의 최종 결과는 unknown으로 둔다. expected plan digest를 별도로 전달할 수 있다.

audit는 Provider와 저장 요약에 의존하지 않는 새 application 재생 검증이다. **runner와 Session 및 metrics 구현을 공유**하므로 별도로 작성한 두 알고리즘의 교차 검증은 아니다. INCLUDED는 저장 입력/기록/재생/평가의 일치 판정이며 모든 평가 논리의 독립 증명, construct validity, 출처 증명을 뜻하지 않는다. plan의 source/runtime 필드는 저장하지만 audit_directory가 현재 source/runtime 자체와 대조하지 않는다. 현재 소스의 정체·실행 환경·ZIP bytes/manifest·원본 불변성은 별도 verification/source/static/preservation gate에서 확인한다. 새 provenance 시스템은 추가하지 않았다.

| 기록 계약 | protocol | profile | export / audit |
|---|---|---|---|
| 과거 v1 원본 | experiment04-offline-1 | experiment04-route-change-decision-1 | export-1 / audit-1 |
| 새 기본 v2 | experiment04-offline-2 | experiment04-route-change-decision-2 | export-2 / audit-2 |

표의 export/audit suffix는 experiment04-offline-export-N / experiment04-offline-audit-N이다. v1/v2의 정확한 결합만 지원한다. schema·scenario·fake configuration은 같은 계약으로 유지한다. 새 execute/CLI는 v2만 생성하고, 감사는 plan protocol에 맞는 원래 profile로 Session을 구성한다. v1 plan/wire/journal/export를 v2 문구로 다시 쓰지 않는다. 알려지지 않은 버전 또는 혼합된 protocol/profile/instructions/export는 EXCLUDED이며 성공으로 처리하지 않는다. 기존 v1 정상·실패·기록 중단·변조 원본 8묶음의 재검증 결과는 새 보고서에 구분해서 기록한다. ZIP에서 새 runner를 실행하려면 실제 Git checkout과 의존성 구성이 필요하다. standalone ZIP 실행 지원은 이번 범위에 추가하지 않았다.

Python 3.12, 표준 라이브러리, 기존 pytest/Ruff/mypy를 사용한다. `.venv` launcher가 깨진 현재 환경에서는 bundled Python 3.12.14와 기존 `.venv/Lib/site-packages`를 PYTHONPATH에 지정했고 의존성을 설치하거나 수리하지 않았다.

```text
python -m journeymap.examples.route_update fake --output trials/<new-directory>
python -m journeymap.examples.route_update audit trials/<new-directory> --expected-plan-sha256 <digest>
python -m journeymap.examples.route_update inspect trials/<new-directory> --expected-plan-sha256 <digest>
```

현 환경의 network/key 차단 실행은 `python trials/experiment04-offline-verification-20261008/offline_cli.py journeymap.examples.route_update ...`다. 검증 script는 fake-only 실행과 audit 중 socket 연결과 실제 키 조회를 금지한다. 전체 기존 HTTP 테스트는 child process에서 테스트용 가짜 키를 주입하고 자체 monkeypatch fixture를 사용한다. 실제 키를 읽거나 로그에 남기지 않는다.

관련 테스트: `tests/test_route_update.py`, `tests/test_route_update_audit.py`, `tests/test_route_update_goal.py`. v1 완료 로그는 [2026-10-08 보고서](EXPERIMENT04_OFFLINE_REPORT_2026-10-08.md), 목표 계약 보완과 새 검증은 [2026-10-09 수정 보고서](EXPERIMENT04_GOAL_REVIEW_REPORT_2026-10-09.md)를 참조한다. 실제 API/live 실행 도구는 이 CLI에 없으며 오프라인 검토에서 멈춘다.

## 미확정 연구 항목

실험 모델·파라미터·예산·mirror 매핑·본실험 순서·prefix 표본 수는 확정하지 않았다. 중립 장소명 전단사 fixture는 미구현이다. 별도 Research Design/Compatibility 참고 문서는 로컬에 없어 첨부의 자체 완결 계약으로 진행했다. 선택 mirror 또는 참고 문서 부재를 완료된 구현으로 표시하지 않는다. 기존 두 지도 × 초기 3회 초안의 6prefix/18분기, 초기 6+후속 108=114회는 산술 상한이며 이번 fake demo 표본 수나 API 승인으로 사용하지 않는다.
