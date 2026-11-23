# Research References to Investigate

이 목록은 JourneyMap 설계를 정당화하거나 복제할 청사진이 아니라, 구현과 실험 설계 전에 읽고 비교할 조사 항목이다. 각 시스템의 최신 논문·공식 구현과 한계를 직접 확인한 뒤 채택 여부를 결정한다. JourneyMap은 아래 시스템을 wholesale로 재현하려 하지 않는다.

| 연구/시스템 | 조사할 아이디어 | JourneyMap에 던지는 질문 | 그대로 복사하지 않을 점 |
|---|---|---|---|
| TextWorld | 텍스트 기반 환경의 상태, admissible command, benchmark 구성 | 구조화 action과 deterministic fixture를 어떻게 평가할까? | 게임 생성 범위와 텍스트 parser가 연구 목적 자체는 아니다. |
| ALFWorld | 언어 지시와 embodied task 환경의 결합 | 관찰·행동 grounding과 task success를 어떻게 측정할까? | 가정 환경이나 사전 정의 task suite를 복제하지 않는다. |
| ReAct | reasoning/acting interleave와 tool-use trace | Observation→결정→ActionRequest trace를 어떤 실험 조건으로 기록할까? | 자유 형식 reasoning이 engine 권위가 되게 하지 않는다. |
| Generative Agents | memory stream, reflection, planning, 사회적 확산 | MemoryPolicy와 정보 전파 실험 변수를 어떻게 분리할까? | 다수 LLM NPC와 고비용 전체 아키텍처는 0.1 범위가 아니다. |
| Voyager | skill library, 자동 curriculum, 장기 탐색 | 장기 행동의 재사용·학습을 후속 실험으로 볼 가치가 있는가? | Minecraft-specific affordance나 self-modifying skill 실행을 도입하지 않는다. |
| Reflexion | 언어적 feedback과 episodic reflection | 실패 후 reflection이 행동 품질에 미치는 효과를 별도 memory condition으로 측정할까? | reflection 텍스트를 사실이나 canonical state로 취급하지 않는다. |
| AgentBench | 여러 환경에서 agent 능력 평가 | 규칙 준수, 회복, 장기 진행의 metric을 어떻게 정의할까? | 광범위 benchmark coverage보다 한 환경의 내부 타당성을 우선한다. |
| SOTOPIA | 사회적 상호작용 시나리오와 평가 | ASK/INFORM/REQUEST, 사회 목표와 평가 편향을 어떻게 설계할까? | LLM 기반 일반 NPC population이나 evaluator 의존을 기본값으로 두지 않는다. |
| Concordia | generative social simulation의 component 구성 | entity/component 경계와 agent/environment 책임을 어떻게 비교할까? | Concordia API나 component model을 그대로 채택하지 않는다. |
| MemGPT 및 장기 기억 연구 | 계층적 기억, context 관리, 회수 정책 | NoMemory/recent/episodic/reflection을 교체 가능한 실험 변수로 어떻게 통제할까? | memory 내용을 World Truth와 합치거나 Controller에 강결합하지 않는다. |

## 문헌 검토 시 기록할 항목

각 참고 자료마다 다음을 남긴다.

- 정확한 서지 정보, 공식 URL/저장소와 확인한 version/date
- 환경이 agent에게 제공하는 정보와 숨기는 정보
- action이 환경 상태를 바꾸는 권한 모델
- deterministic replay 가능 여부와 randomness 처리
- memory, planning, reflection의 위치와 실험 변수 분리 여부
- 평가 metric, baseline, 알려진 한계와 재현 비용
- JourneyMap에 적용할 후보, 거부할 요소와 그 근거

## 현재의 적용 원칙

- 선행 연구에서 prompt pattern보다 환경의 정보·권한 계약을 먼저 비교한다.
- LLM 성능 향상 기법은 engine correctness와 분리된 실험 조건으로 취급한다.
- 외부 benchmark와의 비교는 0.1 vertical slice의 내부 불변식이 검증된 뒤 진행한다.
- 논문 아이디어를 적용할 때는 라이선스와 공식 구현의 provenance를 별도로 확인한다.
