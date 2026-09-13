"""Public catalog reads with an atomic, content-free MCP access journal."""

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.mcp_schemas import CatalogReference, CatalogToolResult, RequirementsInput, SearchInput
from app.models import MCPToolAuditEvent
from app.services.catalog_tools import (
    COMMON_REQUIREMENTS,
    get_service,
    search_services,
    service_card,
)
from app.services.pii import redact_pii
from app.services.service_catalog import active_catalog

TOOL_NAMES = {"search_services", "get_required_information"}


def unavailable_error() -> CatalogToolResult:
    return CatalogToolResult(
        status="error", message="자료를 확인하지 못했어요. 잠시 후 다시 시도해 주세요."
    )


class CatalogToolGateway:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def call(self, name: str, arguments: dict[str, object]) -> CatalogToolResult:
        tool = name if name in TOOL_NAMES else "unknown"
        try:
            with self.session_factory() as db:
                # This prototype uses SQLite. Hold publication/withdrawal stable until the
                # corresponding access journal is committed; never return an unaudited read.
                if db.get_bind().dialect.name != "sqlite":
                    return unavailable_error()
                db.execute(text("BEGIN IMMEDIATE"))
                try:
                    result = self._read(db, tool, arguments)
                except ValidationError:
                    result = CatalogToolResult(
                        status="invalid_input",
                        message="도구에 표시된 입력 항목과 길이를 확인해 주세요.",
                    )
                except Exception:
                    result = unavailable_error()
                db.add(
                    MCPToolAuditEvent(
                        tool=tool,
                        status=result.status,
                        catalog_version=result.catalog.version if result.catalog else None,
                        catalog_review_id=result.catalog.review_id if result.catalog else None,
                        service_ids=[item.service_id for item in result.services],
                    )
                )
                db.commit()
                return result
        except Exception:
            return unavailable_error()

    @staticmethod
    def _read(db: Session, tool: str, arguments: dict[str, object]) -> CatalogToolResult:
        if tool == "unknown":
            return CatalogToolResult(status="invalid_input", message="지원하지 않는 도구예요.")
        call = (
            SearchInput.model_validate(arguments)
            if tool == "search_services"
            else RequirementsInput.model_validate(arguments)
        )
        if isinstance(call, RequirementsInput) and call.service_id is None:
            return CatalogToolResult(
                status="ok",
                message="앱에서 정한 공통 입력 항목이에요. 공식 업무별 필수 서류 목록은 아닙니다.",
                required_information=COMMON_REQUIREMENTS,
            )
        catalog = active_catalog(db)
        if not catalog:
            return CatalogToolResult(
                status="catalog_unavailable",
                message="사용할 수 있는 검수 자료가 없어요. 공식 안내나 담당자 확인이 필요해요.",
            )
        # Defense in depth if a database is corrupted outside the import/review paths.
        if redact_pii(catalog.bundle.model_dump_json()).detected_types:
            return unavailable_error()
        reference = CatalogReference(
            version=catalog.version,
            review_id=catalog.review_id,
            content_hash=catalog.content_hash,
        )
        if isinstance(call, SearchInput):
            cards = search_services(catalog, call.query, call.limit)
            message = (
                "검수 자료의 관련 안내예요. 개별 적용 여부와 행정 판단은 담당자 확인이 필요해요."
                if cards
                else "관련 검수 자료를 찾지 못했어요. 검색어를 바꾸거나 담당자에게 문의해 주세요."
            )
            if any(card.synthetic for card in cards):
                message = "합성 자료로 검색을 체험하는 예시예요. 실제 성남시 제도 안내가 아닙니다."
            return CatalogToolResult(
                status="ok", message=message, catalog=reference, services=cards
            )
        if call.catalog != reference:
            return CatalogToolResult(
                status="stale_catalog",
                message="자료가 변경되었어요. 다시 검색한 결과로 조회해 주세요.",
            )
        assert call.service_id is not None
        service = get_service(catalog, call.service_id)
        if service is None:
            return CatalogToolResult(
                status="not_found", message="현재 유효한 업무를 찾지 못했어요. 다시 검색해 주세요."
            )
        card = service_card(service, catalog)
        return CatalogToolResult(
            status="ok",
            message=(
                "합성 업무의 시연용 질문이에요. 실제 성남시의 필수 서류 목록이 아닙니다."
                if card.synthetic
                else "검수된 업무의 입력 안내예요. 공식 원문과 담당자 안내를 함께 확인해 주세요."
            ),
            catalog=reference,
            services=[card],
            required_information=service.required_information,
        )
