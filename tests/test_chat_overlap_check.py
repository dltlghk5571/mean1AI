from evals.chat_dataset import ChatModelOutput
from evals.chat_eval_schema import EvalRecord
from evals.chat_overlap_check import check_overlap
from evals.chat_training_gen import TrainingProvenance, TrainingRecord
from evals.reserved_seed_registry import ReservedSeedEntry
from evals.text_similarity import content_hash

_TARGET = ChatModelOutput(
    assistant_message="확인했습니다.",
    title="가로수 쓰러짐",
    content="분당구 정자동에서 가로수가 쓰러졌습니다.",
    location_text="분당구 정자동",
    ready_to_submit=True,
)


def _training_record(
    training_id: str, seed_template_id: str, transcript: str, digest: str | None = None
) -> TrainingRecord:
    return TrainingRecord(
        training_id=training_id,
        case_type="normal",
        bucket="sft_candidate",
        messages=[{"role": "user", "content": transcript}],
        target=_TARGET,
        provenance=TrainingProvenance(
            source_id=None,
            seed_template_id=seed_template_id,
            region_scope="seongnam",
            origin="synthetic_teacher",
            license="internal-synthetic-seed",
            teacher_model="synthetic-model",
            prompt_version="test.v0",
            content_hash=digest or content_hash(transcript),
            generation_version="test.v0",
        ),
    )


def _eval_record(eval_id: str, seed_id: str, transcript: str) -> EvalRecord:
    return EvalRecord(
        eval_id=eval_id,
        seed_id=seed_id,
        source_id=None,
        source_url=None,
        region="seongnam",
        district="bundang",
        case_type="complete",
        transcript=transcript,
        expected=_TARGET,
        required_facts=[],
        forbidden_inferences=[],
        safety_expectation="",
        reviewer="synthetic-reviewer",
        review_status="approved",
        review_notes="",
        content_hash=content_hash(transcript),
        dataset_version="test.v0",
    )


def test_check_overlap_clean_when_nothing_shared() -> None:
    training = [_training_record("t1", "seed-t1", "완전히 다른 학습용 문장입니다.")]
    evals = [_eval_record("e1", "seed-e1", "분당구 정자동 가로수 쓰러짐 신고입니다.")]
    report = check_overlap(training, evals, [])
    assert report.findings == []
    assert report.training_records_checked == 1


def test_check_overlap_flags_seed_id_reserved_for_eval() -> None:
    training = [_training_record("t1", "shared-seed", "학습용 문장")]
    registry = [
        ReservedSeedEntry(
            seed_id="shared-seed",
            source_id=None,
            content_hash=None,
            reserved_for="evaluation",
            reserved_by="teammate",
            reserved_at="2026-09-08",
        )
    ]
    report = check_overlap(training, [], registry)
    assert report.findings[0].reason == "seed_id_overlap"


def test_check_overlap_flags_exact_hash_match() -> None:
    transcript = "분당구 정자동 가로수 쓰러짐 신고입니다."
    training = [_training_record("t1", "seed-t1", transcript)]
    evals = [_eval_record("e1", "seed-e1", transcript)]
    report = check_overlap(training, evals, [])
    assert report.findings[0].reason == "exact_hash_overlap"


def test_check_overlap_flags_near_duplicate_transcript() -> None:
    training = [
        _training_record("t1", "seed-t1", "분당구 정자동에서 가로수가 쓰러져 인도를 막고 있습니다.")
    ]
    evals = [
        _eval_record("e1", "seed-e1", "분당구 정자동에서 가로수가 쓰러져 인도를 막고 있습니다!")
    ]
    report = check_overlap(training, evals, [])
    assert report.findings[0].reason == "near_duplicate_overlap"
