"""/api/jobs endpoints."""
from __future__ import annotations

import os
import re
import tempfile
import zipfile
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.api.deps import get_db, get_settings
from app.api.serializers import build_progress, certificate_out, failure_out, job_links
from app.config import Settings
from app.models import Certificate, CertificateStatus, ErrorType, Job, JobStatus
from app.schemas import CertificateListOut, JobCreate, JobDetailOut, JobListOut, JobOut
from app.services import jobs as job_service
from app.services.processor import process_job

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

MAX_FAILURES_IN_JOB_DETAIL = 50


def _get_job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _job_out(db: Session, job: Job) -> JobOut:
    return JobOut(
        id=job.id,
        status=job.status,
        event_name=job.event_name,
        issuer=job.issuer,
        issued_on=job.issued_on,
        progress=build_progress(job.total, job_service.status_counts(db, job.id)),
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        links=job_links(job),
    )


def _enqueue(background_tasks: BackgroundTasks, request: Request, job_id: str) -> None:
    background_tasks.add_task(
        process_job, request.app.state.session_factory, request.app.state.settings.storage_dir, job_id
    )


@router.post("", status_code=202, response_model=JobOut, summary="Create a bulk certificate generation job")
def create_job(
    payload: JobCreate,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    """Accepts the whole recipient list in one request and returns immediately
    (202). Certificates are generated in the background; poll `GET /api/jobs/{id}`."""
    if len(payload.recipients) > settings.max_recipients_per_job:
        raise HTTPException(
            status_code=413,
            detail=f"Too many recipients: {len(payload.recipients)} (max {settings.max_recipients_per_job} per job). Split the request.",
        )
    job = job_service.create_job(db, payload, settings)
    if job.status == JobStatus.PENDING:  # something valid to generate
        _enqueue(background_tasks, request, job.id)
    response.headers["Location"] = f"/api/jobs/{job.id}"
    return _job_out(db, job)


@router.get("", response_model=JobListOut, summary="List jobs (newest first)")
def list_jobs(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    total = db.scalar(select(func.count()).select_from(Job)) or 0
    jobs = db.scalars(select(Job).order_by(Job.created_at.desc(), Job.id).limit(limit).offset(offset)).all()
    return JobListOut(total=total, limit=limit, offset=offset, items=[_job_out(db, j) for j in jobs])


@router.get("/{job_id}", response_model=JobDetailOut, summary="Job status, progress and failures")
def get_job(job_id: str, db: Annotated[Session, Depends(get_db)]):
    job = _get_job_or_404(db, job_id)
    failed = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.FAILED)
        .order_by(Certificate.idx)
        .limit(MAX_FAILURES_IN_JOB_DETAIL + 1)
    ).all()
    base = _job_out(db, job)
    return JobDetailOut(
        **base.model_dump(),
        failures=[failure_out(c) for c in failed[:MAX_FAILURES_IN_JOB_DETAIL]],
        failures_truncated=len(failed) > MAX_FAILURES_IN_JOB_DETAIL,
    )


@router.get("/{job_id}/certificates", response_model=CertificateListOut, summary="Per-recipient results")
def list_certificates(
    job_id: str,
    db: Annotated[Session, Depends(get_db)],
    status: CertificateStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    _get_job_or_404(db, job_id)
    where = [Certificate.job_id == job_id]
    if status is not None:
        where.append(Certificate.status == status)
    total = db.scalar(select(func.count()).select_from(Certificate).where(*where)) or 0
    rows = db.scalars(select(Certificate).where(*where).order_by(Certificate.idx).limit(limit).offset(offset)).all()
    return CertificateListOut(total=total, limit=limit, offset=offset, items=[certificate_out(c) for c in rows])


@router.post("/{job_id}/retry", status_code=202, response_model=JobOut, summary="Retry failed generations")
def retry_job(
    job_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Annotated[Session, Depends(get_db)],
):
    """Re-runs certificates that failed during *generation*. Validation failures
    are not retried (the same input would fail again); submit a corrected job."""
    job = _get_job_or_404(db, job_id)
    if not job_service.is_terminal(job):
        raise HTTPException(status_code=409, detail=f"Job is {job.status.value}; wait until it finishes.")
    if job_service.requeue_failed_generations(db, job) == 0:
        raise HTTPException(status_code=409, detail="No retryable (GENERATION) failures in this job.")
    _enqueue(background_tasks, request, job.id)
    return _job_out(db, job)


def _safe_filename_part(text: str | None) -> str:
    return re.sub(r"[^\w\-]+", "_", text or "recipient").strip("_")[:60] or "recipient"


@router.get("/{job_id}/download", summary="Download all generated certificates as a ZIP")
def download_job_zip(
    job_id: str,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    job = _get_job_or_404(db, job_id)
    certs = db.scalars(
        select(Certificate)
        .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.GENERATED)
        .order_by(Certificate.idx)
    ).all()
    if not certs:
        raise HTTPException(status_code=409, detail="No certificates have been generated for this job yet.")

    # Build on disk, not in memory: a job can hold hundreds of PDFs.
    fd, tmp_path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_STORED) as zf:  # PDFs are already compressed
            for c in certs:
                src = settings.storage_dir / c.file_path
                if src.is_file():
                    zf.write(src, arcname=f"{c.idx + 1:04d}_{_safe_filename_part(c.recipient_name)}.pdf")
    except Exception:
        os.unlink(tmp_path)
        raise
    return FileResponse(
        tmp_path,
        media_type="application/zip",
        filename=f"certificates_{job.id}.zip",
        background=BackgroundTask(os.unlink, tmp_path),
    )
