"""Local worker: python -m app.worker --once (or --watch until Ctrl+C)."""

import argparse
import ctypes
import json
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.database import Base, install_append_only_guards, make_engine, make_session_factory
from app.models import AIProcessingJob, Complaint, utc_now
from app.services import ai_queue
from app.services.classifier import ClassifierError
from app.services.department_catalog import ensure_current_catalog
from app.services.pipeline import ComplaintPipeline
from app.services.runtime import build_pipeline


@dataclass(frozen=True)
class WorkerResult:
    job_id: int
    state: str


def _precondition_error(
    db: Session, pipeline: ComplaintPipeline, job: AIProcessingJob, complaint: Complaint
) -> str | None:
    if complaint.reviewed_at is not None:
        return "human_review_superseded"
    if (
        pipeline.settings.ai_provider != job.provider
        or pipeline.settings.openai_model != job.model
        or getattr(pipeline.classifier, "provider_name", None) != job.provider
    ):
        return "configuration_changed"
    if (
        pipeline.catalog.catalog_version != job.catalog_version
        or pipeline.catalog.source_sha256 != job.source_sha256
    ):
        return "catalog_changed"
    try:
        ensure_current_catalog(db, pipeline.catalog)
    except ValueError:
        return "catalog_changed"
    if ai_queue.input_fingerprint(complaint) != job.input_sha256:
        return "input_changed"
    if pipeline.deferred_safety_reasons(complaint):
        return "safety_review_required"
    return None


def _fail_attempt(
    factory: sessionmaker[Session],
    claim: ai_queue.Claim,
    *,
    now: datetime,
    error_code: str,
    retryable: bool,
) -> WorkerResult:
    with factory() as db:
        changed = ai_queue.fail(db, claim, now=now, error_code=error_code, retryable=retryable)
        db.commit()
        job = db.get(AIProcessingJob, claim.job_id)
        assert job is not None
        return WorkerResult(job.id, job.state if changed else "discarded")


def run_once(
    factory: sessionmaker[Session],
    pipeline: ComplaintPipeline,
    *,
    clock: Callable[[], datetime] = utc_now,
) -> WorkerResult | None:
    if not pipeline.deferred:
        return None
    ai_queue.validate_local_queue(pipeline.settings)
    with factory() as db:
        claim = ai_queue.claim_next(
            db, now=clock(), lease_seconds=pipeline.settings.ai_queue_lease_seconds
        )
        db.commit()
    if claim is None:
        return None

    try:
        with factory() as db:
            job = db.get(AIProcessingJob, claim.job_id)
            complaint = db.get(Complaint, claim.complaint_id)
            assert job is not None and complaint is not None
            if not ai_queue.owns_claim(job, claim, clock()):
                return WorkerResult(claim.job_id, "discarded")
            error = _precondition_error(db, pipeline, job, complaint)
        if error:
            return _fail_attempt(factory, claim, now=clock(), error_code=error, retryable=False)

        # Providers see redacted fields only; no transaction spans their execution.
        prepared = pipeline.prepare_deferred(complaint)

        with factory() as db:
            complaint = db.get(Complaint, claim.complaint_id)
            assert complaint is not None
            ai_queue.lock_complaint(db, complaint)
            job = db.get(AIProcessingJob, claim.job_id, populate_existing=True)
            assert job is not None
            if not ai_queue.owns_claim(job, claim, clock()):
                return WorkerResult(claim.job_id, "discarded")
            error = _precondition_error(db, pipeline, job, complaint)
            if error:
                ai_queue.fail(db, claim, now=clock(), error_code=error, retryable=False)
                db.commit()
                return WorkerResult(claim.job_id, "failed")
            # Result, audit trail, and terminal queue state commit together or all roll back.
            if not ai_queue.complete(db, claim, now=clock()):
                return WorkerResult(claim.job_id, "discarded")
            pipeline.apply_deferred(db, complaint, prepared)
            db.commit()
            return WorkerResult(claim.job_id, "completed")
    except ClassifierError:
        return _fail_attempt(
            factory, claim, now=clock(), error_code="provider_error", retryable=True
        )
    except Exception:
        # Exception strings, SDK responses, prompts and drafts must never enter logs or audits.
        return _fail_attempt(
            factory, claim, now=clock(), error_code="processing_error", retryable=True
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="seongnam-local-ai-worker")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--once", action="store_true", help="Process at most one ready attempt (default)"
    )
    mode.add_argument("--watch", action="store_true", help="Poll locally until Ctrl+C")
    parser.add_argument(
        "--poll-seconds", type=int, choices=range(1, 61), default=2, metavar="1..60"
    )
    args = parser.parse_args(argv)
    if os.name == "nt":
        ctypes.windll.kernel32.SetConsoleTitleW("Seongnam civic AI - local queue worker")
    try:
        settings = Settings()
        ai_queue.validate_local_queue(settings)
        if not settings.ai_deferred_enabled or settings.ai_provider == "rules":
            parser.error("Set AI_DEFERRED_ENABLED=true and AI_PROVIDER=openai for this worker")
        pipeline = build_pipeline(settings)
    except ValueError:
        print(json.dumps({"error": "invalid_local_worker_configuration"}))
        return 2
    engine = make_engine(settings.database_url)
    try:
        Base.metadata.create_all(engine)
        install_append_only_guards(engine)
        factory = make_session_factory(engine)
        while True:
            result = run_once(factory, pipeline)
            if result is not None or not args.watch:
                print(json.dumps(asdict(result) if result else {"state": "idle"}), flush=True)
            if not args.watch:
                return 1 if result and result.state == "failed" else 0
            if result is None:
                time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 0
    except Exception:
        print(json.dumps({"error": "local_worker_error"}))
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
