# 가로등 합성 대화 · 추출·분류 계약 대조

2026-09-13 · G3.1 → G3 → G0 시민의 말을 이해하고 필요한 것만 질문하기.

**14건을 대조했고, 안내 요청에 접수 질문이 붙는 흐름과 건너뛴 항목의 입력 누락을 재현했다.**
원래 대조 작업에서는 정답·앱 코드·공식 자료 승인을 변경하지 않았으며, A·D의 최종
검수와 실제 모델 평가는 미실시다. 이 기록의 test 사례·정답을 학습 자료나 모델 프롬프트에 넣지 않는다.

후속 G1.1에서 **LLR-01의 앱 흐름 수정과 회귀 검증을 완료**했다. 정보 목적의 생활불편 주제는
접수 질문 없이 자료를 검색하고, 추후 시민이 접수를 선택하면 질문을 이어간다. 아래 표는 기준
커밋에서의 발견 기록이다. 후속 **LLR-02의 실제 UI 상태 대조도 완료**했으며, 확인한 이어보기·수정
경로에서는 답변·건너뛰기 손실이 재현되지 않았다. **LLR-03의 두 사례·8개 인용 후보 검수안도
작성**했다. 원문 일치와 정답 일치를 구분하고 사례별 허용 후보를 제안했다. 평가 입력 대응 합의,
LLR-03 정답 반영 결정과 A·D의 라벨 검수는 여전히 대기다. **LLR-04는 두 사례의 장소 제안
5개·합성 후속 흐름 6개를 대조**해 모호한 장소의 처리 기준안을 작성했다. 기준·문구·상태 적용의
최종 합의와 LLR-05~06은 대기다.

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
| LLR-01 | pc043·pc081: 기준 커밋에서 정보 목적과 가로등 주제를 함께 승인하면 접수 템플릿의 장소·시점·표지 질문으로 이어짐 | 후속 G1.1에서 안내 목적의 생활불편 접수 문항을 보류하고 자료 검색으로 연결. 접수 전환 시에만 필요한 문항을 재개하며 복지 안내 질문은 유지 | 앱 수정·API/브라우저 회귀 검증 완료, A·D 라벨 확인 대기 |
| LLR-02 | pc027·pc013·pc009·pc012: 평가 입력에 이미 있는 시설 표지 skipped가 원문 전용 추출 요청에 실리지 않음. DTO에는 답변 상태도 없음 | 후속 UI 대조에서는 저장 상태 손실 없음. 평가에 앱 상태를 전달할 책임을 합의. “모른다”를 모델이 임의로 skipped 처리하거나 답변 문자열로 채워 해결하지 않음 | UI 대조·회귀 검증 완료. C·D 입력 대응, A 사용자 의미 검수 대기 |
| LLR-03 | pc027의 `사흘 전 밤부터`, pc013의 `사흘 전 밤에`는 원문 검사를 통과하지만 현재 정답은 `사흘 전 밤`만 허용. `밤`도 원문에는 있어 의미 충분성과 구분 필요 | 후속 검수안에서 사례별로 원래 정답과 문장에 실제 있는 조사 포함 표현을 함께 허용하도록 권고. 조사 일괄 삭제·부분 문자열만으로 정답 처리하지 않음 | 두 사례·8개 후보 대조 완료. A·D 결정 대기, 원본 정답 유지 |
| LLR-04 | pc005·pc072: `골목`·`우리 골목` 제안 승인 시 장소 질문을 건너뛰지만 `location=null`이면 원문·시점을 보존하고 장소를 물음. 직접 모호한 답변을 해도 다음 단계로 이동 | 초기 추출은 모호한 장소를 원문에 남기고 location을 비워 보완 질문하는 기준 권고. `location_checked`를 위치 정확성으로 해석하지 않음. 건너뛰기·추후 수정 유지 | 상태 대조·기준안 완료. A·D 의미, B 문구, C 상태 적용 검수 대기 |
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

G1.1 수정 이후에는 pc043 출력이 `pc043 information None`으로 바뀐다. pc027의 skipped 차이는
이번 수정 대상이 아니므로 기준 출력과 같다. 후속 회귀는
[API 검사](../tests/test_chat_extraction.py)와 [브라우저 검사](../browser_tests/test_chat_extraction.py)에
있으며 승인 자료 유무, 주제 재선택·이어보기, 접수 전환·최종 동의와 기존 답변 보존을 확인한다.

## LLR-02 후속 · 실제 대화 상태 대조

2026-09-13 · G3.1 → G1·G0 이미 답하거나 건너뛴 항목을 불필요하게 다시 묻지 않기.
`1a40c98`의 앱으로 확인했으며 이번에는 제품 코드·추출 계약·정답·분할을 변경하지 않았다.

가로등 고정 합성 예문에서 발견 시점을 **직접 답변 / 처음 내용에 이미 적었다고 선택 /
자동 정리 제안 확인**의 세 경로로 입력하고, 시설 표지는 화면의 건너뛰기 버튼으로 넘겼다.
세 경로 모두 새로고침, 제목·장소만 수정, 같은 주제 재선택 뒤에 두 항목의 상태가 유지되고
추가 질문 없이 확인 화면을 표시했다. 최종 접수 동의는 체크되지 않았고 접수 버튼은 비활성이다.
직접 입력한 답변은 추가 내용에 한 번 포함되고 원문에서 확인한 답변은 중복해서 붙지 않았다.

시민이 **시설 표지 답변 수정**을 직접 누르면 그 항목을 다시 묻고, 수정 도중 새로고침해도
계속 수정할 수 있었다. 이는 건너뛰기 손실이 아니다. 원문을 편집해 추출 근거 문자열을 지운
경우에는 해당 추출 답변만 무효화하여 다시 묻고 시설 표지의 건너뛰기는 보존한다. 이 경계는
기존 API 검사 `test_editing_source_invalidates_only_extracted_answers`에서 확인했다.

| 평가·입력의 의미 | 현재 앱의 저장 상태 | 대응할 때 지킬 경계 |
| --- | --- | --- |
| `known_fields`의 질문 답변 | 직접 답변은 `answers[field].status=answered`와 값 | 추출 요청의 새 필드가 아님. 평가에 기존 값을 제공할 주체는 C·D 합의 필요 |
| 원문에서 추출하고 시민이 확인한 답변 | `in_description`과 근거 문자열 값 | 원문을 고쳐 근거가 사라지면 해당 값 재확인 |
| “처음 내용에 이미 적었어요” 선택 | 값이 비어 있는 `in_description` | 재질문을 멈추는 시민 선택이다. 모델이 알아낸 답변 값으로 만들지 않음 |
| 명시적인 `skipped_fields` | UI에서 건너뛴 항목의 `status=skipped` | 답변 문자열이나 “모른다” 키워드와 구분. 기존 대화 상태를 신뢰된 입력으로 취급 |
| 긴급 흐름에서 묻지 않은 항목 | `status=not_asked` | 시민이 건너뛰기를 선택했다고 바꾸지 않음 |

현재 `ExtractionRequest` v1은 새 입력의 `source_text`와 템플릿을 받고, 기존
`known_fields`·`skipped_fields`를 받지 않는다. 반면 이어보는 UI는 DB의 `ChatState.intake`를
읽는다. 따라서 pc027·pc013·pc009·pc012의 평가 메타데이터를 원문 전용 추출에 싣지 않은 결과와
실제 UI의 상태 손실은 구분해야 한다. C·D는 평가를 새 원문 추출에 한정할지, 앱의 기존 상태까지
포함해 평가할지와 그 전달 주체를 정하고 A가 건너뛰기의 사용자 의미를 검수해야 한다.
본문의 수동 skip 주입 예시는 이 계약 차이의 대조일 뿐 실제 UI 경로가 아니다.

검증: 관련 질문·추출 API와 재질문 평가 검사 **47건 통과**. 기존
[브라우저 검사](../browser_tests/test_chat_extraction.py)에
`test_known_and_skipped_answers_survive_resume_edit_and_same_topic`의 세 경로를 추가했고,
독립 Chromium에서 모든 HTTP 요청을 인프로세스 `TestClient`로 처리해 통과했다. 로컬 서버는
열지 않았고 검사 후 브라우저·드라이버를 종료했다. 네 평가 사례의 실제 모델 예측이나
참여자 관찰을 수행한 결과가 아니며, 이 범위 밖 대화의 무결함을 보장하지 않는다.

CI에서는 새 세 경로 이후 기존 안내 검사가 429로 실패했다. 세션 범위의 앱과 접속 주소를
공유해 요청 횟수가 누적된 원인이므로 브라우저 fixture에서 검사마다 새 `CitizenRateLimiter`를
제공한다. 각 검사 안에서는 기존 요청 한도를 그대로 적용하며 제품 코드의 제한은 변경하지 않는다.

## LLR-03 후속 · 발견 시점의 인용 범위 검수안

2026-09-13 · G3.1 → G0 시민이 말한 시점 정보를 보존하는지 평가하기.
기준 코드 `2c1ec5a4c8a75418999959554247d49fb527fe13`, 데이터 버전·SHA-256은 위 검토 범위와 같다.
pf006의 **pc027·pc013 두 건만** 대상으로 삼았으며 두 건 모두 기존 test 분할의 합성 초안이다.

| 사례 | 비교한 원문 | 해석과 이번 검수 경계 |
| --- | --- | --- |
| pc027 | 가상 달빛길 가로등이 **사흘 전 밤부터** 안 켜져요. 번호는 몰라서 건너뛸게요. | 시민이 상태가 이어진 시작을 설명한다. `부터`를 포함하면 이 표현을 필드에도 보존한다. 객관적으로 확인된 고장 발생 시각으로 단정하지 않음 |
| pc013 | **사흘 전 밤에** 본 가상 달빛길 가로등 불이 아직 안 들어와요. 표지는 모르겠어요. | 시민이 본 시점이다. `에`를 포함할 수 있으나 `부터`로 바꾸거나 고장 시작 시점으로 단정할 수 없음 |

현행 채점기는 원문에 있는지(`grounded`)와 정답 목록의 문자열에 정확히 일치하는지를 따로
확인한다. 앱은 `value == quote`이며 원문 안의 문자열인지를 검사한다. **원문 검사 통과만으로
답변의 의미가 충분하거나 정답이라는 뜻은 아니다.** 아래 결과는 각 후보를 직접 넣은 대조다.

| 사례 | `observed_time` 후보 | 앱 원문 검사 | 현행 채점 결과 | 검수안 |
| --- | --- | --- | --- | --- |
| pc027 | `사흘 전 밤` | 통과 | 일치 | 기존 허용값 유지. 상대 날짜와 시간대를 담음 |
| pc027 | `사흘 전 밤부터` | 통과 | `extracted_fields` 불일치 | 추가 허용 권고. 원문에 있는 시작 표현까지 보존 |
| pc027 | `밤` | 통과 | `extracted_fields` 불일치 | 추가하지 않음. 어느 날인지 설명한 `사흘 전`이 빠짐 |
| pc027 | `사흘 전 밤에` | 거부 | 근거 없음 + 추출 불일치 | 추가하지 않음. 해당 원문에 없음 |
| pc013 | `사흘 전 밤` | 통과 | 일치 | 기존 허용값 유지. 상대 날짜와 시간대를 담음 |
| pc013 | `사흘 전 밤에` | 통과 | `extracted_fields` 불일치 | 추가 허용 권고. 시민이 본 시점의 표현을 보존 |
| pc013 | `밤` | 통과 | `extracted_fields` 불일치 | 추가하지 않음. `사흘 전`이 빠짐 |
| pc013 | `사흘 전 밤부터` | 거부 | 근거 없음 + 추출 불일치 | 추가하지 않음. 다른 사례의 표현이며 시작 의미도 더함 |

앱 거부 사유는 `extraction_quote_not_in_source`, 채점기의 근거 없음은
`ungrounded_extraction`이다. 나머지 서비스·의도·장소·행동은 현행 정답으로 고정해 시점 범위의
차이만 대조했다. 원본 두 사례를 그대로 복사한 임시 부분집합과 수동 출력을 `score()`에 전달했고,
각 실행은 두 ID를 모두 포함했다. 앱 쪽은 같은 원문·템플릿의 `ExtractionRequest`와 수동 제안에
`validate_proposal()`만 실행했다. LLR-02의 앱 상태 적용이나 모델·DB·서버 실행은 포함하지 않았다.
8개 후보의 검사·채점 결과와 원본 해시 불변을 확인했고, 기존 채점기 회귀 검사 32건이 통과했다.
수동 출력의 식별자는 `manual-llr03-contract-check`이며 실제 모델 예측·성능 수치가 아니다.

### A·D가 결정할 두 선택지

| 선택지 | 정답 범위와 영향 |
| --- | --- |
| **사례별 정확한 인용 대안 추가 · 권고** | pc027은 `["사흘 전 밤", "사흘 전 밤부터"]`, pc013은 `["사흘 전 밤", "사흘 전 밤에"]`를 허용. 기존 값을 유지하면서 같은 문장의 의미를 보존한 범위 차이를 수용 |
| 현행 한 가지 범위 유지 | 두 사례 모두 `["사흘 전 밤"]`만 허용. 조사 없는 범위를 평가 규칙으로 명시해야 하며, 더 긴 원문 인용도 계속 추출 불일치로 집계 |

권고안은 **정답 변경 전 검수안**이다. `Gold.extracted_fields`는 이미 복수의 정확한 인용을
지원하므로 새 채점기나 일괄 정규화가 필요하지 않다. 모든 조사·부분 문자열·전체 문장을 허용하는
규칙으로 확대하지 않는다. 시민 확인 화면의 원문과 제안을 유지하고 날짜를 계산해 바꾸지 않는다.

A는 발견 시점 질문에 두 표현이 충분한지, D는 원문 일치·현행 비교와의 영향을 검수하고
선택·검토자·날짜를 남긴다. 승인 후 실제 반영할 때 데이터·계약의 버전과 해시, 변경한 사례를
함께 기록하고 동일 예측을 비교할 때 어느 정답 버전을 썼는지 구분한다. 현재 버전은 스키마의
Literal에도 고정되어 있으므로 JSONL만 몰래 수정하지 않는다. test 원문·정답을 학습이나 모델
프롬프트에 복사하지 않으며, 이번 검수로 모델 성능이 개선되었다고 발표하지 않는다.

검수안 작성은 여기서 종료한다. 이번 커밋에서 앱·평가 코드, 원본 정답·분할·버전은 그대로다.
LLR-04~06, 실제 모델 연결과 실제 참여자 관찰은 별도 의존성을 유지한다.

## LLR-04 후속 · 모호한 장소의 처리 기준안

2026-09-13 · G3.1 → G1·G0 정확한 주소를 몰라도 상황과 장소 단서를 보완하며 이어가기.
기준 코드 `43fa0972b5a55cf6d61852bad587c2a2737e8c2a`, 데이터 버전·SHA-256은 위와 같다.
pf004의 pc005·pc072는 **train 분할의 합성 초안 두 건**이다. 정답에는 발견 시점 `어제 저녁`만
있고 장소는 없다. 다음 질문은 `location_text` 또는 `facility_label` 중 하나를 허용한다.

| 사례 | 원문 | 아직 없는 정보 |
| --- | --- | --- |
| pc005 | 어제 저녁 **골목** 가로등이 하나도 안 켜졌어요. 고쳐 주세요. | 골목의 이름·동·주변 시설 등 어느 골목인지 구별할 단서 |
| pc072 | **우리 골목** 가로등 좀 봐 주세요. 어제 저녁 내내 불이 꺼져 있었어요. | `우리`가 가리키는 위치를 앱이 알 수 있는 근거. 사용자의 집·현재 위치를 추정하지 않음 |

### 현행 계약과 적용 결과

발견 시점은 기존 정답으로 고정하고 장소만 바꾼 수동 제안을 시민이 확인했다고 가정했다.
앱의 원문 검사와 `extraction_preview.accept()` 뒤 질문은 다음과 같다.

| 사례 | 추출 `location` 제안 | 원문 검사 | 적용 후 질문 / `location_checked` | 현행 채점 |
| --- | --- | --- | --- | --- |
| pc005 | `null` | 통과 | 장소 / false | 일치 |
| pc005 | `골목` | 통과 | 시설 표지 / true | 장소 필드가 정답에 없어 `extracted_fields` 불일치 |
| pc072 | `null` | 통과 | 장소 / false | 일치 |
| pc072 | `우리 골목` | 통과 | 시설 표지 / true | `extracted_fields` 불일치 |
| pc072 | `골목` | 통과 | 시설 표지 / true | `extracted_fields` 불일치 |

다섯 경로 모두 원문과 `observed_time=어제 저녁`을 보존한다. 비어 있지 않은 장소 제안을
승인하면 앱은 장소 질문을 마친 것으로 처리한다. 하지만 **원문에 존재하고 시민이 제안을
확인했다는 사실만으로 현장을 특정할 수 있는 것은 아니다.** 시설 표지도 현재 허용 질문이므로
표지 질문으로 넘어간 것 자체는 `next_step` 실패가 아니다. 장소 필드의 의미 충분성이 쟁점이다.

`location=null`의 현재 안내는 “어디에서 있었던 일인가요? 주변 건물이나 시설 이름만 알려 주셔도
돼요.”이며 화면에는 “정확한 장소를 모르겠어요” 선택이 있다. 두 초기 상태에서 각각 아래 세
합성 후속 입력을 `prepare_state()`로 적용했다. 원본 평가 사례에 후속 턴을 추가한 것은 아니다.

| 합성 후속 입력 | 현재 저장·진행 | 해석 |
| --- | --- | --- |
| `합성 별빛공원 동문 앞 골목` | 해당 문자열을 장소로 보존하고 시설 표지를 질문 | 사용자가 새로 준 단서다. 공식 주소·시설·관할 검증을 한 것은 아님 |
| `우리 골목` | 그대로 장소에 저장하고 시설 표지를 질문 | 직접 답변 단계에는 의미 충분성을 판정해 재질문하는 기능이 없음 |
| `skip_location` 선택 | 장소를 빈 값으로 두고 시설 표지를 질문 | 장소 미상으로도 계속 진행 가능. 빈 값은 좌표나 주소로 보완하지 않음 |

이어 시설 표지를 건너뛰면 여섯 경로 모두 확인 단계가 되고, 원문·시점·건너뛰기를 보존한다.
상태 JSON 왕복과 같은 주제 재선택도 비교했다. DB 이어보기나 브라우저 관찰을 한 결과는 아니다.
`location_checked=true`는 **장소 단계를 거쳤다는 진행 상태**다. 건너뛰기에도 true이며 위치
정확성·관할 확인·공식 접수의 증거가 아니다. 현재 [확인 화면 코드](../app/static/citizen-chat.js)는
빈 장소에만 “장소 미입력 · 담당자 확인이 필요해요”를 표시하고, 비어 있지 않은 모호한 장소는
입력 문자열을 그대로 표시한다.

### 권고할 기준과 시민 안내

**현재 v1에서는 `골목`·`우리 골목`만으로 어느 곳인지 구별할 수 없으면 `location=null`을
권고한다.** 단서는 원래 본문에 남기고 이미 확인한 발견 시점은 다시 묻지 않는다. 이는
두 표현을 차단하는 키워드 규칙이 아니다. LLM이 제공된 문맥에 구별 가능한 장소 단서가 있는지
판단할 기준이며, 실제 모델이 이 기준을 지키는지는 별도 평가해야 한다.

장소 보완은 한 번에 한 가지 단서만 요청한다. 기존 안내를 유지하거나 B가 아래 문구를 검수할 수 있다.

> 말씀하신 골목 근처에 아는 건물이나 가게·공원 이름이 있나요? 정확한 주소는 몰라도 괜찮아요.

“정확한 장소를 모르겠어요”를 선택하면 같은 질문을 반복하지 않고 다음 선택 항목과 확인 단계로
이어간다. 다시 모호한 답변을 주더라도 정확한 주소 입력을 반복 강요하지 않고 시민의 표현을 남긴다.
구별이 안 되는 장소에는 담당자 확인이 필요하다는 안내를 검토하고, 시민이 단서를 더 알게 되면
기존 수정 기능을 사용한다. 이 문구·표시 정책은 **아직 구현에 반영하지 않은 기준안**이다.
구체적인 동·시설·방향을 말한 경우에도 사용자 진술과 공식 위치 검증은 구분한다.

| 결정할 선택 | 영향과 필요한 검수 |
| --- | --- |
| **원문 보존 + 추출 location 비움 · 권고** | 현재 정답과 계약으로 장소 질문 가능. A·D가 단서의 충분성을 검수하고 B가 질문을 확인. 기존 단계 진행·동의는 C가 유지 |
| 모호한 원문도 location 값으로 사용 | 현재 앱에서는 장소 질문을 건너뜀. 채택하려면 A·D가 라벨 기준, B·C가 미확정 장소 표시·추가 확인 방식을 먼저 합의. 위치가 확정되었다고 취급하지 않음 |

검증은 같은 원문·템플릿의 수동 제안 5개와 합성 후속 상태 흐름 6개의 함수 대조다. train의
두 원본 사례를 그대로 복사한 임시 부분집합에 수동 출력을 넣어 채점 함수의 실패 항목만 확인했다.
식별자는 `manual-llr04-state-check`이며 train으로 실제 모델 성능을 평가한 것이 아니다.
`ExtractionRunner.run`, 모델·정부 API·DB·서버는 호출하지 않았다. 제안 검사·상태·채점 결과와
원본 해시 불변을 확인했고 앱·평가 코드, 정답·분할·버전을 바꾸지 않았다.

기준안 작성은 종료한다. A·D의 의미 기준, B의 안내·사용성, C의 상태·표시 적용 검수가 남아 있으며,
LLR-05~06·실제 모델·공식 장소/관할 확인·참여자 관찰은 이번 결과로 완료 처리하지 않는다.

## 검토 종료와 남은 범위

G3.1의 초기 14건 대조에서 **사례 ID별 대응·불일치 기록**은 완료했다. 전체 라벨 승인이나 G3의 실제
LLM 협업을 완료 처리하지 않는다. A·D는 LLR-01~06 각각에 검토자·날짜·결론·수정 버전을 남기고,
필요한 변경만 후속으로 진행한다. 이 문서를 작성하면서 팀원에게 연락이나 작업 발송은 하지 않았다.

초기 14건에는 가로등 긴급 위험, 명시적인 보안등 관할, 여러 턴의 정정, 사진 판독 사례가 없다.
전체 데이터에는 다른 업무의 긴급 사례가 있지만 이 14건으로 그 영역의 성능을 주장할 수 없다.
새 평가 가족 추가는 이 검토를 끝내기 위한 조건이 아니며 기존 분할을 보존한 별도 후속 후보다.

후속 G1.1의 안내 흐름 수정은 완료했으며 현재 우선순위는 [TASKS.md](../TASKS.md)를 따른다.
LLR-02의 UI 상태 대조, LLR-03 인용 검수안과 LLR-04 장소 기준안도 종료한다. 입력 대응 합의,
LLR-03~04 최종 결정, LLR-05~06, 공식 자료 L01~L05, 실제 서버 연결은 각각의 의존성을 유지한다.
