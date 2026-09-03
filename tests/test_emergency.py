from app.schemas import Urgency
from app.services.emergency import detect_emergency


def test_detects_critical_gas_signal() -> None:
    result = detect_emergency("배관에서 가스 냄새가 심하고 누출되는 것 같습니다.")

    assert result.urgency == Urgency.CRITICAL
    assert "gas_leak" in result.signals


def test_normal_complaint_is_not_emergency() -> None:
    result = detect_emergency("공원 벤치 도장이 벗겨졌습니다.")

    assert result.urgency == Urgency.NORMAL
    assert result.signals == []
