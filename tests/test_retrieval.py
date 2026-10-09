import io
import zipfile

from tests.conftest import make_recipients


def test_download_single_certificate_pdf(client, post_job):
    job_id = post_job([{"name": "Priya Sharma", "email": "priya@example.com"}]).json()["id"]
    item = client.get(f"/api/jobs/{job_id}/certificates").json()["items"][0]
    assert item["download_url"] == f"/api/certificates/{item['id']}/download"

    resp = client.get(item["download_url"])
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert "certificate_Priya_Sharma.pdf" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF")


def test_certificate_metadata_endpoint(client, post_job):
    job_id = post_job(make_recipients(1)).json()["id"]
    cert_id = client.get(f"/api/jobs/{job_id}/certificates").json()["items"][0]["id"]
    meta = client.get(f"/api/certificates/{cert_id}").json()
    assert meta["status"] == "GENERATED" and meta["job_id"] == job_id and meta["index"] == 0


def test_unknown_certificate_is_404(client):
    assert client.get("/api/certificates/nope").status_code == 404
    assert client.get("/api/certificates/nope/download").status_code == 404


def test_failed_certificate_cannot_be_downloaded(client, post_job):
    job_id = post_job([{"name": "No email"}]).json()["id"]
    item = client.get(f"/api/jobs/{job_id}/certificates").json()["items"][0]
    assert item["download_url"] is None
    resp = client.get(f"/api/certificates/{item['id']}/download")
    assert resp.status_code == 409
    assert "FAILED" in resp.json()["detail"]


def test_missing_file_on_disk_is_404(client, settings, post_job):
    job_id = post_job(make_recipients(1)).json()["id"]
    item = client.get(f"/api/jobs/{job_id}/certificates").json()["items"][0]
    (settings.storage_dir / job_id / f"{item['id']}.pdf").unlink()
    assert client.get(item["download_url"]).status_code == 404


def test_zip_contains_all_generated_certificates_only(client, post_job):
    recipients = make_recipients(3) + [{"name": "Broken"}]
    job_id = post_job(recipients).json()["id"]
    resp = client.get(f"/api/jobs/{job_id}/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert zf.namelist() == ["0001_User_0.pdf", "0002_User_1.pdf", "0003_User_2.pdf"]
        assert all(zf.read(n).startswith(b"%PDF") for n in zf.namelist())


def test_list_certificates_supports_filter_and_pagination(client, post_job):
    recipients = make_recipients(5) + [{"name": "bad"}]
    job_id = post_job(recipients).json()["id"]

    page = client.get(f"/api/jobs/{job_id}/certificates", params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 6 and [i["index"] for i in page["items"]] == [2, 3]

    failed = client.get(f"/api/jobs/{job_id}/certificates", params={"status": "FAILED"}).json()
    assert failed["total"] == 1 and failed["items"][0]["index"] == 5

    assert client.get(f"/api/jobs/{job_id}/certificates", params={"status": "BOGUS"}).status_code == 422
    assert client.get(f"/api/jobs/{job_id}/certificates", params={"limit": 0}).status_code == 422
