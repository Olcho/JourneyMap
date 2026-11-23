# MemoryReplica retention v1 Actual

2026-11-11 KST. 구현 기준은 [실험 실행계획 v1 A~G](https://app.notion.com/p/40de2656dc2681b391f4c8f9fc3ef51c)다.

새 배치의 프로토콜은 `memoryreplica-actual-outcome-retention-1`이다. 개발은 고정 6세계와 30실행 및 최대 210요청, 본 평가는 고정 60세계와 300실행 및 최대 2,100요청이다. N의 과거 기억은 0개이고 S/F/M/R은 고유 방향성 도로 기록 4개씩을 사용한다. 각 실행의 제한은 6행동/24틱이다. 주 비교는 M−R이고 보조 비교는 S−F다.

`case_id`는 세계, `episode_id`는 세계와 기억 정책 조합이다. Actual은 `episode_id`로 요청을 식별하고, 저장 응답을 바로 앞 wire의 SHA-256에 연결한다. 연구 프로세스가 재구성과 첫 행동 입력을 미리 고정한다. 지도 응답은 행동 입력에서 제외한다. 기존 HTTP transport에는 검증한 `next_wire.body`를 전달한다.

새 계획은 기존 SQLite 원장, writer lease, 승인, 요청 예약, 송신 기록, 응답 정산을 사용한다. 원장이 정산한 응답을 연구에 전달한다. 저장된 과거 배치는 해당 소스로 검증한다. 전송·회계 미확정 실행의 점수는 null이며 나머지 계획 행의 미시도 상태를 기록한다. 새 계획의 기본 금액 한도는 US$0이다.

공개 연구 패키지는 저장소 루트의 [experiments/memoryreplica-v1](../../experiments/memoryreplica-v1/README.md)에 있다. Actual은 격리된 Python 자식 프로세스의 UTF-8 모드로 연구 코드를 호출하고, 양쪽 소스와 고정 입력의 해시를 binding에 담는다.

원본 모의 이력 → B 72슬롯 → 기존 9사례 → 개발 30실행이 한 시험 원장에서 통과했다. 모의 응답 150개를 기존 연구 결과와 대조했다. [11월 11일 구현·검증 보고서](C:/Users/<user>/Documents/JourneyMap/artifacts/memoryreplica-v1-20261111/REPORT.md)

11월 13일부터 저장 기록은 당시 runtime descriptor와 동결 소스 inventory를 사용해 binding을 재구성한다. 저장 승인, event chain, 소비 ID, 정산 및 과거 보고서의 무재호출 재생을 대조한다. 현재 Python으로 재생 결과가 일치함을 확인할 수 있다. 과거 실행 파일의 실제 바이트 확인은 별도의 보장이다.

새 binding은 현재 Python의 경로·버전·SHA-256을 기록한다. 실제 실행의 승인과 전송 전 검사에는 그 새 descriptor의 동일성이 필요하다. [11월 13일 실행 준비 결과](C:/Users/<user>/Documents/JourneyMap/artifacts/memoryreplica-v1-20261111/DEVELOPMENT_PREPARATION_2026-11-13.md)

본 평가 sequence 2183에서 저장이 중단된 경우에 한해 별도 복구 binding을 사용한다. 복구 승인에는 원래 run과 binding, 정산된 checkpoint, 현재 소스와 Python, 저장 프레임으로 복원한 다음 wire, 누적 한도와 원래 승인 만료 시각이 연결된다. writer lease 아래 복구 승인 ID 소비와 원장 기록을 한 트랜잭션으로 커밋한 후 다음 미전송 요청을 처리한다. 미정산 항목이 있거나 같은 복구 승인이 이미 소비됐으면 실행을 중단한다. 원래 승인, fence, 응답과 점수는 보존한다. 새 저장 오류에는 journal 단계, 쓰기 구간과 사용 가능한 숫자 오류 코드가 기록된다. [복구 실행안](C:/Users/<user>/Documents/JourneyMap/artifacts/memoryreplica-v1-main-recovery-2183/recovery-proposal.json)

Sequence 2189의 전송 전 DNS 예외에는 별도 1회 승인으로 `main-ring-10--F:5` 본문을 284번째 시도로 제출한다. 정산된 프레임 5개로 요청을 복원하며, 이전 `TRANSPORT_UNCERTAIN`과 중단 이벤트, 승인 및 예약을 원장에 남긴다. 예산은 US$15에서 본 평가 미해결 예약 US$0.336864를 먼저 차감하고, 요청은 누적 283회부터 센다. 운영 원장에는 승인 소비와 예외 적용을 원자적으로 추가한다. 종료 자료는 별도 경로에 보관한다. 이후 불명확한 전송이나 저장 실패에는 자동으로 다시 전송하지 않는다. [후속 binding](C:/Users/<user>/Documents/JourneyMap/artifacts/memoryreplica-v1-main-dns-followup-2189/followup-binding.json)

Sequence 4822의 HTTP 503에도 해당 요청에 한정한 별도 1회 승인을 기록한다. 저장된 지도 응답을 재생해 `main-bridge-3--M:1`의 동일한 첫 행동 요청을 901번째 시도로 제출한다. DNS와 HTTP 503의 본 평가 예약 US$0.673728을 예산에서 차감하며 과거 이력 예약은 별도로 유지한다. 이 재전송을 운영상 이탈로 보고하고 새 전송 불확정이나 저장 실패 시 후속 요청을 중단한다. [HTTP 503 후속 binding](C:/Users/<user>/Documents/JourneyMap/artifacts/memoryreplica-v1-main-http503-followup-4822/followup-binding.json)

새 60세계 후속 배치는 같은 300실행 프로필을 사용하며 별도 입력, Run ID, 승인과 비용 한도를 갖는다. 원장 검증에서 `ALL_CASES_COMPLETED`이고 현재 요청의 예약이 비어 있으면, 원장이 검증한 DNS 및 HTTP 503 예외 예약만 남은 본 평가를 전임 배치로 연결할 수 있다. 예외 예약은 새 binding의 과거 미해결 비용에 합산하며 새 배치 원장의 예약과 한도는 별도로 시작한다. [후속 배치 준비](../../../artifacts/memoryreplica-v1-followup-20261118/README.md)

후속 배치의 sequence 7366에는 45건의 점수, 223건의 정산과 224번째 `ATTEMPTED` 기록이 있다. 저장 원응답이 없는 이 checkpoint에 한해 새 복구 승인이 `followup-20261118-ring-9--R:4`의 동일 바이트를 225번째 시도로 한 번 제출하도록 허용한다. 원래 시도와 예약 US$0.336864는 보존한다. 새 시도의 정산된 프레임만 행동 입력으로 전달하며, 재전송을 포함해 누적 2,100요청, US$15 및 자동 재시도 0회를 적용한다. 새 승인 창을 사용하며 실행 중 Windows 자동 절전 방지 요청을 유지하고 종료 시 해제한다. [복구 제안](../../../artifacts/memoryreplica-v1-followup-20261118/recovery-7366/recovery-proposal.json)

`memoryreplica-independent-authority-1`은 지정한 단일 retention Run ID용 빈 SQLite 원장을 준비한다. 새 원장의 genesis는 경로, 장치, authority ID와 출처 SHA-256을 고정한다. 기존 `TaskApproval`, writer lease, 시도 fence, 요청 예약·정산, Engine 재구성과 무재호출 감사를 재사용한다. genesis의 장치·경로와 바인딩이 일치하고 원장에 실행 기록이 없어야 새 실행을 시작한다. 이전 원장의 채점·응답·승인·잔액은 그대로 보존한다. 독립 실행에는 복구와 자동 재시도를 허용하지 않는다. [후속 300건 독립 실행 제안](../../../artifacts/memoryreplica-v1-followup-20261122/README.md)
