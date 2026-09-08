from unittest.mock import Mock

import pytest

from evals.chat_baseline import run_baseline, score_prediction
from evals.chat_baseline_run import predict_with_agent
from evals.chat_dataset import ChatModelOutput, DatasetRecord

_TARGET = ChatModelOutput(
    assistant_message="확인했습니다.",
    title="가로등이 꺼져 있어요",
    content="어제 저녁 공원 산책로 가로등이 꺼져 있었습니다.",
    location_text="데모공원 산책로",
    ready_to_submit=True,
)


def _record(draft_id: str = "d1") -> DatasetRecord:
    return DatasetRecord(
        draft_id=draft_id,
        complaint_id="c1",
        category="accepted_unedited",
        split="test",
        edited=False,
        messages=[
            {"role": "system", "content": "instructions"},
            {"role": "user", "content": "사용자: 어제 저녁 공원 가로등이 꺼져 있었어요"},
        ],
        target=_TARGET,
    )


def test_score_prediction_identical_output_is_perfect_match() -> None:
    case = score_prediction("d1", _TARGET, _TARGET)
    assert case.ready_match is True
    assert case.title_token_overlap == 1.0
    assert case.content_token_overlap == 1.0
    assert case.location_token_overlap == 1.0


def test_score_prediction_disjoint_output_scores_zero_overlap() -> None:
    predicted = _TARGET.model_copy(
        update={
            "title": "완전히 다른 내용",
            "content": "전혀 겹치지 않는 문장",
            "ready_to_submit": False,
        }
    )
    case = score_prediction("d1", _TARGET, predicted)
    assert case.ready_match is False
    assert case.title_token_overlap == 0.0
    assert case.content_token_overlap == 0.0


def test_run_baseline_aggregates_metrics_over_multiple_cases() -> None:
    records = [_record("d1"), _record("d2")]
    predictions = {"d1": _TARGET, "d2": _TARGET.model_copy(update={"ready_to_submit": False})}
    report = run_baseline(records, lambda record: predictions[record.draft_id])
    assert report.case_count == 2
    assert report.ready_agreement_rate == pytest.approx(0.5)
    assert report.mean_title_token_overlap == pytest.approx(1.0)


def test_run_baseline_on_empty_input_returns_neutral_report() -> None:
    report = run_baseline([], lambda record: _TARGET)
    assert report.case_count == 0
    assert report.ready_agreement_rate == 1.0


def test_predict_with_agent_invokes_graph_on_exported_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.chat_agent import ChatAgent

    sdk = Mock()
    sdk.responses.parse.return_value = Mock(
        output_parsed=Mock(
            assistant_message="네",
            title="예측된 제목",
            content="예측된 내용",
            location_text="예측된 위치",
            ready_to_submit=True,
        )
    )
    monkeypatch.setattr("openai.OpenAI", Mock(return_value=sdk))
    agent = ChatAgent(api_key="synthetic-unused-key", model="synthetic-model")

    predicted = predict_with_agent(agent, _record("d1"))
    assert predicted.title == "예측된 제목"
    assert predicted.ready_to_submit is True
    called_input = sdk.responses.parse.call_args.kwargs["input"]
    assert called_input == "사용자: 어제 저녁 공원 가로등이 꺼져 있었어요"
