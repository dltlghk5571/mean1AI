"""Shared seed registry: how the training-data owner and the eval-data owner
avoid picking the same source scenario without ever sharing eval text.

The registry (eval_dataset/reserved_seed_registry.jsonl) holds only IDs and
hashes -- never transcript text, never an expected _ChatExtraction target.
The eval-data teammate appends one entry per seed/source item *before*
authoring it. The training generator (evals/chat_training_gen.py) loads this
file and refuses to generate from any reserved seed_id, so a seed can never
end up on both sides.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ReservedSeedEntry(BaseModel):
    """One reservation. `content_hash` is optional at reservation time (the
    eval record may not be written yet) but should be filled in once the
    eval transcript exists, so the overlap checker can also catch a training
    seed that independently produced near-identical text."""

    model_config = ConfigDict(extra="forbid")

    seed_id: str = Field(min_length=1, max_length=100)
    source_id: str | None = None
    content_hash: str | None = None
    reserved_for: Literal["evaluation"]
    reserved_by: str = Field(min_length=1, max_length=120)
    reserved_at: str = Field(min_length=1, max_length=40)  # ISO date/datetime, kept as text
    note: str = Field(default="", max_length=300)


def load_registry(path: Path) -> list[ReservedSeedEntry]:
    """Empty file (or a file that doesn't exist yet) is valid -- an empty
    registry, not an error, since the registry starts empty until the
    teammate reserves their first seed."""
    if not path.exists():
        return []
    entries: list[ReservedSeedEntry] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entries.append(ReservedSeedEntry.model_validate_json(line))
        except ValidationError as exc:
            raise ValueError(f"Invalid reserved-seed entry at {path}:{line_number}: {exc}") from exc
    return entries


def reserved_seed_ids(entries: Sequence[ReservedSeedEntry]) -> set[str]:
    return {entry.seed_id for entry in entries}


def reserved_content_hashes(entries: Sequence[ReservedSeedEntry]) -> set[str]:
    return {entry.content_hash for entry in entries if entry.content_hash}
