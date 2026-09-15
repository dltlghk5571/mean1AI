"""Shared reviewed-catalog reads for the citizen agent and MCP adapter."""

import re
from datetime import UTC, datetime

from app.service_data_schemas import PublicService, RequiredInformation, ServiceCard
from app.services.pii import redact_pii
from app.services.service_catalog import ActiveCatalog

COMMON_REQUIREMENTS = [
    RequiredInformation(
        field_id="content", question="어떤 불편을 겪으셨나요? 상황을 편하게 알려 주세요."
    ),
    RequiredInformation(
        field_id="location_text",
        question="어디에서 있었던 일인가요? 주변 시설 이름을 알려 주세요.",
        required=False,
    ),
]


def service_card(service: PublicService, catalog: ActiveCatalog) -> ServiceCard:
    source = catalog.document(service.source_document_id)
    return ServiceCard(
        service_id=service.id,
        title=service.title,
        summary=service.summary,
        source_url=source.source_url,
        source_title=source.title,
        catalog_version=catalog.version,
        review_due_at=catalog.review_due_at.isoformat(),
        synthetic=source.synthetic,
        requires_human_review=service.requires_human_review,
    )


def search_services(catalog: ActiveCatalog | None, query: str, limit: int) -> list[ServiceCard]:
    if not catalog:
        return []
    query = redact_pii(query).text.casefold()
    tokens = {word for word in re.findall(r"[가-힣a-z0-9]+", query) if len(word) >= 2}
    # Retrieval baseline only; this never classifies an intent, department, or complaint.
    scored = [
        (sum(token in f"{item.title} {item.summary}".casefold() for token in tokens), item)
        for item in catalog.services(datetime.now(UTC).date())
    ]
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [
        service_card(item, catalog) for score, item in scored if not query.strip() or score > 0
    ][:limit]


def get_service(catalog: ActiveCatalog, service_id: str) -> PublicService | None:
    return next(
        (item for item in catalog.services(datetime.now(UTC).date()) if item.id == service_id), None
    )
