import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        storage_dir=tmp_path / "certs",
        max_recipients_per_job=50,
        recover_on_startup=False,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    # Starlette's TestClient runs background tasks before returning the response,
    # so a job is already finished when `client.post(...)` returns.
    with TestClient(app) as c:
        yield c


def make_recipients(n, prefix="User"):
    return [{"name": f"{prefix} {i}", "email": f"{prefix.lower()}{i}@example.com"} for i in range(n)]


def make_payload(recipients, **overrides):
    payload = {"event_name": "Python Workshop 2026", "issuer": "Aereo Academy", "recipients": recipients}
    payload.update(overrides)
    return payload


@pytest.fixture
def post_job(client):
    def _post(recipients, **overrides):
        return client.post("/api/jobs", json=make_payload(recipients, **overrides))

    return _post
