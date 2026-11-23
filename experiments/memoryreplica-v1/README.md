# MemoryReplica v1 오프라인 실행과 집계

`first/`와 `followup/`은 서로 다른 60세계의 동결 패키지다. `protocol/`의 입력·프롬프트와 함께 실행된 `run_v1.py` 및 `memoryreplica_v1/`의 원문을 유지한다. `source-sha256.json`은 두 실행의 연구 소스, Actual 소스, 패키지 파일 식별자를 기록한다.

- `memoryreplica_v1/adapter.py`는 Engine 관찰과 N/S/F/M/R 기억 입력을 조립하고 저장 응답으로 다음 행동을 재생한다.
- `memoryreplica_v1/evaluate.py`는 방향성 도로 사실의 지도 F1, 제한 내 최저 경로 비용과 실제 행동의 효율 E 계산을 담당한다.
- `memoryreplica_v1/analysis.py`는 세계별 대응 비교와 계열별 층화 부트스트랩을 계산한다.

저장소 루트에서 아래 명령에 연구 가상환경의 Python 3.12를 사용한다. 예시는 Bash 기준이다.

```bash
for batch in first followup; do
  python -I -B -X utf8 "experiments/memoryreplica-v1/$batch/run_v1.py" \
    --source-src src --protocol-dir "experiments/memoryreplica-v1/$batch/protocol" \
    --phase main --output "trials/memoryreplica-$batch-prepared.json" prepare
done

MR_V1_SOURCE_SRC="$PWD/src" \
MR_V1_PROTOCOL_DIR="$PWD/experiments/memoryreplica-v1/first/protocol" \
PYTHONPATH="$PWD/experiments/memoryreplica-v1/first" \
  python -B -X utf8 -m unittest discover -s experiments/memoryreplica-v1/tests -p test_v1.py
```

`prepare`는 각 배치의 지도 첫 요청과 행동 첫 요청 300쌍을 구성한다. 동결 실행기의 `bridge`, `advance`, `summarize`는 이미 저장된 응답을 사용한다. 지도 패널을 생성하는 선택적 분석 함수에는 matplotlib가 필요하다. 위 준비·테스트·집계 명령은 Python 표준 라이브러리를 사용한다.

## 기존 실제 평가 집계

저장소에는 [조건별 표](results/conditions.csv)와 [대응 비교 표](results/contrasts.csv)가 있다. 기록 보유자는 첫 평가의 `research-results.json`과 독립 후속 실행의 SQLite를 읽기 전용으로 집계할 수 있다. `--output`에는 새 디렉터리를 지정한다.

```bash
python -I -B -X utf8 experiments/memoryreplica-v1/summarize_recorded.py \
  --first-results /path/to/research-results.json \
  --followup-db /path/to/probe-runs.sqlite3 \
  --output trials/memoryreplica-recomputed
```

집계는 120개 고유 세계와 각 세계의 5개 완료 점수를 확인한다. 별도로 중단된 후속 실행은 제외한다. 공개 표를 재계산할 때는 커밋된 채점과 동결 입력을 사용한다. 전체 원응답 재감사에는 별도 보존 자료가 필요하다. 새 모델 요청은 발생하지 않는다.
