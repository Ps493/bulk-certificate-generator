"""Job progress tracking, failure isolation, retry and crash recovery."""
from datetime import date

import pytest

from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.schemas import JobCreate
from app.services import jobs as job_service
from app.services import renderer
from app.services.processor import process_job, recover_jobs
from tests.conftest import make_recipients


def _create_job_without_processing(app, recipients):
    """Create a job via the service layer only, so no worker runs."""
    payload = JobCreate(event_name="Workshop", issued_on=date(2026, 1, 1), recipients=recipients)
    with app.state.session_factory() as db:
        return job_service.create_job(db, payload, app.state.settings).id


# ------------------------------------------------------------------ progress
def test_job_is_pending_with_zero_progress_before_processing(app, client):
    job_id = _create_job_without_processing(app, make_recipients(4))
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == JobStatus.PENDING
    assert job["progress"] == {"total": 4, "generated": 0, "failed": 0, "pending": 4, "percent": 0.0}
    assert job["started_at"] is None


def test_progress_is_visible_while_the_job_is_running(app, client, monkeypatch):
    job_id = _create_job_without_processing(app, make_recipients(4))
    snapshots = []
    real_render = renderer.render_certificate

    def spy(**kwargs):
        # Called by the worker just before each certificate; poll the API like a client would.
        snapshots.append(client.get(f"/api/jobs/{job_id}").json())
        return real_render(**kwargs)

    monkeypatch.setattr(renderer, "render_certificate", spy)
    process_job(app.state.session_factory, app.state.settings.storage_dir, job_id)

    assert [s["status"] for s in snapshots] == [JobStatus.PROCESSING] * 4
    assert [s["progress"]["generated"] for s in snapshots] == [0, 1, 2, 3]
    assert [s["progress"]["percent"] for s in snapshots] == [0.0, 25.0, 50.0, 75.0]
    final = client.get(f"/api/jobs/{job_id}").json()
    assert final["status"] == JobStatus.COMPLETED and final["progress"]["percent"] == 100.0


# ----------------------------------------------------------- failure isolation
@pytest.fixture
def explode_for_boom(monkeypatch):
    real_render = renderer.render_certificate
    state = {"enabled": True}

    def flaky(**kwargs):
        if state["enabled"] and kwargs["recipient_name"] == "Boom":
            raise RuntimeError("disk on fire")
        return real_render(**kwargs)

    monkeypatch.setattr(renderer, "render_certificate", flaky)
    return state


def test_one_generation_failure_does_not_stop_the_others(client, post_job, explode_for_boom):
    recipients = [
        {"name": "Alice", "email": "alice@example.com"},
        {"name": "Boom", "email": "boom@example.com"},
        {"name": "Carol", "email": "carol@example.com"},
    ]
    job_id = post_job(recipients).json()["id"]
    job = client.get(f"/api/jobs/{job_id}").json()

    assert job["status"] == JobStatus.COMPLETED_WITH_ERRORS
    assert job["progress"]["generated"] == 2 and job["progress"]["failed"] == 1
    [failure] = job["failures"]
    assert failure["index"] == 1 and failure["recipient_name"] == "Boom"
    assert failure["error_type"] == "GENERATION"
    assert "RuntimeError: disk on fire" in failure["error"]

    items = client.get(f"/api/jobs/{job_id}/certificates").json()["items"]
    assert [i["status"] for i in items] == ["GENERATED", "FAILED", "GENERATED"]
    assert items[1]["download_url"] is None


def test_real_renderer_failure_is_isolated(client, post_job):
    """No mocking: a name the font can't render fails only that certificate."""
    recipients = [
        {"name": "Alice", "email": "alice@example.com"},
        {"name": "प्रिया", "email": "priya@example.com"},
        {"name": "Carol", "email": "carol@example.com"},
    ]
    job_id = post_job(recipients).json()["id"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == JobStatus.COMPLETED_WITH_ERRORS
    assert job["progress"]["generated"] == 2
    assert "cannot render" in job["failures"][0]["error"]


def test_all_generation_failures_mark_job_failed(client, post_job, monkeypatch):
    def always_fail(**kwargs):
        raise OSError("no space left")

    monkeypatch.setattr(renderer, "render_certificate", always_fail)
    job_id = post_job(make_recipients(2)).json()["id"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == JobStatus.FAILED
    assert job["progress"]["failed"] == 2


def test_retry_regenerates_only_failed_certificates(client, post_job, explode_for_boom):
    recipients = [
        {"name": "Alice", "email": "alice@example.com"},
        {"name": "Boom", "email": "boom@example.com"},
        {"name": "bad row"},  # validation failure: must NOT be retried
    ]
    job_id = post_job(recipients).json()["id"]
    alice_before = client.get(f"/api/jobs/{job_id}/certificates").json()["items"][0]["generated_at"]

    explode_for_boom["enabled"] = False  # the transient problem is gone
    resp = client.post(f"/api/jobs/{job_id}/retry")
    assert resp.status_code == 202

    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["progress"] == {"total": 3, "generated": 2, "failed": 1, "pending": 0, "percent": 100.0}
    assert job["status"] == JobStatus.COMPLETED_WITH_ERRORS
    assert job["failures"][0]["error_type"] == "VALIDATION"
    items = client.get(f"/api/jobs/{job_id}/certificates").json()["items"]
    assert items[1]["status"] == "GENERATED"
    assert items[0]["generated_at"] == alice_before  # untouched

    # Nothing retryable left.
    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 409


def test_retry_rejected_while_job_is_running(app, client):
    job_id = _create_job_without_processing(app, make_recipients(2))
    assert client.post(f"/api/jobs/{job_id}/retry").status_code == 409


# ------------------------------------------------------------ crash recovery
def test_recover_jobs_resumes_interrupted_work(app, client):
    job_id = _create_job_without_processing(app, make_recipients(3))
    with app.state.session_factory() as db:  # simulate a crash half-way through
        job = db.get(Job, job_id)
        job.status = JobStatus.PROCESSING
        first = db.query(Certificate).filter_by(job_id=job_id, idx=0).one()
        first.status = CertificateStatus.GENERATED
        first.file_path = "fake.pdf"
        db.commit()

    assert recover_jobs(app.state.session_factory, app.state.settings.storage_dir) == [job_id]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == JobStatus.COMPLETED
    assert job["progress"]["generated"] == 3

    # Finished jobs are left alone on the next start.
    assert recover_jobs(app.state.session_factory, app.state.settings.storage_dir) == []
