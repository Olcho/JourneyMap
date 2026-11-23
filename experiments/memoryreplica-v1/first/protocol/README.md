**MemoryReplica v1 실험 기준과 검증 자료**

새 제목과 연구 범위는 MemoryReplica_Protocol_v1_2026-11-05.md에 있습니다.

이 묶음은 기존 JourneyMap 엔진으로 생성한 과거 경험과 조건별 입력을 포함합니다. LLM의 응답이나 실제 LLM 성능 결과는 포함하지 않습니다. reference_only라는 이름의 값은 입력 그래프의 프로그램 계산값입니다.

Python 3.12 이상과 기존 실행 통합본의 research/src가 필요합니다.

```bash
python validate_design.py --source-src /absolute/path/to/research/src --phase development --output local_development.json
python validate_design.py --source-src /absolute/path/to/research/src --phase main --output local_main.json
```

기존 E04/Actual 원본은 수정하지 않습니다. 현재 검증한 범위는 과거 행동의 엔진 실행, 임무 결과 분류, 기억 선택, 조건별 첫 입력, 지도 기준 점수와 제한을 지키는 기준 경로입니다. 유료 호출 연결과 새 임무의 전체 행동 루프는 이 패키지의 완료 범위가 아닙니다.

main_inputs.json 안의 world_truth와 reference_only 값은 연구자 전용입니다. 이 파일 전체를 모델에게 보내면 안 됩니다. 모델 입력은 해당 사례의 contexts[정책]와 prompts.md의 지시문으로만 구성합니다.

파일 서명은 freeze_manifest.json에 있습니다. 연구 주제와 입력을 고정하는 단순 기록이며 별도 운영 시스템이 아닙니다.
