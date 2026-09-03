import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class KnowledgeDocument:
    id: str
    title: str
    category: str
    version: str
    approved: bool
    body: str
    path: Path


_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)
_TOKEN = re.compile(r"[가-힣A-Za-z0-9]{2,}")


def _parse_document(path: Path) -> KnowledgeDocument:
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

    required = {"id", "title", "category", "version", "approved"}
    missing = required - metadata.keys()
    if missing:
        raise ValueError(f"Missing metadata {sorted(missing)} in {path}")

    return KnowledgeDocument(
        id=metadata["id"],
        title=metadata["title"],
        category=metadata["category"],
        version=metadata["version"],
        approved=metadata["approved"].lower() == "true",
        body=match.group(2).strip(),
        path=path,
    )


class KnowledgeRetriever:
    def __init__(self, directory: Path) -> None:
        self.documents = [_parse_document(path) for path in sorted(directory.glob("*.md"))]

    def retrieve(self, *, category: str, text: str, limit: int = 3) -> list[KnowledgeDocument]:
        query_tokens = set(_TOKEN.findall(text.lower()))
        scored: list[tuple[float, KnowledgeDocument]] = []

        for document in self.documents:
            if not document.approved:
                continue
            document_tokens = set(_TOKEN.findall(f"{document.title} {document.body}".lower()))
            overlap = len(query_tokens & document_tokens)
            category_bonus = 10 if document.category == category else 0
            score = float(category_bonus + overlap)
            if score > 0:
                scored.append((score, document))

        scored.sort(key=lambda item: (-item[0], item[1].id))
        return [document for _, document in scored[:limit]]
