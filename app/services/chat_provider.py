"""Replaceable conversation interface, separate from the complaint classifier.

The demo follows explicit citizen choices. It does not classify free text or claim
to retrieve benefits. A future club-server adapter must validate AgentReply JSON.
"""

from typing import Protocol

from app.chat_schemas import AgentContext, AgentReply


class ChatAgentProvider(Protocol):
    provider_name: str

    def respond(self, context: AgentContext) -> AgentReply: ...


class DemoChatProvider:
    provider_name = "demo"

    def respond(self, context: AgentContext) -> AgentReply:
        messages = {
            "intent": "이 이야기를 민원으로 접수할까요, 아니면 관련 정보를 알아볼까요?",
            "description": (
                "어떤 불편을 겪으셨나요? 언제부터 어떤 상황인지 편하게 적어 주세요. "
                "부서는 직접 고르지 않으셔도 됩니다."
            ),
            "location": (
                "어디에서 있었던 일인가요? 주변 건물이나 시설 이름을 알려 주세요. "
                "정확한 주소를 몰라도 괜찮아요."
            ),
            "review": "접수할 내용을 모았어요. 아래 내용을 확인하고, 필요한 부분은 고쳐 주세요.",
            "information": (
                "현재는 대화 흐름을 체험하는 시연 모드예요. 복지·생활정보 검색은 아직 "
                "연결되지 않았어요. 복지로에서 지원 제도를, 성남시 민원편람에서 신청 절차를 "
                "확인할 수 있어요. 대상 여부와 지원 금액은 공식 안내와 담당자에게 확인해 주세요."
            ),
        }
        return AgentReply(
            next_stage=context.expected_stage,
            message=messages[context.expected_stage],
            source_ids=["bokjiro", "seongnam_handbook"]
            if context.expected_stage == "information"
            else [],
        )


class UnavailableChatProvider:
    provider_name = "unavailable"

    def respond(self, context: AgentContext) -> AgentReply:
        raise RuntimeError("chat_provider_unavailable")


def build_chat_provider(name: str) -> ChatAgentProvider:
    return DemoChatProvider() if name in {"demo", "agent_demo"} else UnavailableChatProvider()
