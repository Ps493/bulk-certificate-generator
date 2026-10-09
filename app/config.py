"""Application settings, read from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Any SQLAlchemy URL works (e.g. postgresql+psycopg://user:pw@host/db).
    database_url: str = "sqlite:///./data/certificates.db"
    # Where generated PDFs are written.
    storage_dir: Path = Path("./data/certificates")
    # Upper bound on recipients accepted in one request.
    max_recipients_per_job: int = 1000
    # Used on the certificate when the request does not provide an issuer.
    default_issuer: str = "Certificate Authority"
    # Re-queue jobs that were interrupted by a restart.
    recover_on_startup: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        return cls(
            database_url=os.getenv("DATABASE_URL", defaults.database_url),
            storage_dir=Path(os.getenv("STORAGE_DIR", str(defaults.storage_dir))),
            max_recipients_per_job=int(
                os.getenv("MAX_RECIPIENTS_PER_JOB", defaults.max_recipients_per_job)
            ),
            default_issuer=os.getenv("DEFAULT_ISSUER", defaults.default_issuer),
            recover_on_startup=_env_bool("RECOVER_ON_STARTUP", defaults.recover_on_startup),
        )
