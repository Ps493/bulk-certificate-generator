"""Pydantic schemas: request validation and response shapes."""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import CertificateStatus, ErrorType, JobStatus

NAME_MAX = 80
ACHIEVEMENT_MAX = 60
EVENT_NAME_MAX = 100
ISSUER_MAX = 60


def _clean_text(value: str) -> str:
    """Collapse whitespace and reject control characters (incl. newlines)."""
    value = re.sub(r"[ \t]+", " ", value).strip()
    if any(unicodedata.category(ch).startswith("C") for ch in value):
        raise ValueError("must not contain control characters")
    return value


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
class RecipientIn(BaseModel):
    """One recipient. Validated *individually* so one bad row can't sink the job."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=NAME_MAX)
    email: EmailStr
    achievement: str = Field(default="Participant", min_length=1, max_length=ACHIEVEMENT_MAX)

    @field_validator("name", "achievement", mode="before")
    @classmethod
    def _strip_before_length_checks(cls, v: Any) -> Any:
        if isinstance(v, str):
            return _clean_text(v)
        return v  # let pydantic report the type error

    @field_validator("email", mode="after")
    @classmethod
    def _normalise_email(cls, v: str) -> str:
        return v.lower()


class JobCreate(BaseModel):
    """Job-level data. Recipients stay as raw items here on purpose: they are
    validated one by one by ``app.validation`` so we can report per-row errors."""

    model_config = ConfigDict(str_strip_whitespace=True)

    event_name: str = Field(min_length=1, max_length=EVENT_NAME_MAX)
    issued_on: date | None = None
    issuer: str | None = Field(default=None, min_length=1, max_length=ISSUER_MAX)
    recipients: list[Any] = Field(min_length=1)


# --------------------------------------------------------------------------- #
# Responses
# --------------------------------------------------------------------------- #
class Progress(BaseModel):
    total: int
    generated: int
    failed: int
    pending: int
    percent: float = Field(description="Share of recipients that reached a final state (generated or failed).")


class JobOut(BaseModel):
    id: str
    status: JobStatus
    event_name: str
    issuer: str
    issued_on: date
    progress: Progress
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    links: dict[str, str]


class FailureOut(BaseModel):
    index: int
    recipient_name: str | None
    recipient_email: str | None
    error_type: ErrorType
    error: str


class JobDetailOut(JobOut):
    failures: list[FailureOut] = Field(description="First failures, for quick triage.")
    failures_truncated: bool


class JobListOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[JobOut]


class CertificateOut(BaseModel):
    id: str
    job_id: str
    index: int
    recipient_name: str | None
    recipient_email: str | None
    achievement: str | None
    status: CertificateStatus
    error_type: ErrorType | None
    error: str | None
    input: Any | None = Field(default=None, description="Original payload; only present for validation failures.")
    generated_at: datetime | None
    download_url: str | None


class CertificateListOut(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CertificateOut]
