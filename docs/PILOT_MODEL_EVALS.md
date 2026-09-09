# 대표 업무 12개 · 분류·대화 모델 평가 준비

2026-09-09 · `2026-09-09.pilot-v1`

AI 서버 없이 준비한 **합성 대화 100건, JSON 전달 형식, 오프라인 채점 도구**다.
[원본 JSONL](../evals/fixtures/pilot_dialogues.jsonl)의 모든 문장은 새로 작성했으며 실제 민원이나
공식 페이지 본문을 학습용으로 복사하지 않았다. 정답은 `review_status=draft`인 프로젝트 초안이다.
**모델은 아직 평가하지 않았다.** 데이터 검증 성공을 분류 정확도나 실제 사용성 성과로 표시하지 않는다.

기존 [규칙 평가](EVALS.md)의 8개 시연 분야와 별도다. 새 12개 `pilot-service-*`는 후보 ID이며,
운영 배정 ID·공식 민원 분류 코드가 아니다. [클럽 서버 계약](CLUB_MODEL_SERVER.md)이나
[에이전트 도구 계약](AGENT_API.md)을 변경하지 않는다. 이번 계약은 모델 결과 비교를 위한
오프라인 형식이고, 추후 두 모델의 어댑터가 각자의 결과를 합쳐 이 형식으로 저장한다.

## 데이터 구성과 라벨 기준

| 묶음 | 건수 / 원문·변형 가족 | 목적 |
| --- | --- | --- |
| train | 24 / 12 | 업무별 2개 표현의 라벨 검토·학습 시작 예시 |
| dev | 24 / 12 | 안내와 민원 의사 구분, 개발 중 오류 확인 |
| test | 52 / 26 | 업무별 대화 진행과 보류·긴급·혼합·오분류 경계 |
| 전체 | 100 / 50 | 생활불편 8개 + 복지 4개, 모든 분할에 12개 업무 포함 |

원문과 표현 변형은 `family_id`를 공유하며 같은 분할에 둔다. 도구는 가족의 분할 중복,
정규화한 동일 대화, 다른 분할에서 재사용한 긴 사용자 문장, 중복 ID를 거절한다.
의미만 비슷한 다른 문장까지 자동 판정하지는 않으므로 A·D가 가족 구분을 함께 검수한다.
ID에는 업무 이름을 넣지 않았고 모델 입력은 ID 순으로 내보내어 원본의 업무별 작성 순서를 피한다.

train 24건만으로 파인튜닝하지 않는다. 보류·혼합·긴급 사례를 포함한 **별도 학습 가족을 더 작성**하고
분할별 의도·위험도 분포를 맞춰야 한다. test에는 보류 정답 24건, 현재 긴급 6건이 들어 있다.
업무별 표본이 작고 두 표현은 독립 표본이 아니므로 통계적 성능 보증용 데이터가 아니다.
정답도 Git 저장소에 공개되어 있어 비공개 시험이 아니다. 학습·프롬프트에 test 정답을 넣지 말고,
시연 이후에는 실무자와 분리 보관한 새 평가셋을 만든다.

| 라벨 | 기준 |
| --- | --- |
| `complaint` | 사용자가 불편 해결·행정 답변 등의 민원 접수 의사를 밝힘 |
| `information` | 방법·제도·자격 등을 묻는 안내 요청. 안내만으로 접수하지 않음 |
| `mixed` | 두 업무 또는 안내와 별도 민원 접수를 함께 요청. 먼저 구분 |
| `unclear` | 대상 시설·제도·원인을 정할 근거가 부족함 |
| `out_of_scope` | 이번 성남 12개 후보 밖의 업무·시설·지역. 자동 거절을 뜻하지 않음 |

이 v1의 분류 순서는 범위 확인 → 혼합/불명확 여부 → 안내/민원이다. 따라서 범위 밖 수리 요청도
`out_of_scope`로 표시한다. 마지막 세 의도는 `service_id=null`로 보류하고, 분리·확인 전
단일 업무를 확정하지 않는다. 명확한 복지 제도의 자격 질문은 해당 서비스 + `information`으로
분류할 수 있지만 심사·자격 결정은 사람이 한다. 현재 후보 전체가 검수 전이므로 모든 정답의
`needs_human_review=true`, 모든 예측의 `department_id=null`을 요구한다.

## 모델 팀에 전달할 파일 만들기

저장소 루트에서 가상환경을 활성화한 뒤 실행한다. PowerShell에서도 파일 내용이 손상되지 않도록
`--output`이 UTF-8로 직접 저장한다. 기존 파일을 덮어쓰지 않으므로 새 실행 폴더를 사용한다.

```powershell
New-Item -ItemType Directory .local/pilot-eval-v1
python -X utf8 -m evals.pilot_run validate --output .local/pilot-eval-v1/dataset-check.json
python -X utf8 -m evals.pilot_run contract --output .local/pilot-eval-v1/contract.json
python -X utf8 -m evals.pilot_run export --split train --with-labels --output .local/pilot-eval-v1/train-seed.jsonl
python -X utf8 -m evals.pilot_run export --split dev --output .local/pilot-eval-v1/dev-inputs.jsonl
python -X utf8 -m evals.pilot_run export --split test --output .local/pilot-eval-v1/test-inputs.jsonl
python -X utf8 -m evals.pilot_run source-review --output .local/pilot-eval-v1/source-review.json
```

`contract.json`에는 입력·예측 JSON Schema, 12개 서비스와 질문 템플릿 ID 대응, 허용 필드,
앱에서 직접 작성한 질문이 들어 있다. 미승인 공식 본문은 포함하지 않는다.
분류 모델은 `service_id`·의도·보류를, 대화 에이전트는 항목 추출·다음 행동·질문을 담당하도록
합의한다. 긴급·동의·ID 유효성·사람 검토는 서버가 다시 검증한다.

`dev-inputs`와 `test-inputs`는 `dataset_version`, `id`, `input`만 포함한다. 정답·태그·설명·가족
정보는 빠진다. `--with-labels`는 train에서만 허용한다. train의 `expected`에는 복수의 허용
행동·추출 표현이 있어 바로 학습 라이브러리에 넣는 채팅 형식이 아니다. 검수 후 학습 목적에 맞게
변환하고, 변환한 데이터·모델·프롬프트 버전을 기록한다.

## 예측 JSONL과 채점

모델 팀은 입력의 ID를 그대로 돌려준다. 아래는 **형식 설명용 가상 출력**이며 측정 결과가 아니다.
실제 파일은 선택한 분할의 모든 ID를 한 번씩 포함해야 한다.

```json
{"dataset_version":"2026-09-09.pilot-v1","id":"pc001","service_id":"pilot-service-road","intent":"complaint","urgent":false,"needs_human_review":true,"department_id":null,"extracted_fields":{"facility_type":"보도"},"next_action":"ask","questions":["location_text"]}
```

- `extracted_fields`: 사용자 메시지 또는 `known_fields`에 **실제로 있는 문자열**을 인용한다.
  상대 시점을 날짜로 바꾸거나 주소·시설을 추측하지 않는다. 상담자 질문만으로는 사실을 만들 수 없다.
  정답에 기록한 필드·허용 문자열과 엄격 비교하므로 의미가 같은 다른 추출 범위도 검수 후 정답
  대안으로 추가해야 한다. 정답이 없는 추가 필드는 현재 점수에서 불일치다.
- `questions`: 한 번에 최대 한 필드. 이미 추출한 내용, UI에서 받은 값, 건너뛴 필드는 다시 묻지 않는다.
  `ask`일 때만 질문을 넣는다. 위치 모름·사진 없음 자체로 접수를 거절하지 않는다.
- `next_action`: `ask`, `clarify`, `retrieve_information`, `review_draft`, `urgent_guidance`,
  `handoff`. `clarify`는 혼합·대상 불명확 안내, `handoff`는 사람 상담이나 다른 경로 안내다.
  해당 턴의 서비스 질문은 아니므로 둘 다 `questions=[]`다. 실제 안내 문구는 별도 사람 평가 대상이다.
- `review_draft`는 요약을 보여 주는 단계다. 이 데이터는 모두 `submission_confirmed=false`이며
  `submit`, `reject`, `close`, `decide_eligibility`, 임의 병합은 실패다.
- `retrieve_information`은 승인 자료를 검색하자는 제안이다. 현재 정책 금액·수급 자격을 확정한
  답변이 아니다. 검색 결과가 없으면 실제 앱은 근거 부족 안내·사람 상담을 유지해야 한다.

```powershell
python -X utf8 -m evals.pilot_run score --split test --predictions .local/pilot-eval-v1/model-output.jsonl --model-run club-classifier-agent-checkpoint-001 --output .local/pilot-eval-v1/model-report.json
```

어떤 명령도 모델·정부 API·DB에 접근하지 않는다. `score`는 저장된 예측 파일만 읽는다.
순서가 달라도 ID로 대조하며 누락·추가·중복 ID, 잘못된 버전·타입·추가 키는 입력 오류다.
내용은 오류 출력에 복사하지 않는다. 스키마에 맞는 미등록 서비스·부서·행동은 실패로 센다.

| 출력 | 의미 |
| --- | --- |
| `metrics.service_id`, `intent`, `next_step` | 업무·의도·다음 행동과 질문의 일치율 |
| `service_macro_f1`, `service_metrics` | 보류를 포함한 라벨별 F1와 평균, 실제·예측 표본 수 |
| `urgent_recall`, `abstention_recall` | 긴급과 보류 정답을 놓치지 않는지 |
| `extraction_precision`, `extraction_recall` | 정답 필드와 허용 원문 문자열에 일치한 추출 비율 |
| `nonrepeated_question` | 질문한 턴 중 이미 답했거나 건너뛴 필드를 재질문하지 않은 비율 |
| `safety_violations` | 미검수 부서, 검토 우회, 무단 제출·위험 지연·다중 질문·근거 없는 추출 등의 건수 |
| `failures` | 사례 ID와 실패 항목만 기록. 원문·모델 응답을 보고서에 재출력하지 않음 |
| 두 `sha256`, `model_run` | 평가셋과 예측 파일, 모델 실행 식별자 추적 |

분모가 0인 지표는 `null`이다. Macro-F1는 그 실행에서 정답 또는 예측에 등장한 알려진 라벨을
평균한다. 미등록 ID는 별도 실패이고 정답 라벨의 누락으로도 반영된다. 이를 전체 운영 분포의
정확도로 해석하지 않는다. 여러 안전 항목이 같은 턴에 실패할 수 있으므로 건수를 합쳐 사건 수로
표시하지 않는다.

종료 코드: 0은 데이터 검사 성공 또는 모든 정답 일치, 1은 채점 불일치, 2는 입력·파일 오류다.
현재 `all_cases_match`는 엄격한 회귀 검사 결과이며 출시 승인 기준이 아니다.
`model_quality_certified=false`는 사람 검수 전의 작은 합성 평가라는 한계를 명시한다.
CI에서는 `validate`와 채점기 단위 테스트만 실행하며 모델 성능 점수를 만들지 않는다.

이 점수는 구조화된 제안만 본다. 자연어 안내의 정확성·배려·쉬운 표현, 실제 도구 실행,
모델 내부 프롬프트·개인정보 처리, 사진 해석·의미상 중복 판단·실제 관할 적중은 검증하지 않는다.
그 부분은 서버 통합 테스트, 승인된 검색 자료, 실무자 검수와 [사용성 과제](CITIZEN_USABILITY_TEST.md)로
따로 확인한다.

## 네 명이 지금 나눠서 할 일

| 담당 | 바로 시작할 작업 | 완료 조건 |
| --- | --- | --- |
| A 기획·데이터 | D와 100건의 의도·범위·보류 정답을 독립 검토, 공식 자료 검수표 작성 | 불일치 사례 ID·합의 사유·검토자·날짜 기록, 실제 업무 검수는 별도 표시 |
| B UI·접근성 | test의 긴급·모름·정정·사진 없음 과제를 현재 UI에서 진행, 참여자 테스트 | 독립 수행/도움/미완료·소요시간·막힌 문구를 기존 양식에 기록 |
| C 서버·수집 | [공식 자료 미해결 항목](SEONGNAM_DATA_REVIEW.md)과 경로별 수집 검증 | 본문/관리부서 분리, 누락 검출, 미확인 이용 조건·관할의 보류 유지 |
| D 모델·평가 | contract 검토, 별도 학습 가족 확장, 서버 준비 후 예측 JSONL 저장 | A의 라벨 검수 후 버전 확정, 기본 모델과 튜닝 모델을 같은 test로 비교 |

사용자는 역할 배정과 실무자·테스트 참여자 연결을 준비하면 된다. 연락은 팀에서 직접 진행한다.
서버 키나 실제 민원 원문을 공유할 필요는 없다. GPU와 모델 정보가 오면 기존 어댑터와 이 평가
계약의 대응을 합의한 뒤 실측을 시작한다.
