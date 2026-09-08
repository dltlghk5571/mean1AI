"""Shared, dependency-free text-dedup primitives.

Used by both the training-data generator (evals/chat_training_gen.py, to skip
exact duplicates within a single generation run) and the train/eval overlap
checker (evals/chat_overlap_check.py, to catch leakage across the two
datasets). One implementation so both call sites agree on what "the same
text" means. No embedding model or third-party dependency -- normalized
SHA-256 for exact matches, character 5-gram Jaccard for near-duplicates.
"""

from __future__ import annotations

import hashlib
import re

_WHITESPACE = re.compile(r"\s+")
SHINGLE_SIZE = 5
# ponytail: fixed threshold, tune from real overlap-check runs if it proves
# too strict/loose once real data exists.
NEAR_DUPLICATE_THRESHOLD = 0.85


def normalize_text(text: str) -> str:
    """Collapse whitespace and casefold so trivial formatting differences
    don't defeat exact-match dedup."""
    return _WHITESPACE.sub(" ", text).strip().casefold()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def shingles(text: str, n: int = SHINGLE_SIZE) -> set[str]:
    """Character n-grams of the normalized text. Empty/short text yields a
    single shingle of whatever it has, so it never spuriously matches
    everything via an empty set."""
    normalized = normalize_text(text)
    if len(normalized) <= n:
        return {normalized} if normalized else set()
    return {normalized[i : i + n] for i in range(len(normalized) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_near_duplicate(
    text_a: str, text_b: str, *, threshold: float = NEAR_DUPLICATE_THRESHOLD
) -> bool:
    return jaccard(shingles(text_a), shingles(text_b)) >= threshold
