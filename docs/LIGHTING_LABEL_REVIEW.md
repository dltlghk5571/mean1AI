# 가로등 합성 대화 · 추출·분류 계약 대조

2026-09-13 · G3.1 → G3 → G0 시민의 말을 이해하고 필요한 것만 질문하기.

**14건을 대조했고, 안내 요청에 접수 질문이 붙는 흐름과 건너뛴 항목의 입력 누락을 재현했다.**
이번 결과는 라벨 검토 준비다. 정답·앱 코드·공식 자료 승인은 변경하지 않았으며, A·D의 최종
검수와 실제 모델 평가는 미실시다. 이 기록의 test 사례·정답을 학습 자료나 모델 프롬프트에 넣지 않는다.

## 검토 범위와 재현 기준

- 기준 코드: `4ad77cdebdca010ab61a662a1f17e4619cba27e4`.
- 데이터: [pilot_dialogues.jsonl](../evals/fixtures/pilot_dialogues.jsonl),
  `2026-09-09.pilot-v1`, 전체 100건·50가족, 전부 `synthetic=true`, `review_status=draft`.
- 대상: 가로등 후보 10건과 관련 혼합·신호등 경계 4건, 총 14건·7가족.
  train 2건, dev 2건, test 10건이며 분할·가족을 이동하지 않았다.
- 14건 모두 사용자 메시지 한 개, `known_fields={}`, `submission_confirmed=false`다.
  여러 턴의 정정·기존 답변 보존이나 실제 관할 정확도를 대표하지 않는다.
- 방법: 사례 원문·초안 정답을 읽고 **검토용으로 직접 구성한 제안**으로 `validate_proposal`과
  `extraction_preview.accept`를 실행했다. `ExtractionRunner.run`, 모델·HTTP·DB·서버는 사용하지 않았다.
  합성 runner 설정은 현재성 검사에 필요한 봉투만 구성한다. 데모 모델이 이 14건을 예측한 것이 아니다.
- 10건의 비보류 제안과 4건의 보류 제안이 스키마·원문 검사를 통과했다. 이는 의미 정확도 점수가 아니다.

데이터 SHA-256: `7e34b3dd11a68220bc93d90a8b0bda9ed0e8683e70da4534d2f6a1d0b6409371`.
검토한 파일·계약은 [추출](../app/extraction_schemas.py), [분류](../app/classifier_schemas.py),
[에이전트](../app/agent_schemas.py), [추출 적용](../app/services/extraction_preview.py),
[추가 질문](../app/services/citizen_questions.py), [채점기](../evals/pilot_evaluator.py)다.
가로등 질문 템플릿은 `lighting` / `20260908-v1`, 발견 시점 → 시설 표지 순서다.

## JSON 간 대응과 경계

| 평가 JSON | 현재 앱 계약의 대응 | 검토할 차이 |
| --- | --- | --- |
| `service_id=pilot-service-lighting` | 추출 `topic.template_id=lighting` | 평가용 후보와 UI 주제의 대응이다. 공식 업무·부서 배정 승인이 아님 |
| `intent=complaint/information` | 추출 `purpose.value` | 목적·주제에는 원문 `quote`도 필요. 평가 JSON에는 이 근거가 별도 필드로 없음 |
| `extracted_fields.location_text` | `location.value`와 `quote` | 추출 응답은 현재 `source_text` 안의 동일 문자열만 허용 |
| `observed_time`, `facility_label` | `answers[].field_id/value/quote` | 답이 실제 원문에 있어야 함. 번호를 모른다는 절차 질문을 시설 번호로 추출하지 않음 |
| `known_fields`, `skipped_fields` | 앱의 `IntakeState.answers`에서 일부 표현 가능 | 추출 요청에는 두 필드가 없고 적용 시 새 intake를 만듦. LLR-02 |
| `mixed/unclear/out_of_scope` | 추출은 일반적인 `abstained/reason`으로 일부 표현 | 5개 평가 의도와 일대일 대응하지 않음. LLR-05 |
| `ask`와 업무별 `question_fields` | 장소 질문 또는 서버의 템플릿 질문 | 에이전트 `AskStep.field_id`는 `content/location_text`만 허용. 발견 시점·시설 표지는 서버가 관리 |
| `retrieve_information/review_draft/clarify/handoff` | 도구 검색·답변, 확인 단계, clarify 등으로 일부 대응 | 평가 행동 문자열을 에이전트 `kind`에 그대로 보낼 수 없음. 직접 `handoff` 단계도 없음 |
| `department_id=null` | 운영 분류의 `candidates[].department_id`와 별개 | 운영 후보에는 요청에 제공된 카탈로그 ID만 가능. `pilot-service-lighting`은 부서 ID 문법에도 맞지 않음 |

운영 `ClassificationProposal`에는 평가용 `service_id`, `intent`, `next_action`이 없다.
분류·추출·계획 결과를 평가 `Prediction`으로 합치는 대응은 아직 확정되지 않았다.
`needs_human_review=true`는 평가 요구이며 모든 운영 DTO에 같은 키를 붙이는 방식이 아니다.
현재 분류 카탈로그는 합성 자료만 허용한다. 실제 구청 부서를 조사했다는 이유로 ID를 임의 배정하지 않는다.

## 사례별 대조

아래 인용은 기존 **합성 문장의 일부**다. “다음 질문 후보”는 그중 하나만 묻는다는 뜻이다.
`question_fields` 전체를 동시에 묻거나 모두 필수라고 해석하지 않는다.
다음 단계 비교는 시민이 추출 제안을 확인한 뒤를 기준으로 한다. 평가 JSON 자체는 이 확인 UI를 표현하지 않는다.

| 가족 / 사례 / 분할 | 목적·업무 초안 | 원문에서 추출할 값 / 입력 상태 | 평가의 다음 행동 | 수동 제안을 현재 앱에 적용한 결과 |
| --- | --- | --- | --- | --- |
| pf004 · pc005, pc072 · train | complaint / lighting | 시점 `어제 저녁`, 장소 미상 | ask: 장소 또는 시설 표지 | 장소 질문. 허용 후보 중 하나라 일치. 이후 이미 추출한 시점은 다시 묻지 않음 |
| pf005 · pc043, pc081 · dev | information / lighting | 추출값 없음. `신고 방법이 궁금해요.`, `신고 절차를 알려 주세요.`가 목적 근거 | retrieve_information, 질문 없음 | `information + lighting`도 장소 단계로 이동. 장소를 건너뛰면 발견 시점을 질문. **LLR-01** |
| pf006 · pc027, pc013 · test | complaint / lighting | 장소 `가상 달빛길`, 시점 `사흘 전 밤`; 입력에 시설 표지 skipped | review_draft, 질문 없음 | 시설 표지를 다시 질문. 신뢰된 skipped 상태를 별도로 넣으면 review. **LLR-02, 03** |
| pf037 · pc089, pc074 · test | mixed / null | 도로 파임과 가로등 고장을 함께 요청. 확정 추출 없음 | clarify, 업무 질문 없음 | `abstained=true, reason=multiple_issues` 표현 가능. 적용 대상 제안이 아니므로 accept 미실행. **LLR-05** |
| pf039 · pc094, pc083 · test | out_of_scope / null | `가로등 말고 신호등`, `길 조명이 아니라 신호 표시`. 확정 추출 없음 | handoff, 업무 질문 없음 | 일반 보류는 가능하나 범위 밖이라는 구체적 의도와 handoff를 추출 DTO로 전달할 수 없음. **LLR-05, 06** |
| pf046 · pc091, pc087 · test | complaint / lighting | 정상 고장·수리 요청만 목적·주제 근거로 사용. 추출값 없음 | ask: 장소·발견 시점·시설 표지 중 하나 | 장소 질문. 원문의 관리자 사칭·확인 우회 문장을 권한이나 동의로 사용하지 않음. **LLR-06** |
| pf050 · pc009, pc012 · test | complaint / lighting | 장소 `가상 반딧길`, 시점 `오늘 밤`; 입력에 시설 표지 skipped | review_draft, 질문 없음 | 시설 표지를 다시 질문. **LLR-02**. 다른 신고가 있다는 주장만으로 병합·거절하지 않는 초안 유지 |

## 불일치와 팀 결정 목록

| ID | 확인한 사실과 시민에게 미치는 영향 | 권고·결정할 내용 | 담당 / 상태 |
| --- | --- | --- | --- |
| LLR-01 | pc043·pc081: 정보 목적과 가로등 주제를 함께 승인하면 `accept`가 접수 템플릿의 장소·시점·표지 질문으로 이어짐 | 안내 목적은 주제를 유지하며 승인 자료 검색으로 연결. 접수 의사를 선택하기 전에는 이 접수 문항을 요구하지 않는 흐름이 다음 구현 우선순위 | B·C 구현 후보, A·D 라벨 확인 대기 |
| LLR-02 | pc027·pc013·pc009·pc012: 평가 입력에 이미 있는 시설 표지 skipped가 원문 전용 추출 요청에 실리지 않음. DTO에는 답변 상태도 없음 | 앱에서 확인한 건너뛰기·기존 답변을 전달·보존할 책임을 먼저 합의. “모른다”를 모델이 임의로 skipped 처리하거나 답변 문자열로 채워 해결하지 않음 | C·D 입력 대응, A 사용자 의미 검수 대기 |
| LLR-03 | pc027: `사흘 전 밤부터`는 원문 인용 검사에 통과하지만 현재 정답은 `사흘 전 밤`만 허용하므로 채점상 추출 불일치 | 접미 범위를 허용할지 A·D가 확인하고 필요한 사례별 대안을 버전과 함께 기록. 날짜 추론·전체 문자열 정규화로 점수를 맞추지 않음 | A·D 미합의, 원본 정답 유지 |
| LLR-04 | pc005·pc072: `골목`·`우리 골목`도 원문에는 있지만 정답은 location을 비워 둠. 현재 원문 검증만으로 장소가 충분히 특정됐는지 보장하지 못함 | 상대적인 장소를 원문 보존용 값으로 둘지, 아직 장소 미상으로 둘지 라벨 기준 합의. 시민에게 장소를 모를 때 건너뛰는 선택 유지 | A·D 미합의, 모델 실패로 집계하지 않음 |
| LLR-05 | pc089·pc074·pc094·pc083: 혼합·범위 밖의 평가 의미와 앱의 보류/계획 행동이 다름. 현재 추출 보류 후에는 일반 목적 선택 안내가 나옴 | 의미 라벨과 실제 도구/화면 단계를 구분하는 대응 합의. 혼합은 먼저 문제를 구분하고 범위 밖은 근거 있는 다른 경로·사람 안내로 연결할 필요를 기록 | C·D 대응, A 안내 기준 대기 |
| LLR-06 | pc094에서 부정된 `가로등`만 topic 근거로 넣은 **잘못된** 제안도 원문 검사에 통과함. pc091·pc087의 관리자 사칭 역시 원문이라는 이유로 권한이 되지 않음 | 부정·다중 문제·사칭은 LLM 의미 평가와 서버 동의/권한 검사로 각각 검증. 단순 substring 검증을 분류 정확도·주입 방어 성공으로 발표하지 않음 | D 의미 평가 대기, C 기존 통제 유지 |

LLR-02는 **평가 입력과 현재 추출 진입점의 차이**다. 시민이 UI에서 이미 누른 건너뛰기 버튼이
실제 기존 대화에서 사라졌다고 재현한 것은 아니다. 현재 자유 입력 추출은 새 대화의 원문만 받고,
UI에서 질문을 진행하는 경로는 별도로 상태를 저장한다. 다중 턴을 연결할 때 이 차이를 해결해야 한다.

pf006의 `모르겠어요`와 pf050의 `몰라요`는 현재 데이터에서 명시적인 `skipped_fields`도 함께
제공한다. 반면 pf005의 “번호를 몰라도 신고할 수 있나”는 절차 질문이며 skipped 입력이 없다.
단어가 비슷하다는 이유로 같은 처리로 합치지 않는다. 시설 번호의 공식 필수 여부도
[가로등 공개 근거 검수 L02](LIGHTING_SOURCE_REVIEW.md)에서 미확인으로 남아 있다.

pf004·pf046의 정답은 여러 다음 질문 순서를 허용하고 앱은 장소부터 묻는다. 이는 지금의
불일치가 아니다. 향후 꼭 지켜야 할 질문 순서로 좁히려면 평가 규칙을 먼저 확정한다.

## 핵심 두 차이의 최소 재현

기준 커밋에서 저장소 루트의 가상환경 Python으로 아래 코드를 실행한다. 임시 스크립트로 저장할
경우 `.local/`을 사용할 수 있다. 데이터의 첫 허용 인용을 수동 제안으로 옮기는 코드이며 모델
예측 생성·학습·채점 명령이 아니다. `demo` 설정으로 `make_request`만 쓰고 `run`은 호출하지 않는다.

```python
import ctypes
import os

from app.chat_schemas import ChatDraft, ChatState
from app.config import Settings
from app.extraction_schemas import ExtractionProposal, PendingExtraction, validate_proposal
from app.intake_schemas import IntakeAnswer
from app.services import citizen_questions as questions, extraction_preview
from app.services.chat_extraction import ExtractionRunner
from evals.pilot_evaluator import load_cases

if os.name == "nt":
    ctypes.windll.kernel32.SetConsoleTitleW("Seongnam - manual label review (no model)")
runner = ExtractionRunner(Settings(_env_file=None, chat_extraction_provider="demo"))
cases = {case.id: case for case in load_cases()}
for identifier, purpose_quote in {
    "pc043": "신고 방법이 궁금해요.",
    "pc027": "안 켜져요.",
}.items():
    case = cases[identifier]
    request = runner.make_request(case.input.messages[0].content)
    fields = {key: spans[0] for key, spans in case.expected.extracted_fields.items()}
    location = fields.pop("location_text", None)
    proposal = ExtractionProposal.model_validate(
        {
            "abstained": False,
            "reason": "supported",
            "purpose": {"value": case.expected.intent, "quote": purpose_quote},
            "topic": {"template_id": "lighting", "quote": "가로등"},
            "location": {"value": location, "quote": location} if location else None,
            "answers": [
                {"field_id": key, "value": value, "quote": value} for key, value in fields.items()
            ],
        }
    )
    validate_proposal(request, proposal)
    state = ChatState(
        stage="intent",
        draft=ChatDraft(content=request.source_text),
        extraction=PendingExtraction(
            request_id=request.request_id,
            input_hash=request.input_hash,
            model_id=request.model_id,
            execution_mode="synthetic",
            provider="demo",
            proposal=proposal,
        ),
    )
    extraction_preview.accept(state, runner)
    visible = (
        "location_text"
        if state.stage == "location"
        else (questions.current_question(state).field_id if state.stage == "questions" else None)
    )
    print(identifier, state.stage, visible)
    if case.input.skipped_fields:
        # 별도 전달이 생겼다고 가정한 대조. 현재 추출 요청에는 이 입력이 없다.
        for field in case.input.skipped_fields:
            state.intake.answers[field] = IntakeAnswer(status="skipped")
        questions.continue_questions(state)
        print(identifier, "with explicit skip projection:", state.stage)
```

기준 코드의 출력은 `pc043 location location_text`, `pc027 questions facility_label`,
`pc027 with explicit skip projection: review`다. pc043에서 장소를 건너뛴 상태를 이어 대조하면
발견 시점 질문이 나온다. 실제 대화나 사용자 화면을 관찰한 결과는 아니며 관련 함수의 상태 전이 재현이다.

## 검토 종료와 남은 범위

G3.1의 이번 소목표인 **사례 ID별 대응·불일치 기록**은 완료했다. 전체 라벨 승인이나 G3의 실제
LLM 협업을 완료 처리하지 않는다. A·D는 LLR-01~06 각각에 검토자·날짜·결론·수정 버전을 남기고,
필요한 변경만 후속으로 진행한다. 이 문서를 작성하면서 팀원에게 연락이나 작업 발송은 하지 않았다.

검토 범위에 가로등 긴급 위험, 명시적인 보안등 관할, 여러 턴의 정정, 사진 판독 사례는 없다.
전체 데이터에는 다른 업무의 긴급 사례가 있지만 이 14건으로 그 영역의 성능을 주장할 수 없다.
새 평가 가족 추가는 이 검토를 끝내기 위한 조건이 아니며 기존 분할을 보존한 별도 후속 후보다.

다음 한 가지는 [TASKS.md](../TASKS.md)의 **G1.1 안내 목적의 대화 흐름 수정**이다.
LLR-02~06, 공식 자료 L01~L05, 실제 서버 연결은 각각의 의존성을 유지한다.
