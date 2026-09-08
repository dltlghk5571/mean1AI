from pathlib import Path

import pytest

from evals.reserved_seed_registry import (
    ReservedSeedEntry,
    load_registry,
    reserved_content_hashes,
    reserved_seed_ids,
)


def _entry(seed_id: str = "seed-1", content_hash: str | None = "a" * 64) -> ReservedSeedEntry:
    return ReservedSeedEntry(
        seed_id=seed_id,
        source_id=None,
        content_hash=content_hash,
        reserved_for="evaluation",
        reserved_by="teammate",
        reserved_at="2026-09-08",
        note="synthetic fixture",
    )


def test_load_registry_missing_file_returns_empty_list(tmp_path: Path) -> None:
    assert load_registry(tmp_path / "missing.jsonl") == []


def test_load_registry_parses_lines_and_skips_blanks(tmp_path: Path) -> None:
    path = tmp_path / "registry.jsonl"
    path.write_text(
        _entry("seed-1").model_dump_json() + "\n\n" + _entry("seed-2").model_dump_json() + "\n",
        encoding="utf-8",
    )
    entries = load_registry(path)
    assert [e.seed_id for e in entries] == ["seed-1", "seed-2"]


def test_load_registry_raises_on_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "registry.jsonl"
    path.write_text("not json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="registry.jsonl"):
        load_registry(path)


def test_reserved_seed_ids_extracts_ids() -> None:
    entries = [_entry("seed-1"), _entry("seed-2")]
    assert reserved_seed_ids(entries) == {"seed-1", "seed-2"}


def test_reserved_content_hashes_skips_none() -> None:
    entries = [_entry("seed-1", content_hash="b" * 64), _entry("seed-2", content_hash=None)]
    assert reserved_content_hashes(entries) == {"b" * 64}
