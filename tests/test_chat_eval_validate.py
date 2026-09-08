from pathlib import Path

from evals.chat_dataset import ChatModelOutput
from evals.chat_eval_schema import EvalRecord
from evals.chat_eval_validate import validate_file

_TARGET = ChatModelOutput(
    assistant_message="확인했습니다.",
    title="가로수 쓰러짐",
    content="분당구 정자동에서 가로수가 쓰러졌습니다.",
    location_text="분당구 정자동",
    ready_to_submit=True,
)


def _valid_line(eval_id: str = "eval-1") -> str:
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
        required_facts=[],
        forbidden_inferences=[],
        safety_expectation="",
        reviewer="synthetic-reviewer",
        review_status="approved",
        review_notes="",
        content_hash="a" * 64,
        dataset_version="test.v0",
    ).model_dump_json()


def test_validate_file_missing_file_reports_error(tmp_path: Path) -> None:
    errors = validate_file(tmp_path / "missing.jsonl")
    assert "does not exist" in errors[0]


def test_validate_file_empty_file_reports_error(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    errors = validate_file(path)
    assert "empty" in errors[0]


def test_validate_file_valid_lines_report_no_errors(tmp_path: Path) -> None:
    path = tmp_path / "valid.jsonl"
    path.write_text(_valid_line("eval-1") + "\n" + _valid_line("eval-2") + "\n", encoding="utf-8")
    assert validate_file(path) == []


def test_validate_file_reports_duplicate_eval_id(tmp_path: Path) -> None:
    path = tmp_path / "dup.jsonl"
    path.write_text(_valid_line("dup") + "\n" + _valid_line("dup") + "\n", encoding="utf-8")
    errors = validate_file(path)
    assert any("duplicate eval_id" in e for e in errors)


def test_validate_file_reports_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text("not json\n", encoding="utf-8")
    errors = validate_file(path)
    assert len(errors) == 1
