"""/api/certificates endpoints."""
from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings
from app.api.serializers import certificate_out
from app.config import Settings
from app.models import Certificate, CertificateStatus
from app.schemas import CertificateOut

router = APIRouter(prefix="/api/certificates", tags=["certificates"])


def _get_or_404(db: Session, certificate_id: str) -> Certificate:
    cert = db.get(Certificate, certificate_id)
    if cert is None:
        raise HTTPException(status_code=404, detail="Certificate not found")
    return cert


@router.get("/{certificate_id}", response_model=CertificateOut, summary="Certificate metadata")
def get_certificate(certificate_id: str, db: Annotated[Session, Depends(get_db)]):
    return certificate_out(_get_or_404(db, certificate_id))


@router.get("/{certificate_id}/download", summary="Download one certificate (PDF)")
def download_certificate(
    certificate_id: str,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    cert = _get_or_404(db, certificate_id)
    if cert.status != CertificateStatus.GENERATED:
        detail = f"Certificate is {cert.status.value}"
        if cert.error_message:
            detail += f": {cert.error_message}"
        raise HTTPException(status_code=409, detail=detail)

    path = settings.storage_dir / cert.file_path
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Certificate file is missing from storage")

    safe = re.sub(r"[^\w\-]+", "_", cert.recipient_name or "recipient").strip("_") or "recipient"
    return FileResponse(path, media_type="application/pdf", filename=f"certificate_{safe}.pdf")
