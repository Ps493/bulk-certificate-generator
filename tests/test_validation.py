import pytest

from app.models import JobStatus
from app.validation import validate_recipients
from tests.conftest import make_recipients


# ---- request-level validation (whole request is rejected) -------------------
@pytest.mark.parametrize(
    "payload",
    [
        {"recipients": [{"name": "A", "email": "a@example.com"}]},  # no event_name
        {"event_name": "   ", "recipients": [{"name": "A", "email": "a@example.com"}]},  # blank event
        {"event_name": "E", "recipients": []},  # empty list
        {"event_name": "E"},  # no recipients
        {"event_name": "E", "recipients": "nope"},  # wrong type
        {"event_name": "E" * 101, "recipients": [{"name": "A", "email": "a@example.com"}]},
        {"event_name": "E", "issued_on": "not-a-date", "recipients": [{"name": "A", "email": "a@example.com"}]},
    ],
)
def test_invalid_request_is_rejected_with_422(client, payload):
    assert client.post("/api/jobs", json=payload).status_code == 422


def test_too_many_recipients_is_413(post_job):
    resp = post_job(make_recipients(51))  # limit is 50 in test settings
    assert resp.status_code == 413
    assert "max 50" in resp.json()["detail"]


# ---- recipient-level validation (only the bad row fails) --------------------
def test_invalid_recipients_fail_individually_and_valid_ones_still_generate(client, post_job):
    recipients = [
        {"name": "Good One", "email": "good1@example.com"},  # 0 ok
        {"name": "No Email"},  # 1 missing email
        {"name": "Bad Email", "email": "not-an-email"},  # 2 invalid email
        {"email": "noname@example.com"},  # 3 missing name
        "just a string",  # 4 not an object
        {"name": "Good Two", "email": "good2@example.com", "achievement": "Winner"},  # 5 ok
        {"name": "Dup", "email": "GOOD1@example.com"},  # 6 duplicate of 0 (case-insensitive)
    ]
    body = post_job(recipients).json()
    job = client.get(f"/api/jobs/{body['id']}").json()

    assert job["status"] == JobStatus.COMPLETED_WITH_ERRORS
    assert job["progress"] == {"total": 7, "generated": 2, "failed": 5, "pending": 0, "percent": 100.0}

    failures = {f["index"]: f for f in job["failures"]}
    assert set(failures) == {1, 2, 3, 4, 6}
    assert all(f["error_type"] == "VALIDATION" for f in failures.values())
    assert "email" in failures[1]["error"]
    assert "email" in failures[2]["error"]
    assert "name" in failures[3]["error"]
    assert "JSON object" in failures[4]["error"]
    assert "duplicate of recipient at index 0" in failures[6]["error"]

    items = client.get(f"/api/jobs/{body['id']}/certificates").json()["items"]
    assert [i["status"] for i in items] == ["GENERATED", "FAILED", "FAILED", "FAILED", "FAILED", "GENERATED", "FAILED"]
    assert items[4]["input"] == "just a string"  # raw input is echoed back for triage
    assert items[5]["achievement"] == "Winner"


def test_all_invalid_job_fails_immediately_without_background_work(client, post_job):
    body = post_job([{"name": "x"}, {"email": "y@example.com"}]).json()
    assert body["status"] == JobStatus.FAILED
    assert body["completed_at"] is not None
    assert body["progress"]["failed"] == 2
    assert client.get(f"/api/jobs/{body['id']}/download").status_code == 409


def test_name_is_trimmed_and_email_lowercased(client, post_job):
    job_id = post_job([{"name": "  Asha   Rao  ", "email": "Asha@Example.COM"}]).json()["id"]
    item = client.get(f"/api/jobs/{job_id}/certificates").json()["items"][0]
    assert item["recipient_name"] == "Asha Rao"
    assert item["recipient_email"] == "asha@example.com"


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "Line\nBreak", "email": "a@example.com"},
        {"name": "x" * 81, "email": "a@example.com"},
        {"name": "   ", "email": "a@example.com"},
        {"name": 123, "email": "a@example.com"},
        {"name": "A", "email": "a@example.com", "achievement": "y" * 61},
    ],
)
def test_recipient_field_rules(bad):
    [result] = validate_recipients([bad])
    assert not result.is_valid and result.error


def test_unknown_recipient_fields_are_ignored():
    [result] = validate_recipients([{"name": "A", "email": "a@example.com", "shoe_size": 9}])
    assert result.is_valid
    assert result.recipient.achievement == "Participant"
