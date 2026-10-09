"""Application factory.

Run with:  uvicorn app.main:create_app --factory
"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import certificates, jobs
from app.config import Settings
from app.db import Base, make_engine, make_session_factory
from app.services.processor import recover_jobs

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)  # swap for Alembic migrations in a real deployment
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    session_factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.recover_on_startup:
            # Resume interrupted jobs without delaying start-up.
            threading.Thread(
                target=recover_jobs, args=(session_factory, settings.storage_dir), name="job-recovery", daemon=True
            ).start()
        yield
        engine.dispose()

    app = FastAPI(
        title="Bulk Certificate Generator API",
        version="1.0.0",
        description="Submit a list of recipients, get PDF certificates generated in the background.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory

    app.include_router(jobs.router)
    app.include_router(certificates.router)

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok"}

    return app
