import re
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.chat_agent import ChatAgent, _ChatExtraction


def _mock_openai(monkeypatch: pytest.MonkeyPatch, parsed: _ChatExtraction) -> Mock:
    sdk = Mock()
    sdk.responses.parse.return_value = Mock(output_parsed=parsed)
    monkeypatch.setattr("openai.OpenAI", Mock(return_value=sdk))
    return sdk


def _enable_chat(
    test_app: FastAPI, monkeypatch: pytest.MonkeyPatch, parsed: _ChatExtraction
) -> Mock:
    sdk = _mock_openai(monkeypatch, parsed)
    test_app.state.chat_agent = ChatAgent(api_key="synthetic-unused-key", model="synthetic-model")
    return sdk


_NOT_READY = _ChatExtraction(
    assistant_message="안녕하세요",
    title="",
    content="",
    location_text="",
    ready_to_submit=False,
)


def _start(client: TestClient) -> str:
    page = client.get("/minwon/new")
    assert page.status_code == 200
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert csrf
    client.headers["X-Citizen-CSRF"] = csrf[1]
    return page.text


def test_chat_widget_hidden_and_endpoint_disabled_without_api_key(
    anonymous_client: TestClient, test_app: FastAPI
) -> None:
    assert test_app.state.chat_agent is None
    page = _start(anonymous_client)
    assert "data-chat-widget" not in page
    response = anonymous_client.post(
        "/minwon/chat/message", json={"history": [], "message": "안녕하세요"}
    )
    assert response.status_code == 503


def test_chat_widget_visible_and_turn_returns_draft_when_ready(
    anonymous_client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = _ChatExtraction(
        assistant_message="말씀 감사합니다. 접수 전 내용을 확인해 주세요.",
        title="가로등이 꺼져 있어요",
        content="어제 저녁 공원 산책로 가로등이 꺼져 있었습니다.",
        location_text="데모공원 산책로",
        ready_to_submit=True,
    )
    sdk = _enable_chat(test_app, monkeypatch, parsed)
    page = _start(anonymous_client)
    assert "data-chat-widget" in page

    response = anonymous_client.post(
        "/minwon/chat/message",
        json={"history": [], "message": "어제 저녁 공원 가로등이 꺼져 있었어요"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is True
    assert body["draft"] == {
        "title": "가로등이 꺼져 있어요",
        "content": "어제 저녁 공원 산책로 가로등이 꺼져 있었습니다.",
        "location_text": "데모공원 산책로",
    }
    sdk.responses.parse.assert_called_once()


def test_chat_requires_csrf(
    anonymous_client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_chat(test_app, monkeypatch, _NOT_READY)
    # No prior GET /minwon/new, so no citizen session cookie/CSRF header is set.
    response = anonymous_client.post(
        "/minwon/chat/message", json={"history": [], "message": "안녕하세요"}
    )
    assert response.status_code == 403


def test_chat_rejects_oversized_history(
    anonymous_client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_chat(test_app, monkeypatch, _NOT_READY)
    _start(anonymous_client)
    history = [{"role": "user", "content": "메시지"} for _ in range(17)]
    response = anonymous_client.post(
        "/minwon/chat/message", json={"history": history, "message": "메시지"}
    )
    assert response.status_code == 400


def test_chat_rejects_oversized_body(
    anonymous_client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_chat(test_app, monkeypatch, _NOT_READY)
    _start(anonymous_client)
    response = anonymous_client.post(
        "/minwon/chat/message", json={"history": [], "message": "가" * 41_000}
    )
    assert response.status_code == 413


def test_chat_safety_gate_overrides_ready_for_sensitive_content(
    anonymous_client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = _ChatExtraction(
        assistant_message="정리했습니다.",
        title="힘든 상황",
        content="자살 충동을 느낀다는 이웃이 있어요",
        location_text="",
        ready_to_submit=True,
    )
    _enable_chat(test_app, monkeypatch, parsed)
    _start(anonymous_client)
    response = anonymous_client.post(
        "/minwon/chat/message", json={"history": [], "message": "이웃이 걱정돼요"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert body["draft"] is None
