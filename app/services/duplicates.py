from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Complaint, ComplaintLocationReview, DuplicateCandidate
from app.schemas import DuplicateCandidateRead, DuplicateDecision, DuplicateScoreBreakdown
from app.services.audit import record_audit

LOCATION_NORMALIZATION_VERSION = "local-location-v1"
DUPLICATE_SCORING_VERSION = "local-duplicates-v1"
DUPLICATE_WINDOW = timedelta(days=30)
DUPLICATE_THRESHOLD = 0.70
MAX_DUPLICATE_CANDIDATES = 5


@dataclass(frozen=True)
class DuplicateScore:
    total: float
    category: float
    location: float
    time: float
    text: float
    evidence: list[str]
    eligible: bool


def normalize_location(value: str | None) -> str | None:
    """Normalize a redacted free-text location without any external lookup."""
    if not value or not value.strip():
        return None

    normalized = unicodedata.normalize("NFKC", value).casefold()
    characters = [character if character.isalnum() else " " for character in normalized]
    return " ".join("".join(characters).split()) or None


def _compact(value: str | None) -> str:
    return "".join(character for character in (value or "") if character.isalnum())


def _text_ngrams(value: str, width: int = 2) -> set[str]:
    compact = _compact(unicodedata.normalize("NFKC", value).casefold())
    if not compact:
        return set()
    if len(compact) <= width:
        return {compact}
    return {compact[index : index + width] for index in range(len(compact) - width + 1)}


def text_similarity(left: str, right: str) -> float:
    left_grams = _text_ngrams(left)
    right_grams = _text_ngrams(right)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _format_time_delta(delta: timedelta) -> str:
    minutes = max(0, round(delta.total_seconds() / 60))
    if minutes < 60:
        return f"{minutes}분"
    hours = round(minutes / 60)
    if hours < 48:
        return f"{hours}시간"
    return f"{round(hours / 24)}일"


def score_duplicate(current: Complaint, candidate: Complaint) -> DuplicateScore:
    current_location = normalize_location(current.redacted_location_text)
    candidate_location = normalize_location(candidate.redacted_location_text)
    category_score = float(bool(current.category and current.category == candidate.category))
    location_score = float(bool(current_location) and current_location == candidate_location)

    delta = abs(_as_utc(current.created_at) - _as_utc(candidate.created_at))
    within_window = delta <= DUPLICATE_WINDOW
    time_score = max(0.0, 1.0 - (delta / DUPLICATE_WINDOW)) if within_window else 0.0
    current_text = f"{current.redacted_title}\n{current.redacted_content}"
    candidate_text = f"{candidate.redacted_title}\n{candidate.redacted_content}"
    similarity = text_similarity(current_text, candidate_text)

    total = 0.30 * category_score + 0.40 * location_score + 0.15 * time_score + 0.15 * similarity
    evidence = [
        "민원 분야 일치" if category_score else "민원 분야 불일치",
        "정규화 위치 일치" if location_score else "정규화 위치 불일치 또는 누락",
        f"접수 간격 {_format_time_delta(delta)}",
        f"비식별 문구 유사도 {similarity:.0%}",
    ]
    eligible = bool(
        category_score and location_score and within_window and total >= DUPLICATE_THRESHOLD
    )
    return DuplicateScore(
        total=round(total, 4),
        category=category_score,
        location=location_score,
        time=round(time_score, 4),
        text=round(similarity, 4),
        evidence=evidence,
        eligible=eligible,
    )


def sync_location_review(db: Session, complaint: Complaint) -> ComplaintLocationReview:
    normalized = normalize_location(complaint.redacted_location_text)
    location_review = db.get(ComplaintLocationReview, complaint.id)
    changed = location_review is None or location_review.normalized_location_text != normalized

    if location_review is None:
        location_review = ComplaintLocationReview(
            complaint_id=complaint.id,
            normalized_location_text=normalized,
            normalization_version=LOCATION_NORMALIZATION_VERSION,
            status="unconfirmed" if normalized else "missing",
        )
        db.add(location_review)
    else:
        location_review.normalization_version = LOCATION_NORMALIZATION_VERSION
        if changed:
            location_review.normalized_location_text = normalized
            location_review.status = "unconfirmed" if normalized else "missing"
            location_review.confirmed_by = None
            location_review.confirmed_at = None

    record_audit(
        db,
        complaint_id=complaint.id,
        action="location_normalized",
        actor_type="system",
        details={
            "normalization_version": LOCATION_NORMALIZATION_VERSION,
            "has_location": normalized is not None,
            "changed": changed,
            "status": location_review.status,
        },
    )
    return location_review


def confirm_location(
    db: Session,
    complaint: Complaint,
    *,
    actor_id: str,
) -> ComplaintLocationReview:
    location_review = db.get(ComplaintLocationReview, complaint.id)
    if location_review is None:
        location_review = sync_location_review(db, complaint)
    if not location_review.normalized_location_text:
        raise ValueError("확인할 위치가 없습니다.")

    previous_status = location_review.status
    location_review.status = "confirmed"
    location_review.confirmed_by = actor_id
    location_review.confirmed_at = datetime.now(UTC)
    record_audit(
        db,
        complaint_id=complaint.id,
        action="location_confirmed",
        actor_type="officer",
        actor_id=actor_id,
        details={
            "previous_status": previous_status,
            "normalization_version": location_review.normalization_version,
        },
    )
    return location_review


def refresh_duplicate_candidates(db: Session, complaint: Complaint) -> list[DuplicateCandidate]:
    db.execute(
        delete(DuplicateCandidate).where(
            DuplicateCandidate.complaint_id == complaint.id,
            DuplicateCandidate.status == "suggested",
        )
    )
    reviewed_candidate_ids = set(
        db.scalars(
            select(DuplicateCandidate.candidate_complaint_id).where(
                DuplicateCandidate.complaint_id == complaint.id,
                DuplicateCandidate.status.in_(("confirmed", "rejected")),
            )
        ).all()
    )

    current_created_at = _as_utc(complaint.created_at)
    lower_bound = current_created_at - DUPLICATE_WINDOW
    upper_bound = current_created_at + DUPLICATE_WINDOW
    possible_matches = list(
        db.scalars(
            select(Complaint).where(
                Complaint.id != complaint.id,
                Complaint.created_at >= lower_bound,
                Complaint.created_at <= upper_bound,
            )
        ).all()
    )

    scored_matches: list[tuple[Complaint, DuplicateScore]] = []
    for possible_match in possible_matches:
        if possible_match.id in reviewed_candidate_ids:
            continue
        score = score_duplicate(complaint, possible_match)
        if score.eligible:
            scored_matches.append((possible_match, score))
    scored_matches.sort(key=lambda item: item[1].total, reverse=True)

    records: list[DuplicateCandidate] = []
    for possible_match, score in scored_matches[:MAX_DUPLICATE_CANDIDATES]:
        record = DuplicateCandidate(
            complaint_id=complaint.id,
            candidate_complaint_id=possible_match.id,
            total_score=score.total,
            category_score=score.category,
            location_score=score.location,
            time_score=score.time,
            text_score=score.text,
            evidence=score.evidence,
            scoring_version=DUPLICATE_SCORING_VERSION,
            status="suggested",
        )
        db.add(record)
        records.append(record)

    record_audit(
        db,
        complaint_id=complaint.id,
        action="duplicate_candidates_scored",
        actor_type="system",
        details={
            "scoring_version": DUPLICATE_SCORING_VERSION,
            "window_days": DUPLICATE_WINDOW.days,
            "threshold": DUPLICATE_THRESHOLD,
            "candidate_count": len(records),
            "candidates": [
                {
                    "complaint_id": record.candidate_complaint_id,
                    "score": record.total_score,
                }
                for record in records
            ],
            "automatic_merge": False,
            "automatic_close": False,
        },
    )
    return records


def list_duplicate_candidates(db: Session, complaint_id: str) -> list[DuplicateCandidateRead]:
    rows = db.execute(
        select(DuplicateCandidate, Complaint)
        .join(Complaint, Complaint.id == DuplicateCandidate.candidate_complaint_id)
        .where(DuplicateCandidate.complaint_id == complaint_id)
        .order_by(DuplicateCandidate.total_score.desc(), DuplicateCandidate.created_at.desc())
    ).all()
    return [
        DuplicateCandidateRead(
            candidate_complaint_id=candidate.id,
            redacted_title=candidate.redacted_title,
            redacted_location_text=candidate.redacted_location_text,
            category=candidate.category,
            created_at=candidate.created_at,
            total_score=record.total_score,
            score_breakdown=DuplicateScoreBreakdown(
                category=record.category_score,
                location=record.location_score,
                time=record.time_score,
                text=record.text_score,
            ),
            evidence=record.evidence,
            review_status=record.status,
            reviewed_by=record.reviewed_by,
            reviewed_at=record.reviewed_at,
        )
        for record, candidate in rows
    ]


def decide_duplicate_candidate(
    db: Session,
    complaint: Complaint,
    *,
    candidate_complaint_id: str,
    decision: DuplicateDecision,
    actor_id: str,
) -> DuplicateCandidate:
    record = db.scalar(
        select(DuplicateCandidate).where(
            DuplicateCandidate.complaint_id == complaint.id,
            DuplicateCandidate.candidate_complaint_id == candidate_complaint_id,
        )
    )
    if record is None:
        raise ValueError("검토할 중복 후보가 없습니다.")

    previous_status = record.status
    record.status = decision.value
    record.reviewed_by = actor_id
    record.reviewed_at = datetime.now(UTC)
    record_audit(
        db,
        complaint_id=complaint.id,
        action=f"duplicate_candidate_{decision.value}",
        actor_type="officer",
        actor_id=actor_id,
        details={
            "candidate_complaint_id": candidate_complaint_id,
            "previous_status": previous_status,
            "decision": decision.value,
            "score": record.total_score,
            "scoring_version": record.scoring_version,
            "complaint_status_changed": False,
            "automatic_merge": False,
            "automatic_close": False,
        },
    )
    return record
