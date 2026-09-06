"""A bounded planner/tool loop. No submission, network or arbitrary SQL tool exists."""

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import BoundedSemaphore
from typing import Protocol

from sqlalchemy.orm import Session

from app.agent_schemas import (
    STEP_ADAPTER,
    AnswerStep,
    AskStep,
    ClarifyStep,
    GetRequiredInformation,
    PlanningContext,
    ReviewStep,
    SearchServices,
    ServiceCard,
    ToolObservation,
    ToolStep,
)
from app.chat_schemas import AgentContext, AgentReply
from app.service_data_schemas import PublicService, RequiredInformation
from app.services.pii import redact_pii
from app.services.service_catalog import ActiveCatalog, active_catalog

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


class AgentPlanner(Protocol):
    def plan(self, context: PlanningContext) -> dict[str, object]: ...


class DemoToolPlanner:
    """Explicit-stage simulator; never classifies free text by keywords."""

    def plan(self, context: PlanningContext) -> dict[str, object]:
        if context.stage == "information":
            if not context.observations:
                return {
                    "kind": "tool",
                    "call": {
                        "name": "search_services",
                        "call_id": "services",
                        "query": context.draft.content[:2000],
                        "limit": 3,
                    },
                }
            return {
                "kind": "answer",
                "service_ids": [item.service_id for item in context.observations[-1].services],
            }
        if context.stage in {"description", "location"}:
            if not context.observations:
                return {
                    "kind": "tool",
                    "call": {"name": "get_required_information", "call_id": "requirements"},
                }
            return {
                "kind": "ask",
                "field_id": "content" if context.stage == "description" else "location_text",
            }
        return {"kind": "review" if context.stage == "review" else "clarify"}


class AgentRunError(ValueError):
    def __init__(self, events: list[dict[str, object]]) -> None:
        super().__init__("agent_execution_failed")
        self.events = events


@dataclass(frozen=True)
class AgentExecution:
    reply: AgentReply
    cards: list[ServiceCard]
    events: list[dict[str, object]]
    catalog_review_id: int | None

    def verify_catalog(self, db: Session) -> None:
        current = active_catalog(db)
        if (current.review_id if current else None) != self.catalog_review_id:
            raise ValueError("catalog_changed_during_agent_turn")


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


class CitizenAgentExecutor:
    def __init__(self, planner: AgentPlanner, *, timeout: float = 30, concurrency: int = 4) -> None:
        self.planner = planner
        self.timeout = timeout
        self.capacity = BoundedSemaphore(concurrency)

    def execute(self, db: Session, context: AgentContext) -> AgentExecution:
        if not self.capacity.acquire(blocking=False):
            raise AgentRunError([{"status": "busy"}])
        try:
            return self._execute(db, context, time.monotonic() + self.timeout)
        finally:
            self.capacity.release()

    def _execute(self, db: Session, context: AgentContext, deadline: float) -> AgentExecution:
        catalog = active_catalog(db)
        events: list[dict[str, object]] = []
        observations: list[ToolObservation] = []
        selected: dict[str, ServiceCard] = {}
        requirements = {item.field_id: item for item in COMMON_REQUIREMENTS}
        call_ids: set[str] = set()
        # Defense in depth for direct use outside the citizen route.
        safe_context = AgentContext.model_validate_json(redact_pii(context.model_dump_json()).text)
        safe_context.state.service_cards = []
        try:
            for _ in range(4):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("agent_deadline_exceeded")
                planning = PlanningContext(
                    stage=safe_context.expected_stage,
                    draft=safe_context.state.draft.model_copy(deep=True),
                    messages=[item.model_copy(deep=True) for item in safe_context.state.messages],
                    observations=[item.model_copy(deep=True) for item in observations],
                    remaining_tool_calls=3 - len(call_ids),
                    time_budget_seconds=min(60, remaining),
                )
                step = STEP_ADAPTER.validate_python(self.planner.plan(planning))
                if time.monotonic() >= deadline:
                    raise ValueError("agent_deadline_exceeded")
                if isinstance(step, ToolStep):
                    call = step.call
                    if len(call_ids) >= 3 or call.call_id in call_ids:
                        raise ValueError("tool_budget_or_duplicate_call")
                    call_ids.add(call.call_id)
                    event: dict[str, object] = {
                        "tool": call.name,
                        "call_id": call.call_id,
                        "catalog_version": catalog.version if catalog else None,
                        "status": "failed",
                    }
                    events.append(event)
                    observation = self._tool(call, catalog, selected)
                    observations.append(observation)
                    selected.update({item.service_id: item for item in observation.services})
                    requirements.update(
                        {item.field_id: item for item in observation.required_information}
                    )
                    event.update(
                        {
                            "status": "completed",
                            "service_ids": [item.service_id for item in observation.services],
                            "field_ids": [
                                item.field_id for item in observation.required_information
                            ],
                        }
                    )
                    continue
                stage = safe_context.expected_stage
                cards: list[ServiceCard] = []
                if isinstance(step, AskStep) and stage in {"description", "location"}:
                    expected_field = "content" if stage == "description" else "location_text"
                    if step.field_id != expected_field or step.field_id not in requirements:
                        raise ValueError("unsupported_question_field")
                    message = requirements[step.field_id].question
                elif isinstance(step, AnswerStep) and stage == "information":
                    if not observations or any(key not in selected for key in step.service_ids):
                        raise ValueError("ungrounded_service_id")
                    cards = [selected[key] for key in dict.fromkeys(step.service_ids)]
                    message = (
                        "검수된 자료에서 관련 안내를 찾았어요. 아래 내용과 출처를 확인해 주세요. "
                        "지원 대상 여부와 최종 행정 판단은 담당자 확인이 필요해요."
                        if cards
                        else "지금 답변할 수 있는 검수 자료를 찾지 못했어요. "
                        "공식 안내 사이트를 확인하거나 민원으로 문의해 주세요."
                    )
                    if any(card.synthetic for card in cards):
                        message = (
                            "아래는 합성 자료로 검색 흐름을 체험하는 예시예요. "
                            "실제 성남시 제도 안내가 아닙니다."
                        )
                elif isinstance(step, ReviewStep) and stage == "review":
                    message = "접수할 내용을 모았어요. 내용을 확인하거나 고친 뒤 접수해 주세요."
                elif isinstance(step, ClarifyStep) and stage == "intent":
                    message = "이 이야기를 민원으로 접수할까요, 아니면 관련 정보를 알아볼까요?"
                else:
                    raise ValueError("terminal_action_not_allowed")
                events.append({"step_kind": step.kind, "status": "completed"})
                return AgentExecution(
                    AgentReply(
                        next_stage=stage,
                        message=message,
                        source_ids=["bokjiro", "seongnam_handbook"]
                        if stage == "information" and not cards
                        else [],
                    ),
                    cards,
                    events,
                    catalog.review_id if catalog else None,
                )
            raise ValueError("agent_step_budget_exceeded")
        except Exception:
            raise AgentRunError(events) from None

    def _tool(
        self,
        call: SearchServices | GetRequiredInformation,
        catalog: ActiveCatalog | None,
        selected: dict[str, ServiceCard],
    ) -> ToolObservation:
        observation = ToolObservation(
            call_id=call.call_id, name=call.name, catalog_available=catalog is not None
        )
        if isinstance(call, SearchServices):
            if catalog:
                query = redact_pii(call.query).text.casefold()
                tokens = {word for word in re.findall(r"[가-힣a-z0-9]+", query) if len(word) >= 2}
                candidates = catalog.services(datetime.now(UTC).date())
                # A lexical retrieval baseline only; no intent/category/department assignment.
                scored = [
                    (
                        sum(token in f"{item.title} {item.summary}".casefold() for token in tokens),
                        item,
                    )
                    for item in candidates
                ]
                scored.sort(key=lambda pair: (-pair[0], pair[1].id))
                observation.services = [
                    service_card(item, catalog)
                    for score, item in scored
                    if not query.strip() or score > 0
                ][: call.limit]
        elif call.service_id is None:
            observation.required_information = COMMON_REQUIREMENTS
        else:
            if not catalog or call.service_id not in selected:
                raise ValueError("service_not_in_retrieved_candidates")
            service = next(
                item
                for item in catalog.services(datetime.now(UTC).date())
                if item.id == call.service_id
            )
            observation.required_information = service.required_information
        return observation
