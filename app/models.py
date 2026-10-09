"""Database models.

Two tables:

* ``jobs``          - one row per bulk-generation request.
* ``certificates``  - one row per recipient in the request (valid or not), so
                      every input row has a visible outcome.

Progress counters are *not* stored on the job; they are derived from the
certificate rows (single source of truth, no counter drift).
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Date, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.db import Base


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"  # accepted, not started
    PROCESSING = "PROCESSING"  # worker is generating
    COMPLETED = "COMPLETED"  # every certificate generated
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"  # some generated, some failed
    FAILED = "FAILED"  # nothing could be generated


TERMINAL_JOB_STATUSES = {JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_ERRORS, JobStatus.FAILED}


class CertificateStatus(str, enum.Enum):
    PENDING = "PENDING"
    GENERATED = "GENERATED"
    FAILED = "FAILED"


class ErrorType(str, enum.Enum):
    VALIDATION = "VALIDATION"  # bad input; fix the data and submit again
    GENERATION = "GENERATION"  # valid input, rendering/IO failed; retryable


class UTCDateTime(TypeDecorator):
    """Store naive UTC, always hand back timezone-aware UTC datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed; use timezone-aware UTC")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None else None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_name: Mapped[str] = mapped_column(String(200))
    issuer: Mapped[str] = mapped_column(String(100))
    issued_on: Mapped[date] = mapped_column(Date)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=30), default=JobStatus.PENDING, index=True
    )
    total: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )


class Certificate(Base):
    __tablename__ = "certificates"
    __table_args__ = (
        UniqueConstraint("job_id", "idx", name="uq_certificate_job_idx"),
        Index("ix_certificate_job_status", "job_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    # 0-based position of the recipient in the submitted list.
    idx: Mapped[int] = mapped_column(Integer)

    recipient_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    achievement: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Original payload, kept only for rows that failed validation.
    raw_input: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON, nullable=True)

    status: Mapped[CertificateStatus] = mapped_column(
        Enum(CertificateStatus, native_enum=False, length=20), default=CertificateStatus.PENDING
    )
    error_type: Mapped[ErrorType | None] = mapped_column(
        Enum(ErrorType, native_enum=False, length=20), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Path relative to STORAGE_DIR.
    file_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    job: Mapped[Job] = relationship(back_populates="certificates")
