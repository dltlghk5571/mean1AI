"""Bounded photo confirmation, metadata stripping, and private photo references."""

import base64
import hashlib
import io
from dataclasses import dataclass, field
from typing import Literal, Self

from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chat_schemas import ChatTurn
from app.models import CitizenPhoto

MAX_PHOTOS = 3
MAX_FILE_BYTES = 5_000_000
MAX_BODY_BYTES = 20_050_000
MAX_PIXELS = 16_000_000
MAX_SAVED_BYTES = 2_000_000


class PhotoInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    media_type: Literal["image/jpeg", "image/png"]
    data: str = Field(min_length=1, max_length=6_666_668, repr=False)


class PhotoConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    turn: ChatTurn
    photos: list[PhotoInput] = Field(min_length=1, max_length=MAX_PHOTOS, repr=False)

    @model_validator(mode="after")
    def require_confirmation(self) -> Self:
        if self.turn.action != "confirm" or self.turn.consent != "yes":
            raise ValueError("photos_require_citizen_confirmation")
        return self


class PhotoError(ValueError):
    pass


@dataclass(frozen=True)
class PreparedPhoto:
    content: bytes = field(repr=False)
    source_hash: str = field(repr=False)
    width: int
    height: int


def prepare_photo(value: PhotoInput) -> PreparedPhoto:
    try:
        raw = base64.b64decode(value.data, validate=True)
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("photo_too_large")
        with Image.open(io.BytesIO(raw), formats=["JPEG", "PNG"]) as original:
            if (
                original.width * original.height > MAX_PIXELS
                or Image.MIME[original.format or ""] != value.media_type
                or getattr(original, "n_frames", 1) != 1
            ):
                raise ValueError("unsupported_photo")
            original.verify()
        with Image.open(io.BytesIO(raw), formats=["JPEG", "PNG"]) as original:
            original.load()
            oriented = ImageOps.exif_transpose(original)
            oriented.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
            # A fresh canvas carries pixels only, including for PNGs with text/EXIF/ICC chunks.
            with (
                oriented.convert("RGBA") as rgba,
                Image.new("RGB", oriented.size, "white") as clean,
            ):
                clean.paste(rgba, mask=rgba.getchannel("A"))
                output = io.BytesIO()
                clean.save(output, format="JPEG", quality=85)
                content = output.getvalue()
                width, height = clean.size
            oriented.close()
        if len(content) > MAX_SAVED_BYTES:
            raise ValueError("converted_photo_too_large")
        return PreparedPhoto(content, hashlib.sha256(raw).hexdigest(), width, height)
    except Exception:
        # Decoder errors and validation details can contain uploaded data. Never echo them.
        raise PhotoError(
            "JPG·PNG 사진인지 확인해 주세요. 한 장당 5MB·1,600만 화소까지 첨부할 수 있어요."
        ) from None


def prepare_confirmation(body: bytes) -> tuple[ChatTurn, tuple[PreparedPhoto, ...]]:
    envelope = PhotoConfirmation.model_validate_json(body)
    return envelope.turn, tuple(prepare_photo(value) for value in envelope.photos)


def attach_photos(db: Session, complaint_id: str, photos: tuple[PreparedPhoto, ...]) -> list[str]:
    """Use the intake transaction; no temporary files or independent commits."""
    db.flush()
    records = [
        CitizenPhoto(
            complaint_id=complaint_id,
            position=position,
            width=photo.width,
            height=photo.height,
            content_hash=hashlib.sha256(photo.content).hexdigest(),
            content=photo.content,
        )
        for position, photo in enumerate(photos, 1)
    ]
    db.add_all(records)
    db.flush()
    return [record.id for record in records]


def photo_summaries(db: Session, complaint_id: str, *, officer: bool = False) -> list[dict]:
    prefix = "/api/v1/complaints" if officer else "/minwon"
    return [
        {
            "id": photo.id,
            "position": photo.position,
            "width": photo.width,
            "height": photo.height,
            "url": f"{prefix}/{complaint_id}/photos/{photo.id}",
        }
        for photo in db.scalars(
            select(CitizenPhoto)
            .where(CitizenPhoto.complaint_id == complaint_id)
            .order_by(CitizenPhoto.position)
        )
    ]
