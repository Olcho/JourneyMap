

## 2026-10-17 KST F1 원인 조사 — 비부착 전체 실행과 tick 16 대조

**F1은 “이번 비부착 전체 실행에서도 미재현, 원인 미확정”으로 유지한다.** 진단 없는 전체 pytest가 통과해, 앞선 후속 검토에서 부족했던 비부착 전체 gate의 직접 실행 증거는 확보했다. 그러나 최초 digest 불일치의 필드·원인을 특정하지 못했으므로 수정 완료나 환경 문제라고 결론 내리지 않는다. 사용자 지시 4번에 따라 추가 재현 실행을 중단했다. 기존 전체 검토 판정 “수정 필요”는 원인 확인·종결 근거가 남아 있다는 의미에서 유지하며, 현재 전체 pytest가 실패 중이라는 의미로 읽으면 안 된다.

### 보존 범위와 실행 조건

시작 브랜치·HEAD는 이전 검토와 같은 `codex/actual-output-budget-8192` / `739f08d34325106cf2eaaacc3fadcdecd0858ddd`였다. 미커밋 작업은 기존 검토 문서와 `scripts/diagnose_checkpoint_view.py` 두 파일이었다. 이번 절을 추가하기 전 문서의 SHA-256은 `e72ea862b32aaa497ed304c082dd735c56219d4cf869593ebae8edd70c3078c5`, 기존 진단 도구는 `a59602b4685828b1bd17c40cb74579ce940e7c69353ddf2885520d866e50fcf4`다. 문서 원문·최초 실패·후속 재판정을 보존하고 이 절만 뒤에 추가한다.

새 증거 폴더는 다음과 같으며 이전 임시 폴더를 삭제하거나 재사용하지 않았다.

`C:\Users\USER\AppData\Local\Temp\journeymap-f1-uninstrumented-itc_8_25`

호스트 계정은 `desktop-k5vdtl2\user`, Python은 노트북 `.venv\Scripts\python.exe`의 3.12.10, pytest 8.4.2, Ruff 0.17.1, mypy 1.20.2, pluggy 1.7.0이다. `PYTHONHASHSEED`, `PYTEST_ADDOPTS`, `PYTEST_PLUGINS`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD`, `PYTHONPATH`는 해당 실행에서 모두 미설정이었다. 실제 키나 전체 환경변수는 조회하지 않았다.

전체 pytest를 **정확히 1회** 실행했다. 외부 부모 프로세스는 Git·버전·파일 hash와 종료 결과만 기록하고, 실제 테스트는 다음 별도 자식 프로세스에서 실행했다. 기존 진단 스크립트를 실행하거나 snapshot/digest/view에 wrapper·monkeypatch·추가 plugin을 붙이지 않았다. JUnit은 요청한 결과 보존 옵션이며, 새 temp/cache 경로 외에 테스트 조건을 변경하지 않았다. 기본 `-ra --strict-config --strict-markers`와 전체 `tests` 수집은 유지했다.

```powershell
Set-Location C:\Users\USER\JourneyMap-Actual
& C:\Users\USER\JourneyMap-Actual\.venv\Scripts\python.exe -m pytest --junitxml=C:\Users\USER\AppData\Local\Temp\journeymap-f1-uninstrumented-itc_8_25\junit.xml -o cache_dir=C:\Users\USER\AppData\Local\Temp\journeymap-f1-uninstrumented-itc_8_25\pytest-cache --basetemp=C:\Users\USER\AppData\Local\Temp\journeymap-f1-uninstrumented-itc_8_25\pytest-temp
```

| 직접 확인한 결과 | 값 |
| --- | --- |
| stdout 최종 결과 | **1378 passed in 872.12s (0:14:32)** |
| pytest 종료 코드 | **0** |
| JUnit | tests 1378 / failures 0 / errors 0 / skipped 0 |
| JUnit suite time | 871.852초 |
| F1 testcase | failure/error/skip 없음, time 61.074초 |
| 부모 프로세스 측정 wall time | 874.047초 |
| stderr | 0 bytes |
| 종료 HEAD / Git 상태 | 시작과 동일 |
| 실행 전후 source manifest | 파일별 원본·정규화 hash를 포함해 완전히 동일 |
| 실행 전후 source identity | 모두 `f29e9631f80b19d832e83ce7969318bae061a88b543424ed7254499aca91f973` |
| 문서·기존 진단 도구 | 전체 실행 중 변경 없음, 각각 위의 시작 hash와 같음 |

JUnit의 전체 testcase 목록·계수, stdout의 1378 passed와 종료 코드를 직접 대조했다. 이 수치는 이번에 실제 실행해 관측한 결과이며, 앞선 사용자 제공 진단 부착 결과 1378 passed / 552.08초와 별도의 실행이다. stdout·JUnit·외부 wall time은 측정 범위가 달라 각각 기록한다. 실행 시간 차이를 F1의 원인이나 환경 문제의 증거로 해석하지 않는다.

핵심 증거 SHA-256:

| 파일 | SHA-256 |
| --- | --- |
| `summary.json` | `ba1ad3da00c9ae60e5d5fbdb77f858eee59c64678cc6569fe424f2beb9ecc41b` |
| `junit.xml` | `67d4fdbd237b53af69fe266a000002fafacd460425959ef77d2335c726c5b647` |
| `stdout.txt` | `78172b0cf7575fb9811e6100f1e3d31ba9b60689577c8d4a9f999e44904f823e` |
| `source-before.json` / `source-after.json` | 모두 `7ab631cc3d48a5b09c5f711bb5367a1eadffe52781b7f779b5765d1c6d634543` |

증거 파일:

- `command.txt`, `metadata-before.json`: 실제 명령·계정·도구 버전·HEAD·시작 Git 상태·선택한 비밀이 아닌 환경 설정.
- `source-before.json`, `source-after.json`: 패키지의 Python 파일별 원본 및 LF 정규화 SHA-256과 합성 source identity.
- `stdout.txt`, `stderr.txt`: 전체 pytest의 원래 출력과 오류 스트림을 바이트 그대로 분리 보존.
- `junit.xml`: 전체 testcase 목록·실패/오류/skip 수·대상 F1 testcase.
- `summary.json`: 종료 코드·실행 시간·종료 HEAD/Git 상태·기존 문서와 진단 도구의 종료 hash·JUnit 요약.
- `file-hashes.json`: 전체 실행 종료 시점 최상위 증거 파일 hash.

### tick 16 단서의 직접 대조

같은 노트북 `.venv`와 호스트 계정에서 `LifeSession(LifeConfig())`를 새로 만들어 `run_until(1)`, …, `run_until(17)`을 순서대로 실행했다. UI 조회는 하지 않았다. 각 tick의 정상 checkpoint digest와 15·16·17의 전체 payload를 `normal-tick16-reference/`에 저장했다. 이 정상 참조는 실패한 과거 session이나 before payload의 재구성이 아니다. 정상 참조 계산과 저장 payload 분류는 전체 pytest가 진행되는 동안 별도 프로세스에서 수행했다. 테스트 자식 프로세스의 호출 순서는 바꾸지 않았으나 호스트 부하가 최초 실행과 완전히 같았다고 주장하지 않는다.

| tick | 직접 계산한 정상 checkpoint digest | 최초 보고서 after와 비교 |
| --- | --- | --- |
| 15 | `e405efa9ca63aed023240b5681eaa0d1ade7b282dcf6aa6b11ac9d9cc8020e09` | 다름 |
| 16 | `e257e1e1526dceceade3ab42f6206d80f25557f01946471b6af5c5a5682c5401` | **정확히 일치** |
| 17 | `6cfc3d25f23b7a441028235b23022d50553716ce7ca09d06ff84687cdfe7cde6` | 다름 |

최초 before digest `b29a617885e779681f138447952f8a5a11482e4a43970602d6d1c030dfdc1a7f`는 이 정상 순차 참조의 tick 0–17 어느 것과도 일치하지 않았다. 이를 다른 tick의 상태였다는 증거나 특정 필드가 잘못됐다는 증거로 해석하지 않는다.

정상 tick 16에서는 world digest `9cbd7a08fc913dd703f010993d269b8ae82311903ff1cded2f76169eff42f6f4`, events 119, inputs 323, observations/attempts 각 46, 진행 중 행동 0개였다. source identity는 `f29e9631f80b19d832e83ce7969318bae061a88b543424ed7254499aca91f973`다.

저장된 정상 payload를 다시 읽어 분류한 **정상 15→16 전이**에서는 inventory/movement/survival, kernel의 사건·결과·진행 중 행동·기록, driver의 inputs/observations/max_fatigue/cursor/sampled가 달라졌다. cursor는 15→16이었다. 5개 SurvivalAdvanced, 2개 ActorMoved, 3개 ItemConsumed가 추가됐으며, source compatibility와 RNG는 같았다. 이는 시간 진행으로 인한 정상 변화이고 조회의 효과가 아니다. 전체 분류와 world 값은 `normal-transition-analysis.json`에 보존했다.

따라서 **“조회 후 digest는 정상 tick 16 checkpoint와 일치한다”는 대조 자체는 직접 확인했다.** 그러나 최초 실패 로그에는 tick·session·speed·actor별 조회 위치와 실제 두 payload가 없으므로, 실패가 반드시 tick 16에서 발생했는지, 어느 session이었는지 또는 어떤 필드가 변했는지까지 역으로 확정할 수 없다. 정상 after hash와의 일치만으로 원인을 규명했다고 선언하지 않는다.

사용자가 제시한 Linux 계산은 원본 Linux 실행 로그·payload가 없는 외부 대조 결과다. 이번 독립 계산은 Windows에서 수행했다. `wsl.exe --list`는 종료 1과 설치 도움말만 반환해 사용할 Linux 배포판을 확인하지 못했으며 설치·다운로드는 하지 않았다(`linux-availability.json`). Linux 실행 자체를 다시 수행했다고 기록하지 않는다.

### 정적 경로 점검과 가설의 범위

`live.py:101`의 view는 세계 snapshot, 분리된 flight, perception/Observation preview 및 기록의 읽기 getter를 조합한다. `core/kernel.py:102`의 world snapshot은 canonical JSON 복사이고, `adapters/life.py:46`의 decisions와 `core/observations.py:77`의 content는 분리본을 반환한다. `application/concurrent.py:90`, `:118`의 공개 결과 조회에서도 기록을 갱신하는 경로를 발견하지 못했다. 이는 코드 읽기 근거이지 최초 실패 시의 mutation 부재를 증명하는 증거는 아니다.

checkpoint의 `snapshot`은 world 외에 driver/기록/정책/RNG 및 source compatibility를 포함한다(`checkpoint.py:267`). `_encode`는 dict의 insertion order를 pair 배열로 보존한다(`:162`). `_compatibility`는 호출 때마다 Python 파일 목록·내용을 읽는다(`:109`). 따라서 최초 두 payload가 없으면 world mutation, 기록·컨테이너 순서 변화, source identity 변화 또는 그 밖의 snapshot 차이를 hash 둘만으로 구분할 수 없다.

조사로 좁힌 가설과 배제 범위:

| 가설 | 확보한 근거와 한계 |
| --- | --- |
| 최초 after hash 자체가 정상 checkpoint로 나올 수 없는 손상값 | **배제**. 현재 Windows 정상 tick 16에서 정확히 같은 값이 나옴. before→after가 올바른 전이였다는 뜻은 아님 |
| 제시된 Linux hash와 Windows의 정상 tick 16 직렬화는 반드시 다름 | **이번 참조에서 배제**. 사용자 제시 hash와 Windows 독립 계산이 일치. Linux 실행 원본·다른 환경 전체의 동일성까지 검증한 것은 아님 |
| F1 테스트가 진단 부착 없이는 항상 실패함 | **이번 실행 범위에서 배제**. 비부착 전체 실행의 checkpoint 테스트 20개 구간이 통과함. 간헐적 결함이나 과거 실패의 진실성을 부정하지 않음 |
| 새 8192 소스 수정이 이번 조사 중 결과를 바꿈 | 이번 조사에서 소스·기존 테스트 수정 없음, 전체 실행 전후의 패키지 파일별 원본/정규화 hash 동일. 다만 양 끝의 일치만으로 최초 실패 당시 또는 실행 중간의 모든 파일 읽기 상태까지 증명하지는 못함 |

남은 가설은 최초 snapshot의 world/driver·기록 또는 컨테이너 순서 차이, 경계에서의 source 목록·내용 읽기 차이, 이전 테스트·프로세스 상태와 실행 조건의 간헐적 상호작용이다. 어느 것도 관측된 원인으로 승격하지 않았다. 세계 조회 getter의 alias, source compatibility 또는 환경 중 하나로 원인을 선결정할 근거가 없다. checkpoint에는 `LiveLife.speed`와 session 역할이 저장되지 않으므로 hash 일치만으로 slow/fast 중 어느 실행인지도 판별하지 못한다.

### 진단·수정 여부와 종료 조건

비부착 전체 실행이 통과했고 원인 특정도 하지 못했으므로, 이번에는 실패 분기용 최소 진단을 붙여 다시 실행하지 않았다. 추가 전체/단독 pytest, target 테스트 반복, seed 탐색도 수행하지 않았다. 실행한 것은 전체 pytest 1회와 요청한 정상 tick 16 대조용 rule session 1회이며, 저장 payload 분류는 시뮬레이션을 다시 실행하지 않는 데이터 읽기였다. 수정이 없으므로 수정 후 회귀 검증을 했다고도 기록하지 않는다.

기존 미충족 조건 중 **명령·계정·HEAD/source hash·전체 stdout/stderr·JUnit·종료 코드가 연결된 비부착 전체 통과 증거**는 이번에 충족했다. 남은 핵심 조건은 최초 불일치의 실제 두 payload·실행 문맥을 확보해 원인과 영향 범위를 설명하는 것이다. 이번 통과 결과만으로 그 증거를 대체하지 않는다.

관측한 원인이 없으므로 소스·기존 테스트·기존 진단 도구는 수정하지 않았다. assertion 완화, 비교 필드 제외, source/cache 임의 고정도 하지 않았다. 이번 정상 참조와 증거 수집 코드는 검토 대상 저장소 밖 `C:\Users\USER\Documents\JourneyMap\review-temp`에만 작성했다.

F1을 이후 다시 조사할 근거가 생기면, 원래 기존 target 테스트의 **세 session 생성 → fast speed 설정 → slow 전체 루프 → fast 전체 루프 → `(None, *ACTORS)` 조회 순서**를 유지한다. 원래 `snapshot` 한 번의 반환 payload를 그대로 보관하고 원래 digest/assertion은 유지하는 방식으로 before/after 한 쌍의 session 역할·speed·loop tick·kernel tick·조회 순서와 payload를 확보한다. 추가 pump/run_until/view로 tick 16에 건너뛰어 도달하거나 실패 당시 payload를 정상 참조에서 추정해 채우면 안 된다. 차이는 world, driver/기록·정책·컨테이너 순서, source compatibility로 나눠 검증한다. 추가 관측도 실행 조건을 바꿀 수 있음을 기록한다.

최초 실패와 모든 과거 결과는 계속 유효한 조사 기록이다. 원인 특정 또는 검증 가능한 대체 설명이 없으면 F1은 미확정으로 남긴다. 과거 실제 4096 원본 report replay의 미검증 범위도 이번 조사로 해소되지 않았다. 실제 API 호출·키 조회·승인 파일 생성·commit/push/merge는 하지 않았다. **이 문서는 유료 실행 승인이 아니다.**
