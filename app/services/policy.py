from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyDecision:
    requires_human_review: bool
    reasons: list[str]


_SENSITIVE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "welfare_eligibility": ("수급자", "기초생활", "복지 자격", "장애 등급", "지원 대상"),
    "permit_or_license": ("인허가", "허가", "건축 승인", "영업 허가", "재건축"),
    "fine_or_penalty": ("과태료", "벌금", "영업정지", "행정처분", "단속 취소"),
    "tax": ("세금", "지방세", "취득세", "재산세", "감면"),
    "compensation": ("손해배상", "보상금", "배상", "보상해"),
    "abuse_or_violence": ("아동학대", "가정폭력", "학대", "폭행"),
    "self_harm": ("자살", "죽고 싶", "극단적 선택", "자해"),
}

_SENSITIVE_CATEGORIES = {"welfare", "permit", "tax", "penalty", "compensation"}


def evaluate_policy(text: str, category: str) -> PolicyDecision:
    reasons: list[str] = []
    if category in _SENSITIVE_CATEGORIES:
        reasons.append(f"sensitive_category:{category}")

    for label, keywords in _SENSITIVE_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            reasons.append(f"sensitive_signal:{label}")

    return PolicyDecision(requires_human_review=bool(reasons), reasons=sorted(set(reasons)))
