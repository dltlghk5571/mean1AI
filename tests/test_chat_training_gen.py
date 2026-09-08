from evals.chat_dataset import ChatModelOutput
from evals.chat_training_gen import SeedTemplate, TeacherGeneration, generate_pilot

_NORMAL_TARGET = ChatModelOutput(
    assistant_message="접수할 내용을 정리했습니다.",
    title="가로수 쓰러짐",
    content="분당구 정자동에서 가로수가 쓰러졌습니다.",
    location_text="분당구 정자동",
    ready_to_submit=True,
)


def _seed(seed_id: str = "seed-1", case_type: str = "normal") -> SeedTemplate:
    return SeedTemplate(
        seed_template_id=seed_id,
        region_scope="seongnam",
        category="road_damage",
        case_type=case_type,
        scenario_brief="synthetic fixture scenario",
    )


def test_generate_pilot_produces_sft_candidate_for_normal_case() -> None:
    seeds = [_seed()]
    result = generate_pilot(
        seeds,
        lambda seed: TeacherGeneration(
            transcript="분당구 정자동 가로수 쓰러짐", target=_NORMAL_TARGET
        ),
        reserved_seed_ids=set(),
        teacher_model="synthetic-model",
        prompt_version="test-prompt.v1",
    )
    assert len(result.records) == 1
    assert result.records[0].bucket == "sft_candidate"
    assert result.records[0].provenance.review_status == "draft"
    assert result.records[0].provenance.prompt_version == "test-prompt.v1"
    assert result.records[0].safety_signal is None
    assert not result.rejected
    assert result.summary.prompt_version == "test-prompt.v1"
    assert result.summary.bucket_counts == {"sft_candidate": 1}
    assert result.summary.excluded_safety_signal_trigger_counts == {}


def test_generate_pilot_skips_reserved_seed_without_calling_teacher() -> None:
    calls = []

    def teacher_call(seed: SeedTemplate) -> TeacherGeneration:
        calls.append(seed.seed_template_id)
        return TeacherGeneration(transcript="never reached", target=_NORMAL_TARGET)

    result = generate_pilot(
        [_seed("reserved-seed")],
        teacher_call,
        reserved_seed_ids={"reserved-seed"},
        teacher_model="synthetic-model",
    )
    assert calls == []
    assert not result.records
    assert result.rejected[0].reason == "seed_reserved_for_eval"


def test_generate_pilot_buckets_emergency_case_as_excluded_safety_signal() -> None:
    seeds = [_seed("seed-emergency", case_type="emergency")]
    target = _NORMAL_TARGET.model_copy(
        update={"content": "지금 가스 냄새가 심하게 나서 대피 중입니다."}
    )
    result = generate_pilot(
        seeds,
        lambda seed: TeacherGeneration(transcript="가스 냄새 대피", target=target),
        reserved_seed_ids=set(),
        teacher_model="synthetic-model",
    )
    record = result.records[0]
    assert record.bucket == "excluded_safety_signal"
    assert record.safety_signal is not None
    assert "case_type_emergency" in record.safety_signal.triggers
    assert result.summary.excluded_safety_signal_trigger_counts["case_type_emergency"] == 1
    # excluded content lives only on the record's own messages/target -- the
    # bucket split (writers group by f"{region}/{bucket}.jsonl") is what keeps
    # it out of any sft_candidate.jsonl file, never merged in.
    assert record.bucket != "sft_candidate"


def test_generate_pilot_rejects_teacher_exception_as_generation_failed() -> None:
    def failing_teacher(seed: SeedTemplate) -> TeacherGeneration:
        raise RuntimeError("synthetic failure")

    result = generate_pilot(
        [_seed()], failing_teacher, reserved_seed_ids=set(), teacher_model="synthetic-model"
    )
    assert not result.records
    assert result.rejected[0].reason == "generation_failed"


def test_generate_pilot_rejects_exact_duplicate_transcript() -> None:
    seeds = [_seed("seed-1"), _seed("seed-2")]
    result = generate_pilot(
        seeds,
        lambda seed: TeacherGeneration(
            transcript="동일한 내용의 민원입니다.", target=_NORMAL_TARGET
        ),
        reserved_seed_ids=set(),
        teacher_model="synthetic-model",
    )
    assert len(result.records) == 1
    assert result.rejected[0].reason == "duplicate_content_hash"


def test_generate_pilot_rejects_pii_leak_in_target() -> None:
    leaking_target = _NORMAL_TARGET.model_copy(
        update={"content": "제 전화번호는 010-1234-5678이고 이름은 홍길동입니다."}
    )
    result = generate_pilot(
        [_seed()],
        lambda seed: TeacherGeneration(
            transcript="synthetic transcript text", target=leaking_target
        ),
        reserved_seed_ids=set(),
        teacher_model="synthetic-model",
    )
    assert not result.records
    assert result.rejected[0].reason == "pii_leak_in_target"
