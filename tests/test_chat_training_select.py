import json
from pathlib import Path

from evals.chat_dataset import ChatModelOutput
from evals.chat_training_gen import TrainingProvenance, TrainingRecord
from evals.chat_training_select import main, select_trainable
from evals.text_similarity import content_hash

_TARGET = ChatModelOutput(
    assistant_message="확인했습니다.",
    title="가로수 쓰러짐",
    content="분당구 정자동에서 가로수가 쓰러졌습니다.",
    location_text="분당구 정자동",
    ready_to_submit=True,
)


def _record(
    training_id: str, *, bucket: str = "sft_candidate", review_status: str = "approved"
) -> TrainingRecord:
    transcript = f"transcript for {training_id}"
    return TrainingRecord(
        training_id=training_id,
        case_type="normal",
        bucket=bucket,
        messages=[{"role": "user", "content": transcript}],
        target=_TARGET,
        provenance=TrainingProvenance(
            source_id=None,
            seed_template_id=training_id,
            region_scope="seongnam",
            origin="synthetic_teacher",
            license="internal-synthetic-seed",
            teacher_model="synthetic-model",
            prompt_version="test.v0",
            review_status=review_status,
            content_hash=content_hash(transcript),
            generation_version="test.v0",
        ),
    )


def test_select_trainable_keeps_only_approved_sft_candidates() -> None:
    records = [
        _record("approved-sft", bucket="sft_candidate", review_status="approved"),
        _record("draft-sft", bucket="sft_candidate", review_status="draft"),
        _record("rejected-sft", bucket="sft_candidate", review_status="rejected"),
        _record("approved-excluded", bucket="excluded_safety_signal", review_status="approved"),
    ]
    selected, summary = select_trainable(records)
    assert [r.training_id for r in selected] == ["approved-sft"]
    assert summary.input_count == 4
    assert summary.selected_count == 1
    assert summary.skipped_by_reason == {
        "review_status:draft": 1,
        "review_status:rejected": 1,
        "bucket:excluded_safety_signal": 1,
    }


def test_select_trainable_never_mutates_records() -> None:
    record = _record("approved-sft")
    selected, _ = select_trainable([record])
    assert selected[0] is record


def test_main_writes_only_selected_records(tmp_path: Path, capsys) -> None:
    training_file = tmp_path / "input.jsonl"
    records = [
        _record("approved-sft", bucket="sft_candidate", review_status="approved"),
        _record("draft-sft", bucket="sft_candidate", review_status="draft"),
    ]
    with training_file.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n")

    out_file = tmp_path / "out" / "lora_ready.jsonl"
    exit_code = main(["--training-file", str(training_file), "--out", str(out_file)])
    assert exit_code == 0

    lines = out_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["training_id"] == "approved-sft"

    summary = json.loads(capsys.readouterr().out)
    assert summary["selected_count"] == 1
    assert summary["skipped_by_reason"] == {"review_status:draft": 1}


def test_main_exits_2_on_malformed_input(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.jsonl"
    bad_file.write_text("not json\n", encoding="utf-8")
    exit_code = main(["--training-file", str(bad_file), "--out", str(tmp_path / "out.jsonl")])
    assert exit_code == 2
