from app.services.pii import redact_pii


def test_redacts_supported_direct_identifiers() -> None:
    raw = (
        "주민번호 900101-1234567, 휴대폰 010-1234-5678, "
        "전화 031-123-4567, 이메일 citizen@example.com"
    )

    result = redact_pii(raw)

    assert "900101-1234567" not in result.text
    assert "010-1234-5678" not in result.text
    assert "031-123-4567" not in result.text
    assert "citizen@example.com" not in result.text
    assert result.text.count("[전화번호]") == 2
    assert set(result.detected_types) == {
        "resident_registration_number",
        "mobile_phone",
        "landline_phone",
        "email",
    }


def test_leaves_non_identifier_numbers_untouched() -> None:
    result = redact_pii("가로등 2개가 3일째 꺼져 있습니다.")

    assert result.text == "가로등 2개가 3일째 꺼져 있습니다."
    assert result.detected_types == []


def test_redacts_email_next_to_korean_postposition() -> None:
    result = redact_pii("합성 이메일은 citizen@example.com입니다.")

    assert result.text == "합성 이메일은 [이메일]입니다."
    assert result.detected_types == ["email"]
    assert result.counts == {"email": 1}
