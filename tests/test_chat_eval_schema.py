from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.chat_dataset import ChatModelOutput
from evals.chat_eval_schema import EvalRecord, load_eval_jsonl

_TARGET = ChatModelOutput(
    assistant_message="확인했습니다.",
    title="가로수 쓰러짐",
    content="분당구 정자동에서 가로수가 쓰러졌습니다.",
    location_text="분당구 정자동",
    ready_to_submit=True,
)


def _record(eval_id: str = "eval-1") -> EvalRecord:
    return EvalRecord(
        eval_id=eval_id,
        seed_id="seed-1",
        source_id=None,
        source_url=None,
        region="seongnam",
        district="bundang",
        case_type="complete",
        transcript="분당구 정자동에서 가로수가 쓰러졌습니다.",
        expected=_TARGET,
        required_facts=["가로수 쓰러짐"],
        forbidden_inferences=[],
        safety_expectation="",
        reviewer="synthetic-reviewer",
        review_status="approved",
        review_notes="",
        content_hash="a" * 64,
        dataset_version="test.v0",
    )


def test_load_eval_jsonl_missing_file_returns_empty_list(tmp_path: Path) -> None:
    assert load_eval_jsonl(tmp_path / "missing.jsonl") == []


def test_load_eval_jsonl_parses_records(tmp_path: Path) -> None:
    path = tmp_path / "eval.jsonl"
    path.write_text(_record("eval-1").model_dump_json() + "\n", encoding="utf-8")
    records = load_eval_jsonl(path)
    assert [r.eval_id for r in records] == ["eval-1"]


def test_load_eval_jsonl_raises_on_duplicate_eval_id(tmp_path: Path) -> None:
    path = tmp_path / "eval.jsonl"
    path.write_text(
        _record("dup").model_dump_json() + "\n" + _record("dup").model_dump_json() + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Duplicate eval_id"):
        load_eval_jsonl(path)


def test_eval_record_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        EvalRecord(**{**_record().model_dump(), "unexpected_field": "nope"})
