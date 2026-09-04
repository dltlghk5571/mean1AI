import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.models import (
    CatalogImportEvent,
    Department,
    DepartmentCatalogEntry,
    DepartmentCatalogVersion,
)
from app.schemas import ClassificationCandidate, ClassificationResult
from app.seed import import_department_catalog
from app.services.classifier import DepartmentCatalog, RuleBasedClassifier


def _source_data() -> dict[str, Any]:
    return json.loads(Settings().departments_path.read_text(encoding="utf-8"))


def _write_catalog(tmp_path: Path, data: dict[str, Any], name: str = "catalog.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_catalog_is_explicitly_synthetic_versioned_and_drives_rules() -> None:
    catalog = DepartmentCatalog.from_json(Settings().departments_path)
    classifier = RuleBasedClassifier(catalog)

    assert catalog.catalog_version == "demo-2026-09-04.v1"
    assert catalog.synthetic is True
    assert catalog.approval_status == "approved"
    assert len(catalog.all_departments) == 9
    assert len(catalog.routing_rules) == 7
    assert len(catalog.source_sha256) == 64

    result = classifier.classify(
        title="합성 가로등 고장",
        text="가상 시험동의 가로등이 깜빡입니다.",
        location_text="가상 시험동 1번 위치",
    )

    candidate = result.candidates[0]
    assert candidate.department_id == "ROAD_LIGHTING"
    assert candidate.catalog_version == catalog.catalog_version
    assert candidate.work_assignment_ids == ["WA-RL-01", "WA-RL-02"]
    assert "RULE-STREETLIGHT" in candidate.reason


def test_rules_provider_uses_catalog_keywords_instead_of_a_code_constant(
    tmp_path: Path,
) -> None:
    data = _source_data()
    lighting_rule = data["departments"][0]["routing_rules"][0]
    lighting_rule["keywords"] = ["합성반짝표식"]
    lighting_rule["subcategory"] = "합성 카탈로그 전용 유형"
    catalog = DepartmentCatalog.from_json(_write_catalog(tmp_path, data))

    result = RuleBasedClassifier(catalog).classify(
        title="합성반짝표식 신고",
        text="가상 시설에 합성반짝표식 현상이 있습니다.",
        location_text="가상 시험동",
    )

    assert result.category == "streetlight"
    assert result.subcategory == "합성 카탈로그 전용 유형"
    assert result.candidates[0].department_id == "ROAD_LIGHTING"


def test_catalog_replaces_provider_supplied_work_assignment_ids() -> None:
    catalog = DepartmentCatalog.from_json(Settings().departments_path)
    provider_result = ClassificationResult(
        category="streetlight",
        subcategory="가로등·보안등 고장",
        candidates=[
            ClassificationCandidate(
                department_id="ROAD_LIGHTING",
                confidence=0.99,
                reason="합성 제공자 출력",
                catalog_version="spoofed-version",
                work_assignment_ids=["WA-RM-01"],
            )
        ],
        evidence_summary="합성 테스트",
        provider="synthetic-provider",
    )

    bound = catalog.bind_classification(provider_result)

    assert bound.candidates[0].catalog_version == catalog.catalog_version
    assert bound.candidates[0].work_assignment_ids == ["WA-RL-01", "WA-RL-02"]


@pytest.mark.parametrize(
    ("mutate", "expected_message"),
    [
        (lambda data: data.update({"synthetic": False}), "synthetic"),
        (
            lambda data: data["departments"][0]["routing_rules"][0].update(
                {"work_assignment_ids": ["WA-UNKNOWN"]}
            ),
            "unknown work assignments",
        ),
    ],
)
def test_catalog_rejects_non_synthetic_or_broken_references(
    tmp_path: Path,
    mutate: Any,
    expected_message: str,
) -> None:
    data = _source_data()
    mutate(data)

    with pytest.raises(ValueError, match=expected_message):
        DepartmentCatalog.from_json(_write_catalog(tmp_path, data))


def test_startup_imports_one_version_and_is_idempotent(
    client: TestClient,
    test_app: FastAPI,
) -> None:
    catalog = test_app.state.pipeline.catalog
    session_factory = test_app.state.session_factory

    response = client.get("/api/v1/departments/catalog")
    assert response.status_code == 200
    body = response.json()
    assert body["catalog_version"] == catalog.catalog_version
    assert body["synthetic"] is True
    assert body["source_sha256"] == catalog.source_sha256
    assert len(body["departments"]) == 9
    assert body["departments"][0]["work_assignments"]

    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(DepartmentCatalogVersion)) == 1
        assert db.scalar(select(func.count()).select_from(DepartmentCatalogEntry)) == 9
        assert db.scalar(select(func.count()).select_from(CatalogImportEvent)) == 1
        assert import_department_catalog(db, catalog) is False
        assert db.scalar(select(func.count()).select_from(CatalogImportEvent)) == 1


def test_reused_version_with_changed_bytes_fails_closed(
    client: TestClient,
    test_app: FastAPI,
    tmp_path: Path,
) -> None:
    del client
    data = _source_data()
    data["source_label"] = "변경된 합성 출처"
    changed = DepartmentCatalog.from_json(_write_catalog(tmp_path, data))

    with (
        test_app.state.session_factory() as db,
        pytest.raises(ValueError, match="reused with different content"),
    ):
        import_department_catalog(db, changed)


def test_new_version_keeps_snapshot_and_records_change_summary(
    client: TestClient,
    test_app: FastAPI,
    tmp_path: Path,
) -> None:
    del client
    data = copy.deepcopy(_source_data())
    data["catalog_version"] = "demo-2026-09-04.v2-test"
    data["departments"][0]["description"] = "변경 이력을 검증하는 완전 합성 설명"
    data["departments"][7]["active"] = False
    changed = DepartmentCatalog.from_json(_write_catalog(tmp_path, data, "catalog-v2.json"))

    with test_app.state.session_factory() as db:
        assert import_department_catalog(db, changed) is True
        versions = list(
            db.scalars(
                select(DepartmentCatalogVersion).order_by(DepartmentCatalogVersion.catalog_version)
            ).all()
        )
        assert [version.catalog_version for version in versions] == [
            "demo-2026-09-04.v1",
            "demo-2026-09-04.v2-test",
        ]
        assert db.scalar(select(func.count()).select_from(DepartmentCatalogEntry)) == 18
        events = list(db.scalars(select(CatalogImportEvent).order_by(CatalogImportEvent.id)).all())
        assert len(events) == 2
        assert events[-1].details["changed_department_ids"] == [
            "ROAD_LIGHTING",
            "SAFETY_DUTY",
        ]
        assert events[-1].details["deactivated_department_ids"] == ["SAFETY_DUTY"]
        assert db.get(Department, "SAFETY_DUTY").active is False


def test_catalog_history_tables_reject_updates_and_deletes(
    client: TestClient,
    test_app: FastAPI,
) -> None:
    del client
    statements = (
        "UPDATE department_catalog_versions SET source_label='tampered'",
        "DELETE FROM department_catalog_versions",
        "UPDATE department_catalog_entries SET name='tampered'",
        "DELETE FROM department_catalog_entries",
        "UPDATE catalog_import_events SET action='tampered'",
        "DELETE FROM catalog_import_events",
    )
    for statement in statements:
        with (
            pytest.raises(IntegrityError, match="append-only"),
            test_app.state.engine.begin() as connection,
        ):
            connection.execute(text(statement))


def test_complaint_and_human_approval_audit_catalog_provenance(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/v1/complaints",
        json={
            "title": "합성 가로등 고장",
            "content": "가상 시험동 가로등이 꺼져 있습니다.",
            "location_text": "가상 시험동 1번 위치",
            "channel": "web",
        },
    )
    assert created.status_code == 201
    complaint = created.json()
    candidate = complaint["candidate_departments"][0]
    assert candidate["catalog_version"] == "demo-2026-09-04.v1"
    assert candidate["work_assignment_ids"] == ["WA-RL-01", "WA-RL-02"]

    detail = client.get(f"/api/v1/complaints/{complaint['id']}")
    assert detail.status_code == 200
    triage = next(
        event for event in detail.json()["audit_events"] if event["action"] == "triage_completed"
    )
    assert triage["details"]["catalog_version"] == "demo-2026-09-04.v1"
    assert triage["details"]["top_work_assignment_ids"] == ["WA-RL-01", "WA-RL-02"]

    approved = client.post(
        f"/api/v1/complaints/{complaint['id']}/approve",
        json={
            "department_id": "ROAD_LIGHTING",
            "answer_draft": complaint["answer_draft"],
        },
    )
    assert approved.status_code == 200
    approved_detail = client.get(f"/api/v1/complaints/{complaint['id']}")
    assert approved_detail.status_code == 200
    approval_event = approved_detail.json()["audit_events"][-1]
    assert approval_event["action"] == "human_review_approved"
    assert approval_event["details"]["catalog_version"] == "demo-2026-09-04.v1"
    assert approval_event["details"]["approved_work_assignment_ids"] == [
        "WA-RL-01",
        "WA-RL-02",
    ]
