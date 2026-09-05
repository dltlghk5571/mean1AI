import copy
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.database import Base, install_append_only_guards, make_engine, make_session_factory
from app.models import (
    AuditEvent,
    CatalogImportEvent,
    Complaint,
    Department,
    DepartmentCatalogEntry,
    DepartmentCatalogVersion,
    ReviewDecision,
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
    assert bound.requires_human_review is True
    assert set(bound.review_reasons) == {
        "candidate_catalog_version_mismatch",
        "invalid_work_assignment_reference",
    }


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
    data["supersedes"] = "demo-2026-09-04.v1"
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


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        (("catalog_version",), "invalid version"),
        (("source_label",), "   "),
        (("approval_status",), "draft"),
        (("synthetic",), 1),
        (("effective_from",), (date.today() + timedelta(days=1)).isoformat()),
        (("effective_until",), "2020-01-01"),
        (("effective_until",), (date.today() - timedelta(days=1)).isoformat()),
        (("effective_from",), 0),
        (("supersedes",), "demo-2026-09-04.v1"),
        (("fallback_department_id",), "UNKNOWN"),
        (("departments", 0, "id"), "invalid-id"),
        (("departments", 1, "id"), "ROAD_LIGHTING"),
        (("departments", 0, "name"), "   "),
        (("departments", 0, "active"), "false"),
        (("departments", 0, "active"), False),
        (("departments", 1, "work_assignments", 0, "id"), "WA-RL-01"),
        (("departments", 0, "work_assignments", 1, "id"), "WA-RL-01"),
        (("departments", 0, "work_assignments", 0, "id"), "bad assignment"),
        (("departments", 1, "routing_rules", 0, "id"), "RULE-STREETLIGHT"),
        (("departments", 0, "routing_rules", 0, "keywords"), ["   "]),
        (("departments", 0, "routing_rules", 0, "keywords"), ["가로등", "가로등"]),
        (("departments", 0, "routing_rules", 0, "requires_location"), 0),
        (("departments", 0, "routing_rules", 0, "work_assignment_ids"), ["WA-RM-01"]),
    ],
)
def test_catalog_rejects_invalid_ids_metadata_and_entries(
    tmp_path: Path, field_path: tuple[str | int, ...], value: Any
) -> None:
    data = _source_data()
    target: Any = data
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = value
    with pytest.raises(ValueError):
        DepartmentCatalog.from_json(_write_catalog(tmp_path, data))


def test_catalog_requires_versioned_envelope(tmp_path: Path) -> None:
    data = _source_data()
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(data["departments"]), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid department catalog"):
        DepartmentCatalog.from_json(path)
    del data["catalog_version"]
    with pytest.raises(ValueError, match="catalog_version"):
        DepartmentCatalog.from_json(_write_catalog(tmp_path, data))


def _successor_data(version: str = "demo-m2.v2") -> dict[str, Any]:
    data = _source_data()
    data.update(catalog_version=version, supersedes=data["catalog_version"])
    return data


def test_superseded_replay_cannot_reactivate_removed_entries(
    client: TestClient, test_app: FastAPI, tmp_path: Path
) -> None:
    del client
    original = test_app.state.pipeline.catalog
    data = _successor_data()
    data["departments"] = [row for row in data["departments"] if row["id"] != "SAFETY_DUTY"]
    successor = DepartmentCatalog.from_json(_write_catalog(tmp_path, data))
    with test_app.state.session_factory() as db:
        assert import_department_catalog(db, successor)
        with pytest.raises(ValueError, match="superseded"):
            import_department_catalog(db, original)
        assert db.get(Department, "SAFETY_DUTY").active is False
        assert import_department_catalog(db, successor) is False
        assert db.scalar(select(func.count()).select_from(CatalogImportEvent)) == 2


@pytest.mark.parametrize("predecessor", [None, "unknown-version"])
def test_successor_requires_current_predecessor(
    client: TestClient, test_app: FastAPI, tmp_path: Path, predecessor: str | None
) -> None:
    del client
    data = _successor_data()
    data["supersedes"] = predecessor
    successor = DepartmentCatalog.from_json(_write_catalog(tmp_path, data))
    with test_app.state.session_factory() as db:
        with pytest.raises(ValueError, match="explicitly supersede"):
            import_department_catalog(db, successor)
        assert db.scalar(select(func.count()).select_from(DepartmentCatalogVersion)) == 1
        assert db.scalar(select(func.count()).select_from(CatalogImportEvent)) == 1


def test_backdated_successor_is_rejected(
    client: TestClient, test_app: FastAPI, tmp_path: Path
) -> None:
    del client
    data = _successor_data()
    data["effective_from"] = "2024-12-31"
    successor = DepartmentCatalog.from_json(_write_catalog(tmp_path, data))
    with test_app.state.session_factory() as db:
        with pytest.raises(ValueError, match="cannot move backwards"):
            import_department_catalog(db, successor)
        assert db.scalar(select(func.count()).select_from(DepartmentCatalogVersion)) == 1


@pytest.mark.parametrize("reuse", ["category", "assignment_owner", "rule_owner"])
def test_stable_ids_cannot_be_repurposed(
    client: TestClient, test_app: FastAPI, tmp_path: Path, reuse: str
) -> None:
    del client
    data = _successor_data()
    lighting, roads = data["departments"][:2]
    if reuse == "category":
        lighting["category"] = "welfare"
    elif reuse == "assignment_owner":
        moved = lighting["work_assignments"].pop()
        lighting["routing_rules"][0]["work_assignment_ids"].remove(moved["id"])
        roads["work_assignments"].append(moved)
    else:
        moved = lighting["routing_rules"].pop()
        moved["work_assignment_ids"] = [roads["work_assignments"][0]["id"]]
        roads["routing_rules"].append(moved)
    successor = DepartmentCatalog.from_json(_write_catalog(tmp_path, data))
    with test_app.state.session_factory() as db:
        with pytest.raises(ValueError, match="stable .* cannot change"):
            import_department_catalog(db, successor)
        assert db.get(Department, "ROAD_LIGHTING").category == "streetlight"
        assert db.scalar(select(func.count()).select_from(DepartmentCatalogEntry)) == 9


@pytest.mark.parametrize("retired", ["department", "assignment", "rule"])
def test_retired_ids_cannot_be_reintroduced(
    client: TestClient, test_app: FastAPI, tmp_path: Path, retired: str
) -> None:
    del client
    data = _successor_data()
    if retired == "department":
        data["departments"][7]["active"] = False
    elif retired == "assignment":
        data["departments"][0]["work_assignments"].pop()
        data["departments"][0]["routing_rules"][0]["work_assignment_ids"].pop()
    else:
        data["departments"][0]["routing_rules"].clear()
    successor = DepartmentCatalog.from_json(_write_catalog(tmp_path, data))
    restored_data = _successor_data("demo-m2.v3")
    restored_data["supersedes"] = successor.catalog_version
    restored = DepartmentCatalog.from_json(_write_catalog(tmp_path, restored_data, "restored.json"))
    with test_app.state.session_factory() as db:
        import_department_catalog(db, successor)
        with pytest.raises(ValueError, match="retired .* cannot be reactivated"):
            import_department_catalog(db, restored)
        assert db.scalar(select(func.count()).select_from(DepartmentCatalogVersion)) == 2


def test_partial_import_failure_rolls_back_projection_snapshots_and_audits(
    client: TestClient, test_app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.department_catalog as importer

    created = client.post(
        "/api/v1/complaints",
        json={
            "title": "합성 가로등 고장",
            "content": "합성 가로등 불이 꺼져 깜빡입니다.",
            "location_text": "합성 시험동",
            "channel": "web",
        },
    ).json()
    assert created["status"] == "assigned"
    data = _successor_data()
    data["departments"][0]["name"] = "합성 변경 이름"
    successor = DepartmentCatalog.from_json(_write_catalog(tmp_path, data))
    sync = importer._sync_current_projection

    def fail_after_projection(db: Any, catalog: DepartmentCatalog) -> None:
        sync(db, catalog)
        db.flush()
        raise RuntimeError("synthetic import failure")

    monkeypatch.setattr(importer, "_sync_current_projection", fail_after_projection)
    with test_app.state.session_factory() as db:
        with pytest.raises(RuntimeError, match="synthetic import failure"):
            import_department_catalog(db, successor)
        assert db.scalar(select(func.count()).select_from(DepartmentCatalogVersion)) == 1
        assert db.scalar(select(func.count()).select_from(DepartmentCatalogEntry)) == 9
        assert db.scalar(select(func.count()).select_from(CatalogImportEvent)) == 1
        assert db.get(Department, "ROAD_LIGHTING").name == _source_data()["departments"][0]["name"]
        complaint = db.get(Complaint, created["id"])
        assert complaint.status == "assigned"
        assert complaint.assigned_department_id == "ROAD_LIGHTING"
        assert all(event.action != "catalog_route_invalidated" for event in complaint.audit_events)


def test_additive_legacy_migration_preserves_foreign_keys_and_reviews(tmp_path: Path) -> None:
    engine = make_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    factory = make_session_factory(engine)
    try:
        Base.metadata.create_all(
            engine,
            tables=[
                Base.metadata.tables[name]
                for name in ("departments", "complaints", "audit_events", "review_decisions")
            ],
        )
        with factory() as db:
            db.add(
                Department(
                    id="LEGACY_GROUP",
                    name="합성 이전 그룹",
                    category="other",
                    description="합성 이전 설명",
                    jurisdiction="합성 위치",
                )
            )
            db.flush()
            legacy = Complaint(
                title="합성 이전 접수",
                content="합성 이전 본문",
                status="assigned",
                assigned_department_id="LEGACY_GROUP",
                requires_human_review=False,
                candidate_departments=[{"department_id": "LEGACY_GROUP"}],
            )
            reviewed = Complaint(
                title="합성 검토 완료",
                content="합성 본문",
                status="reviewed",
                assigned_department_id="LEGACY_GROUP",
                requires_human_review=False,
            )
            db.add_all([legacy, reviewed])
            db.flush()
            db.add(
                ReviewDecision(
                    complaint_id=reviewed.id,
                    actor_id="review.demo",
                    actor_role="reviewer",
                    department_id="LEGACY_GROUP",
                    answer_draft="합성 검토 초안",
                    draft_modified=False,
                    grounding_status="legacy",
                )
            )
            db.commit()
            legacy_id, reviewed_id = legacy.id, reviewed.id

        Base.metadata.create_all(engine)
        install_append_only_guards(engine)
        with factory() as db:
            catalog = DepartmentCatalog.from_json(Settings().departments_path)
            assert import_department_catalog(db, catalog)
            legacy_department = db.get(Department, "LEGACY_GROUP")
            assert legacy_department is not None and legacy_department.active is False
            migrated = db.get(Complaint, legacy_id)
            assert migrated is not None
            assert migrated.status == "needs_review"
            assert migrated.assigned_department_id is None
            assert migrated.requires_human_review is True
            assert migrated.candidate_departments == [{"department_id": "LEGACY_GROUP"}]
            assert migrated.audit_events[-1].action == "catalog_route_invalidated"
            preserved = db.get(Complaint, reviewed_id)
            assert preserved is not None
            assert preserved.status == "reviewed"
            assert preserved.assigned_department_id == "LEGACY_GROUP"
            assert db.scalar(select(func.count()).select_from(ReviewDecision)) == 1
            assert import_department_catalog(db, catalog) is False
            assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1
    finally:
        engine.dispose()
