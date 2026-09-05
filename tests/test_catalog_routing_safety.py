import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.schemas import ClassificationCandidate, ClassificationResult
from app.services.classifier import DepartmentCatalog, RuleBasedClassifier
from app.services.department_catalog import import_department_catalog
from app.services.openai_classifier import OpenAIClassifier, _AIClassification
from app.services.pipeline import ComplaintPipeline


def _result(**overrides) -> ClassificationResult:
    fields = {
        "category": "streetlight",
        "subcategory": "가로등·보안등 고장",
        "candidates": [
            ClassificationCandidate(
                department_id="ROAD_LIGHTING", confidence=0.99, reason="합성 제공자 결과"
            )
        ],
        "evidence_summary": "합성 테스트",
        "provider": "mock",
    }
    fields.update(overrides)
    return ClassificationResult(**fields)


def _candidate(department_id: str = "ROAD_LIGHTING", **overrides) -> ClassificationCandidate:
    return ClassificationCandidate(
        department_id=department_id, confidence=0.99, reason="합성 후보", **overrides
    )


def _create(client: TestClient, **overrides) -> dict:
    payload = {
        "title": "합성 가로등 고장",
        "content": "합성 시험동 가로등 불이 꺼져 깜빡입니다.",
        "location_text": "합성 시험동",
        "channel": "web",
    }
    payload.update(overrides)
    response = client.post("/api/v1/complaints", json=payload)
    assert response.status_code == 201
    return response.json()


def _assert_review(client: TestClient, complaint: dict, reason: str) -> None:
    assert complaint["requires_human_review"] is True
    assert complaint["assigned_department_id"] is None
    assert complaint["status"] in {"needs_review", "urgent_review"}
    detail = client.get(f"/api/v1/complaints/{complaint['id']}").json()
    review = next(
        event
        for event in reversed(detail["audit_events"])
        if event["action"] == "routing_review_required"
    )
    assert reason in review["details"]["reasons"]
    assert review["details"]["external_system_connected"] is False
    assert len(review["details"]["source_sha256"]) == 64
    triage = next(
        event for event in reversed(detail["audit_events"]) if event["action"] == "triage_completed"
    )
    assert reason in triage["details"]["review_reasons"]


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (
            _result(candidates=[_candidate("UNKNOWN"), _candidate()]),
            "unknown_or_inactive_department",
        ),
        (_result(candidates=[_candidate("UNKNOWN")]), "no_active_catalog_department"),
        (_result(candidates=[_candidate(), _candidate()]), "duplicate_department_candidate"),
        (
            _result(candidates=[_candidate(), _candidate("ROAD_MAINTENANCE")]),
            "ambiguous_department_candidates",
        ),
        (
            _result(candidates=[_candidate(catalog_version="superseded-v0")]),
            "candidate_catalog_version_mismatch",
        ),
        (
            _result(candidates=[_candidate(work_assignment_ids=["WA-RM-01"])]),
            "invalid_work_assignment_reference",
        ),
        (_result(category="welfare"), "category_department_mismatch"),
        (_result(subcategory="확인되지 않은 업무"), "unmatched_work_assignment"),
        (_result(missing_information=["합성 추가 정보"]), "missing_information"),
        (
            _result(category="other", candidates=[_candidate("CIVIL_COORDINATION")]),
            "fallback_department",
        ),
    ],
)
def test_invalid_and_ambiguous_provider_routes_require_audited_review(
    client: TestClient, test_app: FastAPI, result: ClassificationResult, reason: str
) -> None:
    test_app.state.pipeline.classifier = Mock(
        provider_name="mock", classify=Mock(return_value=result)
    )
    complaint = _create(client)
    _assert_review(client, complaint, reason)
    assert all(
        candidate["department_id"] != "UNKNOWN" for candidate in complaint["candidate_departments"]
    )


def test_legacy_provider_without_provenance_binds_a_valid_route(
    client: TestClient, test_app: FastAPI
) -> None:
    test_app.state.pipeline.classifier = Mock(
        provider_name="mock", classify=Mock(return_value=_result())
    )
    complaint = _create(client)
    assert complaint["status"] == "assigned"
    assert complaint["assigned_department_id"] == "ROAD_LIGHTING"
    candidate = complaint["candidate_departments"][0]
    assert candidate["catalog_version"] == test_app.state.pipeline.catalog.catalog_version
    assert candidate["work_assignment_ids"] == ["WA-RL-01", "WA-RL-02"]


def test_location_rule_overrides_provider_review_flag(
    client: TestClient, test_app: FastAPI
) -> None:
    test_app.state.pipeline.classifier = Mock(
        provider_name="mock", classify=Mock(return_value=_result())
    )
    _assert_review(client, _create(client, location_text=None), "location_required")


def test_sensitive_alternate_candidate_also_requires_review(
    client: TestClient, test_app: FastAPI
) -> None:
    result = _result(
        candidates=[
            _candidate(),
            ClassificationCandidate(
                department_id="WELFARE_REVIEW", confidence=0.3, reason="합성 민감 후보"
            ),
        ]
    )
    test_app.state.pipeline.classifier = Mock(
        provider_name="mock", classify=Mock(return_value=result)
    )
    _assert_review(client, _create(client), "sensitive_category:welfare")


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        (
            "합성 가로등 불이 꺼졌고 지원 대상 여부를 결정해 주세요.",
            "sensitive_signal:welfare_eligibility",
        ),
        (
            "합성 가로등 불이 꺼졌고 아동학대 신고가 필요합니다.",
            "sensitive_signal:abuse_or_violence",
        ),
        ("합성 가로등 불이 꺼졌고 가스 누출이 있습니다.", "urgent_safety_signal"),
    ],
)
def test_high_confidence_never_overrides_safety_policy(
    client: TestClient, test_app: FastAPI, content: str, reason: str
) -> None:
    test_app.state.pipeline.classifier = Mock(
        provider_name="mock", classify=Mock(return_value=_result())
    )
    _assert_review(client, _create(client, content=content), reason)


def _successor(tmp_path: Path, *, expires: bool = False) -> DepartmentCatalog:
    data = json.loads(Settings().departments_path.read_text(encoding="utf-8"))
    data.update(supersedes=data["catalog_version"], catalog_version="demo-m2-routing.v2")
    if expires:
        data["effective_until"] = date.today().isoformat()
    path = tmp_path / "successor.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return DepartmentCatalog.from_json(path)


def _activate(test_app: FastAPI, catalog: DepartmentCatalog) -> None:
    old = test_app.state.pipeline
    test_app.state.pipeline = ComplaintPipeline(
        settings=old.settings,
        catalog=catalog,
        classifier=RuleBasedClassifier(catalog),
        retriever=old.retriever,
    )


def test_successor_invalidates_auto_routes_and_stale_workers_fail_closed(
    client: TestClient, test_app: FastAPI, tmp_path: Path
) -> None:
    original = _create(client)
    assert original["status"] == "assigned"
    successor = _successor(tmp_path)
    with test_app.state.session_factory() as db:
        import_department_catalog(db, successor)
    detail = client.get(f"/api/v1/complaints/{original['id']}").json()
    assert detail["status"] == "needs_review"
    assert detail["assigned_department_id"] is None
    assert detail["candidate_departments"] == original["candidate_departments"]
    assert detail["audit_events"][-1]["action"] == "catalog_route_invalidated"
    _assert_review(client, _create(client), "catalog_superseded")
    approval = {"department_id": "ROAD_LIGHTING", "answer_draft": original["answer_draft"]}
    assert (
        client.post(f"/api/v1/complaints/{original['id']}/approve", json=approval).status_code
        == 400
    )
    blocked = client.get(f"/api/v1/complaints/{original['id']}").json()
    assert blocked["audit_events"][-1]["action"] == "human_review_blocked"
    assert blocked["audit_events"][-1]["details"]["reason"] == "catalog_superseded"
    assert client.get(f"/api/v1/complaints/{original['id']}/reviews").json() == []

    _activate(test_app, successor)
    refreshed = client.post(f"/api/v1/complaints/{original['id']}/reprocess").json()
    assert refreshed["candidate_departments"][0]["catalog_version"] == successor.catalog_version
    assert refreshed["status"] == "assigned"
    approval["answer_draft"] = refreshed["answer_draft"]
    assert (
        client.post(f"/api/v1/complaints/{original['id']}/approve", json=approval).status_code
        == 200
    )


def test_expiration_is_rechecked_after_loading_for_import_routing_and_approval(
    client: TestClient, test_app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _successor(tmp_path, expires=True)
    with test_app.state.session_factory() as db:
        import_department_catalog(db, catalog)
    _activate(test_app, catalog)
    tomorrow = date.today() + timedelta(days=1)
    fake_date = Mock(today=Mock(return_value=tomorrow))
    monkeypatch.setattr("app.services.classifier.date", fake_date)
    provider = Mock(
        provider_name="mock",
        classify=Mock(side_effect=AssertionError("expired catalog reached provider")),
    )
    test_app.state.pipeline.classifier = provider
    complaint = _create(client)
    _assert_review(client, complaint, "catalog_not_effective")
    provider.classify.assert_not_called()
    with test_app.state.session_factory() as db, pytest.raises(ValueError, match="expired"):
        import_department_catalog(db, catalog)
    approval = client.post(
        f"/api/v1/complaints/{complaint['id']}/approve",
        json={"department_id": "ROAD_LIGHTING", "answer_draft": complaint["answer_draft"]},
    )
    assert approval.status_code == 400


def test_mocked_openai_cannot_silently_drop_an_invalid_candidate(
    client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = _result(candidates=[_candidate("UNKNOWN"), _candidate()]).model_dump()
    output.pop("provider")
    output.pop("review_reasons")
    for candidate in output["candidates"]:
        candidate.pop("catalog_version")
        candidate.pop("work_assignment_ids")
    parsed = _AIClassification.model_validate(output)
    sdk = Mock()
    sdk.responses.parse.return_value = SimpleNamespace(output_parsed=parsed)
    monkeypatch.setattr("openai.OpenAI", Mock(return_value=sdk))
    test_app.state.pipeline.classifier = OpenAIClassifier(
        api_key="synthetic-unused-key",
        model="synthetic-model",
        catalog=test_app.state.pipeline.catalog,
    )
    _assert_review(client, _create(client), "unknown_or_inactive_department")
    sdk.responses.parse.assert_called_once()


def test_inactive_catalog_entries_are_not_provider_candidates(tmp_path: Path) -> None:
    data = json.loads(Settings().departments_path.read_text(encoding="utf-8"))
    data["departments"][7]["active"] = False
    path = tmp_path / "inactive.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    catalog = DepartmentCatalog.from_json(path)
    result = catalog.bind_classification(
        _result(candidates=[_candidate(), _candidate("SAFETY_DUTY")])
    )
    assert [candidate.department_id for candidate in result.candidates] == ["ROAD_LIGHTING"]
    assert "unknown_or_inactive_department" in result.review_reasons
    assert result.requires_human_review
    assert all(row["id"] != "SAFETY_DUTY" for row in catalog.as_prompt_data())


def test_rules_require_review_for_competing_work_assignments(tmp_path: Path) -> None:
    data = json.loads(Settings().departments_path.read_text(encoding="utf-8"))
    rules = data["departments"][0]["routing_rules"]
    other_rule = dict(
        rules[0],
        id="RULE-LIGHTING-SYNTH",
        subcategory="합성 다른 업무",
        work_assignment_ids=["WA-RL-01"],
    )
    rules.append(other_rule)
    path = tmp_path / "ambiguous.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    result = RuleBasedClassifier(DepartmentCatalog.from_json(path)).classify(
        title="합성 가로등", text="합성 가로등 불이 꺼져 깜빡입니다.", location_text="합성 시험동"
    )
    assert result.requires_human_review
    assert "ambiguous_work_assignments" in result.review_reasons
