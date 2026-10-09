from app.models import JobStatus
from tests.conftest import make_recipients


def test_create_job_returns_202_and_location(post_job):
    resp = post_job(make_recipients(3))
    assert resp.status_code == 202
    body = resp.json()
    assert resp.headers["location"] == f"/api/jobs/{body['id']}"
    assert body["progress"]["total"] == 3
    assert body["event_name"] == "Python Workshop 2026"
    assert body["issuer"] == "Aereo Academy"
    assert set(body["links"]) == {"self", "certificates", "download_zip", "retry"}


def test_job_is_processed_in_background_and_completes(client, post_job):
    job_id = post_job(make_recipients(5)).json()["id"]
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == JobStatus.COMPLETED
    assert job["progress"] == {"total": 5, "generated": 5, "failed": 0, "pending": 0, "percent": 100.0}
    assert job["started_at"] and job["completed_at"]
    assert job["failures"] == []


def test_defaults_issuer_and_date(client, post_job):
    payload = {"event_name": "Hackathon", "recipients": make_recipients(1)}
    body = client.post("/api/jobs", json=payload).json()
    assert body["issuer"] == "Certificate Authority"
    assert body["issued_on"]  # defaults to today


def test_jobs_are_listed_newest_first(client, post_job):
    first = post_job(make_recipients(1)).json()["id"]
    second = post_job(make_recipients(1, prefix="Other")).json()["id"]
    listing = client.get("/api/jobs").json()
    assert listing["total"] == 2
    assert {j["id"] for j in listing["items"]} == {first, second}


def test_unknown_job_is_404(client):
    assert client.get("/api/jobs/does-not-exist").status_code == 404
    assert client.get("/api/jobs/does-not-exist/certificates").status_code == 404
    assert client.get("/api/jobs/does-not-exist/download").status_code == 404
