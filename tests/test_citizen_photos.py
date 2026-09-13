import base64
import io
import json
import re
import struct
import zlib
from concurrent.futures import ThreadPoolExecutor
from typing import Literal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
from sqlalchemy import func, select

from app.api.citizen_photos import photo_slots
from app.models import AuditEvent, CitizenChat, CitizenPhoto, CitizenSession, Complaint
from app.services import citizen_chat
from app.services.citizen_photos import PhotoError, PhotoInput, prepare_photo


def photo_bytes(*, color="green", format="PNG", size=(20, 12), orientation=None) -> bytes:
    buffer = io.BytesIO()
    with Image.new("RGB", size, color) as image:
        if format == "PNG":
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("Comment", "synthetic-private-metadata")
            image.save(buffer, format=format, pnginfo=metadata)
        else:
            exif = Image.Exif()
            exif[270] = "synthetic-private-metadata"
            if orientation:
                exif[274] = orientation
            image.save(buffer, format=format, exif=exif)
    return buffer.getvalue()


def photo_payload(raw: bytes | None = None, *, media_type="image/png") -> dict:
    return {"media_type": media_type, "data": base64.b64encode(raw or photo_bytes()).decode()}


def review(client: TestClient) -> dict:
    page = client.get("/minwon/new")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert csrf
    client.headers["X-Citizen-CSRF"] = csrf[1]
    state = client.post("/minwon/chat/open", json={}).json()
    for action, message in (
        ("say", "가상 시연 공원의 가로등이 꺼졌어요."),
        ("complaint", ""),
        ("say", "가상 시연 공원 정문"),
    ):
        response = client.post(
            "/minwon/chat/turn",
            json={
                "revision": str(state["revision"]),
                "request_id": str(uuid4()),
                "action": action,
                "message": message,
            },
        )
        assert response.status_code == 200
        state = response.json()
    assert state["stage"] == "review"
    return state


def confirmation(state: dict) -> dict:
    return {
        "turn": {
            "revision": str(state["revision"]),
            "request_id": str(uuid4()),
            "action": "confirm",
            "consent": "yes",
        },
        "photos": [photo_payload()],
    }


def test_png_metadata_is_removed_and_photo_is_reencoded() -> None:
    prepared = prepare_photo(PhotoInput.model_validate(photo_payload()))
    assert b"synthetic-private-metadata" not in prepared.content
    with Image.open(io.BytesIO(prepared.content)) as stored:
        assert stored.format == "JPEG" and stored.size == (20, 12)
        assert not stored.getexif() and "icc_profile" not in stored.info


def test_orientation_is_applied_before_resize_and_metadata_removal() -> None:
    raw = photo_bytes(format="JPEG", size=(1800, 900), orientation=6)
    prepared = prepare_photo(PhotoInput.model_validate(photo_payload(raw, media_type="image/jpeg")))
    assert (prepared.width, prepared.height) == (800, 1600)
    with Image.open(io.BytesIO(prepared.content)) as stored:
        assert not stored.getexif()


@pytest.mark.parametrize("problem", ["type", "corrupt", "svg", "large", "pixels", "animated"])
def test_invalid_images_are_rejected_without_echoing_content(problem: str) -> None:
    raw = photo_bytes()
    media_type: Literal["image/png", "image/jpeg"] = "image/png"
    if problem == "type":
        media_type = "image/jpeg"
    elif problem == "corrupt":
        raw = raw[:40]
    elif problem == "svg":
        raw = b'<svg xmlns="http://www.w3.org/2000/svg">synthetic-private-metadata</svg>'
    elif problem == "large":
        raw += b"x" * 5_000_000
    elif problem == "pixels":
        header = raw[:16] + struct.pack(">II", 8000, 8000) + raw[24:29]
        raw = header + struct.pack(">I", zlib.crc32(header[12:])) + raw[33:]
    elif problem == "animated":
        with (
            Image.new("RGB", (8, 8), "green") as first,
            Image.new("RGB", (8, 8), "white") as second,
        ):
            buffer = io.BytesIO()
            first.save(buffer, format="PNG", save_all=True, append_images=[second], duration=100)
            raw = buffer.getvalue()
    # Bypass the envelope length check to exercise the decoder's independent byte limit.
    value = PhotoInput.model_construct(media_type=media_type, data=base64.b64encode(raw).decode())
    with pytest.raises(PhotoError) as error:
        prepare_photo(value)
    assert "synthetic-private-metadata" not in str(error.value)


def test_photo_intake_is_private_and_retries_are_bound_to_photo_content(
    anonymous_client: TestClient, client: TestClient, test_app: FastAPI
) -> None:
    state = review(anonymous_client)
    body = confirmation(state)
    result = anonymous_client.post("/minwon/chat/confirm-with-photos", json=body)
    assert result.status_code == 200
    assert (
        anonymous_client.post("/minwon/chat/confirm-with-photos", json=body).json() == result.json()
    )
    changed = {**body, "photos": [photo_payload(photo_bytes(color="white"))]}
    assert (
        anonymous_client.post("/minwon/chat/confirm-with-photos", json=changed).status_code == 409
    )
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(Complaint.id))) == 1
        assert db.scalar(select(func.count(CitizenPhoto.id))) == 1
        photo = db.scalar(select(CitizenPhoto))
        assert photo
        url = f"/minwon/{photo.complaint_id}/photos/{photo.id}"
        officer_url = f"/api/v1/complaints/{photo.complaint_id}/photos/{photo.id}"
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "citizen_chat_confirmed"))
        assert event and event.details["photo_ids"] == [photo.id]
        chat = db.scalar(select(CitizenChat))
        assert chat and "photos" not in chat.state
        assert "synthetic-private-metadata" not in json.dumps(event.details)
    image = anonymous_client.get(url)
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert image.headers["cache-control"] == "no-store"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert url in anonymous_client.get(f"/minwon/{photo.complaint_id}").text
    assert "사진 1장도 함께 접수" in anonymous_client.get(result.json()["redirect"]).text
    assert anonymous_client.get(officer_url).status_code == 401
    assert client.get(officer_url).status_code == 200
    assert officer_url in client.get(f"/complaints/{photo.complaint_id}").text
    assert anonymous_client.get(f"/minwon/{uuid4()}/photos/{photo.id}").status_code == 404
    with TestClient(test_app) as other:
        other_page = other.get("/minwon/new")
        assert other.get(url).status_code == 404
        other_csrf = re.search(r'name="csrf_token" value="([^"]+)"', other_page.text)
        receipt = anonymous_client.get(result.json()["redirect"])
        receipt_number = re.search(r"data-receipt-number>([^<]+)", receipt.text)
        lookup_code = re.search(r"data-lookup-code>([^<]+)", receipt.text)
        assert other_csrf and receipt_number and lookup_code
        granted = other.post(
            "/minwon/lookup",
            headers={"X-Citizen-CSRF": other_csrf[1]},
            json={"receipt_number": receipt_number[1], "lookup_code": lookup_code[1]},
        )
        assert granted.status_code == 200 and other.get(url).status_code == 200
    with test_app.state.session_factory() as db:
        session = db.scalar(
            select(CitizenSession).where(CitizenSession.token_hash == chat.owner_session_hash)
        )
        assert session
        session.expires_at = 0
        db.commit()
    assert anonymous_client.get(url).status_code == 404


@pytest.mark.parametrize(
    "problem", ["consent", "stage", "revision", "too_many", "base64", "fields"]
)
def test_rejected_confirmation_keeps_draft_and_saves_nothing(
    problem: str, anonymous_client: TestClient, test_app: FastAPI
) -> None:
    state = review(anonymous_client)
    body = confirmation(state)
    if problem == "consent":
        body["turn"]["consent"] = ""
    elif problem == "stage":
        body["turn"]["action"] = "information"
    elif problem == "revision":
        body["turn"]["revision"] = "0"
    elif problem == "too_many":
        body["photos"] *= 4
    elif problem == "base64":
        body["photos"][0]["data"] = "invalid synthetic-private-metadata"
    elif problem == "fields":
        body["photos"][0]["filename"] = "synthetic-private-metadata.png"
    result = anonymous_client.post("/minwon/chat/confirm-with-photos", json=body)
    assert result.status_code in {409, 422}
    assert "synthetic-private-metadata" not in result.text
    assert anonymous_client.post("/minwon/chat/open", json={}).json() == state
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(Complaint.id))) == 0
        assert db.scalar(select(func.count(CitizenPhoto.id))) == 0


def test_photo_failure_rolls_back_intake_and_allows_same_request_retry(
    anonymous_client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = review(anonymous_client)
    body = confirmation(state)
    attach = citizen_chat.attach_photos

    def fail_after_attachment(db, complaint_id, photos):
        attach(db, complaint_id, photos)
        raise RuntimeError("synthetic-private-metadata")

    monkeypatch.setattr(citizen_chat, "attach_photos", fail_after_attachment)
    result = anonymous_client.post("/minwon/chat/confirm-with-photos", json=body)
    assert result.status_code == 503 and "synthetic-private-metadata" not in result.text
    assert anonymous_client.post("/minwon/chat/open", json={}).json() == state
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(Complaint.id))) == 0
        assert db.scalar(select(func.count(CitizenPhoto.id))) == 0
    monkeypatch.setattr(citizen_chat, "attach_photos", attach)
    assert anonymous_client.post("/minwon/chat/confirm-with-photos", json=body).status_code == 200


def test_csrf_body_limit_and_capacity_release(anonymous_client: TestClient) -> None:
    assert anonymous_client.post("/minwon/chat/confirm-with-photos", json={}).status_code == 403
    state = review(anonymous_client)
    response = anonymous_client.post(
        "/minwon/chat/confirm-with-photos",
        content=(b"x" * 1_000_000 for _ in range(21)),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert photo_slots.acquire(blocking=False) and photo_slots.acquire(blocking=False)
    try:
        busy = anonymous_client.post("/minwon/chat/confirm-with-photos", json=confirmation(state))
        assert busy.status_code == 429 and busy.headers["retry-after"] == "5"
    finally:
        photo_slots.release()
        photo_slots.release()
    assert (
        anonymous_client.post(
            "/minwon/chat/confirm-with-photos", json=confirmation(state)
        ).status_code
        == 200
    )


def test_concurrent_photo_confirmation_creates_one_intake(
    anonymous_client: TestClient, test_app: FastAPI
) -> None:
    body = confirmation(review(anonymous_client))

    def submit():
        with TestClient(test_app) as another:
            another.cookies.update(anonymous_client.cookies)
            return another.post(
                "/minwon/chat/confirm-with-photos",
                json=body,
                headers={"X-Citizen-CSRF": anonymous_client.headers["X-Citizen-CSRF"]},
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: submit(), range(2)))
    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json() == responses[1].json()
    with test_app.state.session_factory() as db:
        assert db.scalar(select(func.count(Complaint.id))) == 1
        assert db.scalar(select(func.count(CitizenPhoto.id))) == 1
