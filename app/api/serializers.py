"""Model -> response-schema conversion."""
from __future__ import annotations

from app.models import Certificate, CertificateStatus, Job
from app.schemas import CertificateOut, FailureOut, Progress


def build_progress(total: int, counts: dict[CertificateStatus, int]) -> Progress:
    generated = counts[CertificateStatus.GENERATED]
    failed = counts[CertificateStatus.FAILED]
    pending = counts[CertificateStatus.PENDING]
    done = generated + failed
    percent = round(done / total * 100, 1) if total else 100.0
    return Progress(total=total, generated=generated, failed=failed, pending=pending, percent=percent)


def job_links(job: Job) -> dict[str, str]:
    base = f"/api/jobs/{job.id}"
    return {
        "self": base,
        "certificates": f"{base}/certificates",
        "download_zip": f"{base}/download",
        "retry": f"{base}/retry",
    }


def certificate_download_url(cert: Certificate) -> str | None:
    if cert.status == CertificateStatus.GENERATED:
        return f"/api/certificates/{cert.id}/download"
    return None


def certificate_out(cert: Certificate) -> CertificateOut:
    return CertificateOut(
        id=cert.id,
        job_id=cert.job_id,
        index=cert.idx,
        recipient_name=cert.recipient_name,
        recipient_email=cert.recipient_email,
        achievement=cert.achievement,
        status=cert.status,
        error_type=cert.error_type,
        error=cert.error_message,
        input=cert.raw_input,
        generated_at=cert.generated_at,
        download_url=certificate_download_url(cert),
    )


def failure_out(cert: Certificate) -> FailureOut:
    return FailureOut(
        index=cert.idx,
        recipient_name=cert.recipient_name,
        recipient_email=cert.recipient_email,
        error_type=cert.error_type,
        error=cert.error_message or "",
    )
