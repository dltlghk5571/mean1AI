"""End-to-end proof that the dataset export actually joins what the live app
produces: a real app.chat_eval log line plus a real citizen_chat_draft
AuditEvent, not just synthetic fixtures."""

import logging
import re
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import AuditEvent
from app.services.chat_agent import ChatAgent, _ChatExtraction
from evals.chat_dataset import AuditDraftRow, build_dataset


def test_export_joins_live_chat_eval_log_with_live_audit_event(
    anonymous_client: TestClient,
    test_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    parsed = _ChatExtraction(
        assistant_message="확인했습니다.",
        title="가로등이 꺼져 있어요",
        content="어제 저녁 공원 산책로 가로등이 꺼져 있었습니다.",
        location_text="데모공원 산책로",
        ready_to_submit=True,
    )
    sdk = Mock()
    sdk.responses.parse.return_value = Mock(output_parsed=parsed)
    monkeypatch.setattr("openai.OpenAI", Mock(return_value=sdk))
    test_app.state.chat_agent = ChatAgent(api_key="synthetic-unused-key", model="synthetic-model")

    page = anonymous_client.get("/minwon/new")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    request_key = re.search(r'name="request_key" value="([^"]+)"', page.text)
    assert csrf and request_key
    anonymous_client.headers["X-Citizen-CSRF"] = csrf[1]

    with caplog.at_level(logging.INFO, logger="app.chat_eval"):
        chat_response = anonymous_client.post(
            "/minwon/chat/message",
            json={"history": [], "message": "어제 저녁 공원 가로등이 꺼져 있었어요"},
        )
    assert chat_response.status_code == 200, chat_response.text
    draft_id = chat_response.json()["draft_id"]
    assert draft_id
    [log_record] = [r for r in caplog.records if r.name == "app.chat_eval"]
    log_line = f"2026-09-07 10:00:00,000 INFO app.chat_eval {log_record.message}"

    submit_response = anonymous_client.post(
        "/minwon/submit",
        json={
            "title": parsed.title,
            "content": parsed.content,
            "location_text": parsed.location_text,
            "request_key": request_key[1],
            "consent": "yes",
            "chat_draft_id": draft_id,
            "chat_edited": "",
        },
    )
    assert submit_response.status_code == 200, submit_response.text
    complaint_id = submit_response.json()["redirect"].split("/")[2]

    with test_app.state.session_factory() as db:
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.complaint_id == complaint_id,
                AuditEvent.action == "citizen_chat_draft",
            )
        )
        assert event is not None
        audit_rows = [
            AuditDraftRow(
                complaint_id=complaint_id,
                draft_id=event.details["draft_id"],
                edited=event.details["edited"],
            )
        ]

    result = build_dataset([log_line], audit_rows, seed=1)
    assert result.rejected == []
    [record] = result.records
    assert record.category == "accepted_unedited"
    assert record.draft_id == draft_id
    assert record.complaint_id == complaint_id
    assert record.target.title == parsed.title
    assert record.target.content == parsed.content
