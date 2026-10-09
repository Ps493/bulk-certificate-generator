"""Background worker: turns PENDING certificate rows into PDF files.

Design notes
------------
* Each certificate is rendered and committed on its own. Progress is therefore
  visible to pollers as it happens, and one failing certificate is recorded
  and skipped instead of aborting the job.
* The worker only talks to the DB through a session factory, so it can run in
  a FastAPI background task, a plain thread, or (later) a Celery/RQ worker
  without changes.
* If the process dies mid-job, the job stays PROCESSING with some rows still
  PENDING. ``recover_jobs`` picks those up on the next start-up.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Certificate, CertificateStatus, ErrorType, Job, JobStatus, utcnow
from app.services import jobs as job_service
from app.services import renderer

log = logging.getLogger(__name__)

MAX_ERROR_LEN = 500


def certificate_relpath(job_id: str, certificate_id: str) -> str:
    return f"{job_id}/{certificate_id}.pdf"


def _write_atomic(path: Path, data: bytes) -> None:
    """Write via temp file + rename so readers never see a half-written PDF."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def process_job(session_factory: sessionmaker[Session], storage_dir: Path, job_id: str) -> None:
    """Generate every PENDING certificate of a job, then finalise the job."""
    with session_factory() as db:
        job = db.get(Job, job_id)
        if job is None:
            log.warning("process_job: job %s not found", job_id)
            return
        job.status = JobStatus.PROCESSING
        job.started_at = job.started_at or utcnow()
        db.commit()

        pending_ids = db.scalars(
            select(Certificate.id)
            .where(Certificate.job_id == job_id, Certificate.status == CertificateStatus.PENDING)
            .order_by(Certificate.idx)
        ).all()

        for cert_id in pending_ids:
            cert = db.get(Certificate, cert_id)
            try:
                relpath = certificate_relpath(job_id, cert_id)
                pdf = renderer.render_certificate(
                    recipient_name=cert.recipient_name,
                    achievement=cert.achievement,
                    event_name=job.event_name,
                    issuer=job.issuer,
                    issued_on=job.issued_on,
                    certificate_id=cert_id,
                )
                _write_atomic(storage_dir / relpath, pdf)
            except Exception as exc:  # noqa: BLE001 - isolate *any* per-certificate failure
                log.exception("certificate %s (job %s, index %s) failed", cert_id, job_id, cert.idx)
                cert.status = CertificateStatus.FAILED
                cert.error_type = ErrorType.GENERATION
                cert.error_message = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LEN]
            else:
                cert.status = CertificateStatus.GENERATED
                cert.file_path = relpath
                cert.generated_at = utcnow()
                cert.error_type = None
                cert.error_message = None
            db.commit()

        job_service.finalize_job(db, job)
        db.commit()
        log.info("job %s finished with status %s", job_id, job.status.value)


def recover_jobs(session_factory: sessionmaker[Session], storage_dir: Path) -> list[str]:
    """Resume jobs left PENDING/PROCESSING by a previous run. Returns their ids."""
    with session_factory() as db:
        job_ids = db.scalars(
            select(Job.id).where(Job.status.in_([JobStatus.PENDING, JobStatus.PROCESSING])).order_by(Job.created_at)
        ).all()
    for job_id in job_ids:
        try:
            process_job(session_factory, storage_dir, job_id)
        except Exception:  # noqa: BLE001
            log.exception("recovery of job %s failed", job_id)
    return list(job_ids)
