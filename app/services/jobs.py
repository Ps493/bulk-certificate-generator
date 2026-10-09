"""Job-level business logic shared by the API and the background worker."""
from __future__ import annotations

from datetime import date

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    TERMINAL_JOB_STATUSES,
    Certificate,
    CertificateStatus,
    ErrorType,
    Job,
    JobStatus,
    utcnow,
)
from app.schemas import JobCreate
from app.validation import validate_recipients


def create_job(db: Session, payload: JobCreate, settings: Settings) -> Job:
    """Validate every recipient and persist the job plus one row per recipient.

    Invalid recipients are stored as FAILED(VALIDATION) immediately; valid ones
    as PENDING for the worker. If nothing is left to do the job is finalised
    right away.
    """
    job = Job(
        event_name=payload.event_name,
        issuer=payload.issuer or settings.default_issuer,
        issued_on=payload.issued_on or date.today(),
        total=len(payload.recipients),
        status=JobStatus.PENDING,
    )
    db.add(job)
    db.flush()  # assigns job.id

    rows = []
    for item in validate_recipients(payload.recipients):
        if item.is_valid:
            r = item.recipient
            rows.append(
                Certificate(
                    job_id=job.id,
                    idx=item.index,
                    recipient_name=r.name,
                    recipient_email=str(r.email),
                    achievement=r.achievement,
                )
            )
        else:
            rows.append(
                Certificate(
                    job_id=job.id,
                    idx=item.index,
                    raw_input=item.raw,
                    status=CertificateStatus.FAILED,
                    error_type=ErrorType.VALIDATION,
                    error_message=item.error,
                )
            )
    db.add_all(rows)
    db.flush()

    if not any(row.status == CertificateStatus.PENDING for row in rows):
        finalize_job(db, job)
    db.commit()
    return job


def status_counts(db: Session, job_id: str) -> dict[CertificateStatus, int]:
    rows = db.execute(
        select(Certificate.status, func.count()).where(Certificate.job_id == job_id).group_by(Certificate.status)
    ).all()
    counts = {s: 0 for s in CertificateStatus}
    counts.update({status: n for status, n in rows})
    return counts


def finalize_job(db: Session, job: Job) -> None:
    """Set the terminal status from the certificate outcomes. Caller commits."""
    counts = status_counts(db, job.id)
    generated, failed = counts[CertificateStatus.GENERATED], counts[CertificateStatus.FAILED]
    if failed == 0:
        job.status = JobStatus.COMPLETED
    elif generated == 0:
        job.status = JobStatus.FAILED
    else:
        job.status = JobStatus.COMPLETED_WITH_ERRORS
    job.completed_at = utcnow()


def requeue_failed_generations(db: Session, job: Job) -> int:
    """Reset retryable failures (GENERATION errors) to PENDING. Returns how many.

    VALIDATION failures are not retried: the same input would fail again.
    """
    result = db.execute(
        update(Certificate)
        .where(
            Certificate.job_id == job.id,
            Certificate.status == CertificateStatus.FAILED,
            Certificate.error_type == ErrorType.GENERATION,
        )
        .values(status=CertificateStatus.PENDING, error_type=None, error_message=None)
    )
    if result.rowcount:
        job.status = JobStatus.PENDING
        job.completed_at = None
    db.commit()
    return result.rowcount


def is_terminal(job: Job) -> bool:
    return job.status in TERMINAL_JOB_STATUSES
