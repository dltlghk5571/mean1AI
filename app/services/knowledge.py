import re
from dataclasses import dataclass
from datetime import date
from math import sqrt
from pathlib import Path

from app.services.pii import redact_pii

APPROVAL_STATUSES = frozenset({"approved", "draft", "revoked"})
RETRIEVAL_STRATEGY = "strict_lexical_v1"


@dataclass(frozen=True)
class KnowledgeDocument:
    id: str
    title: str
    category: str
    version: str
    effective_from: date
    effective_until: date | None
    approval_status: str
    superseded_by: str | None
    body: str
    path: Path

    @property
    def approved(self) -> bool:
        return self.approval_status == "approved"

    def is_effective(self, as_of: date) -> bool:
        return self.effective_from <= as_of and (
            self.effective_until is None or as_of <= self.effective_until
        )


@dataclass(frozen=True)
class RetrievalExclusion:
    document_id: str
    reason: str


@dataclass(frozen=True)
class RetrievalResult:
    documents: list[KnowledgeDocument]
    scores: dict[str, float]
    excluded: list[RetrievalExclusion]
    strategy: str = RETRIEVAL_STRATEGY


_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_TOKEN = re.compile(r"[가-힣A-Za-z0-9]{2,}")
_STOPWORDS = frozenset(
    {
        "그리고",
        "관련",
        "민원",
        "시연용",
        "실제",
        "요청",
        "확인",
        "합니다",
        "있습니다",
        "대한",
    }
)
_PROMPT_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?previous", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
    re.compile(r"이전\s*(지시|명령).{0,12}무시"),
    re.compile(r"시스템\s*(프롬프트|지시).{0,12}(공개|출력|변경)"),
)
_AUTOMATED_DISPOSITION_PATTERN = re.compile(
    r"(담당자\s*검토\s*없이|사람\s*검토\s*없이|자동으로|즉시).{0,40}"
    r"(종결|거부|기각|승인|확정|부과|취소|처분|과태료|보상).{0,20}"
    r"(한다|하라|하세요|처리|결정|확정)",
    re.IGNORECASE,
)


def tokenize(text: str) -> set[str]:
    return {token for token in _TOKEN.findall(text.lower()) if token not in _STOPWORDS}


def content_safety_violation(text: str) -> str | None:
    if any(pattern.search(text) for pattern in _PROMPT_INJECTION_PATTERNS):
        return "prompt_injection"
    if _AUTOMATED_DISPOSITION_PATTERN.search(text):
        return "automatic_disposition"
    return None


def _parse_date(value: str, *, field: str, path: Path) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {field} date in {path}: {value}") from exc


def load_document(path: Path) -> KnowledgeDocument:
    raw = path.read_text(encoding="utf-8")
    match = _FRONT_MATTER.match(raw)
    if not match:
        raise ValueError(f"Missing front matter: {path}")

    metadata: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        metadata[key.strip()] = value.strip()

    required = {
        "id",
        "title",
        "category",
        "version",
        "effective_from",
        "effective_until",
        "approval_status",
        "superseded_by",
    }
    missing = required - metadata.keys()
    if missing:
        raise ValueError(f"Missing metadata {sorted(missing)} in {path}")

    approval_status = metadata["approval_status"].lower()
    if approval_status not in APPROVAL_STATUSES:
        raise ValueError(f"Invalid approval_status in {path}: {approval_status}")

    effective_from = _parse_date(metadata["effective_from"], field="effective_from", path=path)
    if effective_from is None:
        raise ValueError(f"effective_from cannot be blank in {path}")
    effective_until = _parse_date(metadata["effective_until"], field="effective_until", path=path)
    if effective_until is not None and effective_until < effective_from:
        raise ValueError(f"effective_until precedes effective_from in {path}")

    superseded_by = metadata["superseded_by"] or None
    document_id = metadata["id"]
    if superseded_by == document_id:
        raise ValueError(f"Document cannot supersede itself: {path}")

    return KnowledgeDocument(
        id=document_id,
        title=metadata["title"],
        category=metadata["category"],
        version=metadata["version"],
        effective_from=effective_from,
        effective_until=effective_until,
        approval_status=approval_status,
        superseded_by=superseded_by,
        body=match.group(2).strip(),
        path=path,
    )


class KnowledgeRetriever:
    def __init__(self, directory: Path) -> None:
        self.documents = [load_document(path) for path in sorted(directory.glob("*.md"))]
        self.by_id = {document.id: document for document in self.documents}
        if len(self.by_id) != len(self.documents):
            raise ValueError("Knowledge document IDs must be unique")
        unknown_superseders = {
            document.superseded_by
            for document in self.documents
            if document.superseded_by and document.superseded_by not in self.by_id
        }
        if unknown_superseders:
            raise ValueError(
                "Unknown superseded_by document IDs: " + ", ".join(sorted(unknown_superseders))
            )

    def _eligibility_reason(self, document: KnowledgeDocument, as_of: date) -> str | None:
        if not document.approved:
            return f"approval_status:{document.approval_status}"
        if not document.is_effective(as_of):
            return "not_effective"
        if document.superseded_by:
            successor = self.by_id[document.superseded_by]
            if successor.approved and successor.is_effective(as_of):
                return f"superseded_by:{successor.id}"
        violation = content_safety_violation(f"{document.title}\n{document.body}")
        if violation:
            return f"unsafe_content:{violation}"
        if redact_pii(f"{document.title}\n{document.body}").detected_types:
            return "unsafe_content:personal_information"
        return None

    def eligible_documents(
        self, *, category: str, as_of: date | None = None
    ) -> list[KnowledgeDocument]:
        effective_date = as_of or date.today()
        return [
            document
            for document in self.documents
            if document.category == category
            and self._eligibility_reason(document, effective_date) is None
        ]

    def retrieve(
        self,
        *,
        category: str,
        text: str,
        limit: int = 3,
        as_of: date | None = None,
    ) -> RetrievalResult:
        effective_date = as_of or date.today()
        query_tokens = tokenize(text)
        scored: list[tuple[float, KnowledgeDocument]] = []
        excluded: list[RetrievalExclusion] = []

        for document in self.documents:
            reason = self._eligibility_reason(document, effective_date)
            if reason is None and document.category != category:
                reason = "category_mismatch"
            if reason:
                excluded.append(RetrievalExclusion(document.id, reason))
                continue

            document_tokens = tokenize(f"{document.title} {document.body}")
            overlap = query_tokens & document_tokens
            if not overlap:
                excluded.append(RetrievalExclusion(document.id, "no_lexical_overlap"))
                continue
            score = len(overlap) / sqrt(len(query_tokens) * len(document_tokens))
            scored.append((score, document))

        scored.sort(key=lambda item: (-item[0], item[1].id))
        selected = scored[: max(0, limit)]
        return RetrievalResult(
            documents=[document for _, document in selected],
            scores={document.id: round(score, 6) for score, document in selected},
            excluded=excluded,
        )
