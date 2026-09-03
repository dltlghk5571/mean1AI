from app.config import Settings
from app.services.classifier import DepartmentCatalog, RuleBasedClassifier


def make_classifier() -> RuleBasedClassifier:
    settings = Settings(app_env="test", ai_provider="rules")
    return RuleBasedClassifier(DepartmentCatalog.from_json(settings.departments_path))


def test_streetlight_routes_to_lighting_group() -> None:
    result = make_classifier().classify(
        title="가로등 고장",
        text="공원 입구 가로등 불이 꺼졌습니다.",
        location_text="정자동 공원 입구",
    )

    assert result.category == "streetlight"
    assert result.candidates[0].department_id == "ROAD_LIGHTING"
    assert result.candidates[0].confidence >= 0.90
    assert result.requires_human_review is False


def test_missing_location_requires_review() -> None:
    result = make_classifier().classify(
        title="가로등 고장",
        text="가로등 불이 꺼졌습니다.",
        location_text=None,
    )

    assert result.requires_human_review is True
    assert result.missing_information
