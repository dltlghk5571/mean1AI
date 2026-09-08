import json

from evals.chat_training_gen import SeedTemplate
from evals.chat_training_prompt_export import main, render_prompt

_SEED = SeedTemplate(
    seed_template_id="l2-normal-001",
    region_scope="seongnam",
    category="road_damage",
    case_type="normal",
    scenario_brief="Citizen reports a pothole near a Seongnam intersection.",
)


def test_render_prompt_contains_all_required_sections() -> None:
    prompt = render_prompt(_SEED)

    # seed identity + both variants requested
    assert "l2-normal-001" in prompt
    assert "Requested variants: 1 and 2" in prompt
    assert '"variant_id": 1' in prompt
    assert '"variant_id": 2' in prompt

    # live production instructions reused verbatim
    from app.services.chat_agent import _INSTRUCTIONS

    assert _INSTRUCTIONS in prompt

    # target JSON schema block (from TeacherGeneration.model_json_schema())
    assert '"transcript"' in prompt
    assert "ready_to_submit" in prompt

    # seed scenario fields
    assert '"region_scope": "seongnam"' in prompt
    assert '"category": "road_damage"' in prompt
    assert '"case_type": "normal"' in prompt
    assert _SEED.scenario_brief in prompt

    # key constraint phrases
    assert "Never invent facts" in prompt
    assert "never ask more than one question in the same turn" in prompt
    assert "Only use fictional/invented personal names" in prompt
    assert "real, verifiable Seongnam locations" in prompt
    assert "meaningfully different from each other" in prompt

    # JSON-only response format
    assert "Return JSON only" in prompt
    assert "no Markdown code fence" in prompt


def test_render_prompt_skips_non_seongnam_place_name_rule_text_only_when_relevant() -> None:
    # The Seongnam place-name rule text is present for every seed (it's a
    # standing rule in the shared constraints block, not conditionally
    # rendered) -- this just documents that render_prompt is otherwise
    # identical regardless of region_scope.
    national_seed = _SEED.model_copy(update={"region_scope": "national"})
    prompt = render_prompt(national_seed)
    assert '"region_scope": "national"' in prompt


def _write_seed_file(path, seeds: list[SeedTemplate]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for seed in seeds:
            handle.write(seed.model_dump_json() + "\n")


def _write_registry(path, entries: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")


def test_main_exports_prompts_skips_reserved_and_writes_manifest_and_metadata(tmp_path) -> None:
    seeds = [
        _SEED,
        _SEED.model_copy(
            update={"seed_template_id": "l2-incomplete-001", "case_type": "incomplete"}
        ),
        _SEED.model_copy(update={"seed_template_id": "l2-emergency-001", "case_type": "emergency"}),
    ]
    seed_file = tmp_path / "seeds.jsonl"
    _write_seed_file(seed_file, seeds)

    registry_file = tmp_path / "registry.jsonl"
    _write_registry(
        registry_file,
        [
            {
                "seed_id": "l2-emergency-001",
                "reserved_for": "evaluation",
                "reserved_by": "eval-teammate",
                "reserved_at": "2026-01-01",
            }
        ],
    )

    out_dir = tmp_path / "run"
    exit_code = main(
        [
            "--seed-file",
            str(seed_file),
            "--out-dir",
            str(out_dir),
            "--run-id",
            "test-run-1",
            "--registry",
            str(registry_file),
            "--declared-model",
            "ChatGPT Plus web, gpt-5.1",
        ]
    )
    assert exit_code == 0

    for subdir in ("prompts", "manual_responses", "normalized", "rejected", "review"):
        assert (out_dir / subdir).is_dir()

    # reserved seed is skipped: no prompt file, absent from manifest
    prompt_files = sorted(p.name for p in (out_dir / "prompts").glob("*.txt"))
    assert prompt_files == ["l2-incomplete-001.txt", "l2-normal-001.txt"]

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == "test-run-1"
    assert manifest["variants_per_seed"] == 2
    assert set(manifest["expected_seed_ids"]) == {"l2-normal-001", "l2-incomplete-001"}
    assert len(manifest["expected_variant_keys"]) == 4
    assert set(manifest["expected_variant_keys"]) == {
        "l2-normal-001::v1",
        "l2-normal-001::v2",
        "l2-incomplete-001::v1",
        "l2-incomplete-001::v2",
    }
    assert manifest["skipped_reserved_seed_ids"] == ["l2-emergency-001"]

    metadata = json.loads((out_dir / "metadata.json").read_text(encoding="utf-8"))
    for key in (
        "run_id",
        "git_commit",
        "git_branch",
        "generation_method",
        "prompt_version",
        "generation_version",
        "exported_seed_ids",
        "skipped_reserved_seed_ids",
        "declared_subscription_model",
        "generation_date_utc",
    ):
        assert key in metadata
    assert metadata["generation_method"] == "manual_offline_teacher"
    assert metadata["declared_subscription_model"] == "ChatGPT Plus web, gpt-5.1"
    assert metadata["skipped_reserved_seed_ids"] == ["l2-emergency-001"]


def test_main_reports_bad_seed_file_as_exit_code_2(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "--seed-file",
            str(tmp_path / "does-not-exist.jsonl"),
            "--out-dir",
            str(tmp_path / "out"),
            "--run-id",
            "test-run-2",
        ]
    )
    assert exit_code == 2
