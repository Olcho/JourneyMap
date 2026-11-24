# Probe 전송 진단 계약

2026-10-26. protocol `life-reasoning-probe-execution-2`, 진단 버전 `probe-transport-diagnostic-1`.
과거 17번째 실패 원인을 규명한 변경이 아니다. 진단은 향후 관측을 분류하며 회계 확정 권한은 갖지 않는다.

## 관측 단계와 의미

| stage | 실제로 감싼 코드 경계 |
| --- | --- |
| REQUEST_SCAN | 전송 전 요청 문자열의 키 echo 검사 |
| CONNECTION_OBJECT | HTTPSConnection 객체를 얻는 factory 호출 |
| REQUEST | conn.request; 이 안에서 DNS/TCP/TLS가 수행될 수 있음 |
| RESPONSE_HEADERS | getresponse 및 status 취득 |
| BODY_READ | read1/isclosed, 크기 및 read 사이 deadline 확인 |
| FRAMING_CHECK | 본문 수신 후 알려진 길이 잔여량과 chunked 상태 확인 |
| DECODE_JSON | UTF-8 decode, strict JSON 및 object 형식 확인 |
| SECRET_SCAN | 원문/escaped key/정규화 JSON의 키 echo 검사 |
| RUNNER | send 바깥으로 나온 예외를 runner에서 보수적으로 포착 |
| CREDENTIAL | 예약 후 prepare 실패; 전송 시도 이전 NOT_SENT |
| RECORDING | 명시적 recording-only 합성 transport. HTTP 단계/완료를 관측했다고 주장하지 않음 |

객체 생성 성공을 DNS/TCP/TLS 성공으로 기록하지 않는다. 해당 세부 연결 단계의 성공 여부는 계측하지 않았다.
연결 preflight, 추가 HTTP, 재시도, 계정 GET은 없다. finally의 close는 기존 best-effort 정리이며
그 성공 여부를 진단하는 필드는 없다. 이 계약은 소켓 packet/TLS handshake를 독립 관찰하는 trace가 아니다.

## 고정 분류와 비밀정보 경계

exception_category는 isinstance만 사용하며 메시지/args/클래스명/모듈명/traceback을 읽지 않는다.
우선순위는 TimeoutError → gaierror → SSLError → RemoteDisconnected → ConnectionResetError →
ConnectionRefusedError → IncompleteRead → OSError → UnicodeError → ValueError → RecursionError → OTHER다.
RemoteDisconnected가 reset의 하위 타입인 점과 SSL/DNS/timeout이 OSError에 가려지지 않는 점을 검증한다.

저장 category는 TIMEOUT, DNS, TLS, REMOTE_DISCONNECTED, CONNECTION_RESET, CONNECTION_REFUSED,
INCOMPLETE_READ, OS_ERROR, UNICODE_ERROR, VALUE_ERROR, RECURSION_ERROR, OTHER 중 하나다.
예외가 없으면 NONE이다. 알 수 없는 예외는 동적 이름 대신 OTHER다. errno나 임의 헤더는 저장하지 않는다.
IncompleteRead.partial을 포함한 예외 내부 본문, 환경변수, 키, 요청 본문을 진단에 넣지 않는다.
기존 ATTEMPTED 본문과 응답 보관은 원래 계약을 따르며 진단을 통해 우회하지 않는다.

## journal 결합과 의미 검증

진단은 별도 sidecar가 아닌 RESPONSE 또는 NOT_SENT data의 필수 diagnostic 필드로 저장한다.
추가 전송 경로는 없고 기존 SQLite FULL transaction에서 event·count·tail·status와 함께 확정된다.
원래 journal과 따로 저장해 한쪽만 성공하는 모호성을 피하기 위한 선택이다.

정확한 필드는 version, run_id, request_id, body_digest, journal_stage, journal_ordinal,
stage, category, disposition, framing_complete다. 추가 필드나 누락도 거부한다.
run/request/body/event/ordinal을 바깥 journal과 대조하므로 다른 요청의 진단을 옮겨도 거부한다.
NONE/EXCEPTION 모순, 조기 단계의 HTTP status 존재, 불완전 단계의 framing_complete=true,
HTTP 경로에 RECORDED를 대입하는 경우와 deadline/원문/transport_error 모순도 검사한다.
hash chain을 재계산해도 이 의미 검증을 통과해야 한다.

disposition은 COMPLETE, LATE_COMPLETE, EXCEPTION, DEADLINE_PARTIAL, FRAMING_INCOMPLETE,
SIZE_LIMIT, INVALID_RESPONSE, SECRET_ECHO, RECORDED로 고정한다.
framing_complete는 실제 HTTP 본문 완전성 검사를 통과한 뒤만 true다. 유효 JSON이나 usage의 존재와는 별개다.

- COMPLETE/LATE_COMPLETE: SECRET_SCAN 단계, 원문 보존, 예외 없음, 완료 시각이 각각 120,000ms 이하/초과.
- 하위 TimeoutError: EXCEPTION/TIMEOUT으로 기록하되 전송 후 회계는 TRANSPORT_UNCERTAIN을 유지한다.
- 부분 수신 중 deadline: DEADLINE_PARTIAL, transport TIMEOUT, 원문 없음, 예약 유지.
- 완전한 늦은 응답: LATE_COMPLETE, 원문과 유효 usage 회수, 기존 classifier가 TIMEOUT 처리, 엔진 미전달.
- 길이 잔여/chunked 미완료: FRAMING_INCOMPLETE 또는 stdlib 수신 예외, 원문 없음, 예약 유지.
- JSON/UTF-8 실패: framing 검사 후 해석 단계의 INVALID_RESPONSE 또는 EXCEPTION, 원문 보류.

HTTP 오류·신원/usage 검증은 기존 classifier의 책임이다. 진단만으로 미전송·무과금·예약 해제나 엔진 허용을 결정하지 않는다.
CREDENTIAL 실패만 기존처럼 ATTEMPTED 전 NOT_SENT로 예약을 해제한다.
body/해석에서 ValueError가 발생하면 실제 단계에 따라 분류한다. 해석 오류는 INVALID_RESPONSE이며
해석 바깥 오류는 TRANSPORT_UNCERTAIN이다. 모두 원문 없으면 미확정 비용/예약 정책은 같다.

## 저장 실패와 증거 수준

진단이 없거나 유효하지 않으면 RESPONSE를 만들지 않고 예외로 중단한다. 정상 진단을 대신 만들어내지 않는다.
기존 recording transport만 명시적 RECORDED를 사용할 수 있다. runner 예외는 그 경계를 실제로 포착한 EXCEPTION이다.
진단 포함 append의 OSError/sqlite3.Error는 고정 문구 오류로 노출하고 원문 예외 내용은 출력하지 않는다.
쓰기 실패나 commit 직후 실패에서도 다음 유료 요청은 없다. DB에서 마지막으로 확정된 prefix를 OPEN으로 보존한다.
process 종료·임의 BaseException도 자동 완료/재개하지 않는다. inspect는 읽기 전용이다.

이는 신뢰된 로컬 코드가 남긴 구조화 관측이다. 운영자가 journal과 승인·코드를 통째로 바꾸면
네트워크 원인을 독립 인증할 수 없다. 허용되는 다른 예외로 일관되게 위조한 진단을 hash만으로 판별한다고 주장하지 않는다.

## v1 호환과 실행 차단

v2 binding은 transport_diagnostic_version을 추가한다. source/protocol 결합이 달라 과거 승인으로 실행하지 못한다.
기존 v1 DB를 v2로 자동 변환하지 않으며 v2 claim/read_journal은 protocol marker가 다르면 거부한다.
v1 실제 파일은 새 identity로 재봉인하지 않는다. 변경 전 source a362689b…의 Python runtime은
프로젝트 artifacts/probe-continuation-20261026/frozen-v1/src에 감사 목적에 필요한 .py 파일만 보존했다.
과거 report는 원래 위치에서 읽는다. 운영 DB와 사람 승인 파일은 복사하지 않았다.

이 구현은 기존 UNCERTAIN과 예약을 해소하지 않는다. 다음 실제 실행은 정책·독립 검토·새 실행안·사용자 승인이 남아 있다.
