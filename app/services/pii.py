import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RedactionResult:
    text: str
    detected_types: list[str]
    counts: dict[str, int]


_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "resident_registration_number",
        re.compile(r"(?<!\d)\d{6}\s*-\s*[1-8]\d{6}(?!\d)"),
        "[주민등록번호]",
    ),
    (
        "email",
        re.compile(
            r"(?i)(?<![a-z0-9._%+-])[a-z0-9._%+-]+@[a-z0-9-]+"
            r"(?:\.[a-z0-9-]+)+(?![a-z0-9_.-])"
        ),
        "[이메일]",
    ),
    (
        "mobile_phone",
        re.compile(r"(?<!\d)01[016789][\s-]?\d{3,4}[\s-]?\d{4}(?!\d)"),
        "[전화번호]",
    ),
    (
        "landline_phone",
        re.compile(r"(?<!\d)0(?:2|[3-6][1-5])[\s-]?\d{3,4}[\s-]?\d{4}(?!\d)"),
        "[전화번호]",
    ),
)


def redact_pii(text: str) -> RedactionResult:
    redacted = text
    counts: dict[str, int] = {}
    for pii_type, pattern, replacement in _PATTERNS:
        redacted, count = pattern.subn(replacement, redacted)
        if count:
            counts[pii_type] = count
    return RedactionResult(
        text=redacted,
        detected_types=sorted(counts),
        counts=counts,
    )
