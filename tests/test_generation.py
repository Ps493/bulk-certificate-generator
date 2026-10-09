import io
from datetime import date

import pytest

from app.services.renderer import CertificateRenderError, render_certificate
from tests.conftest import make_recipients


def _render(**overrides):
    kwargs = dict(
        recipient_name="Priya Sharma",
        achievement="Participant",
        event_name="Python Workshop 2026",
        issuer="Aereo Academy",
        issued_on=date(2026, 10, 9),
        certificate_id="cert-123",
    )
    kwargs.update(overrides)
    return render_certificate(**kwargs)


def test_renderer_produces_a_one_page_pdf_with_recipient_data():
    pypdf = pytest.importorskip("pypdf")
    pdf = _render()
    assert pdf.startswith(b"%PDF")
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    for expected in ("Priya Sharma", "Participant", "Python Workshop 2026", "Aereo Academy", "9 October 2026", "cert-123"):
        assert expected in text


def test_long_name_is_shrunk_to_fit():
    long_name = "Maximiliano Alejandro Bartholomew Wolfeschlegelsteinhausen-Fitzgerald III"
    assert len(long_name) <= 80
    assert _render(recipient_name=long_name).startswith(b"%PDF")


def test_unsupported_characters_raise_clear_error():
    with pytest.raises(CertificateRenderError, match="cannot render"):
        _render(recipient_name="प्रिया")  # Devanagari is not in the bundled font


def test_text_that_cannot_fit_raises():
    with pytest.raises(CertificateRenderError, match="too long"):
        _render(event_name="W" * 60)  # one unbreakable word wider than the page


def test_unicode_latin_names_are_supported():
    assert _render(recipient_name="José Müller-Łukasiewicz").startswith(b"%PDF")


def test_generated_files_are_written_to_storage_and_are_distinct(client, settings, post_job):
    job_id = post_job(make_recipients(3)).json()["id"]
    items = client.get(f"/api/jobs/{job_id}/certificates").json()["items"]
    files = [settings.storage_dir / job_id / f"{i['id']}.pdf" for i in items]
    assert all(f.is_file() and f.read_bytes().startswith(b"%PDF") for f in files)
    assert len({f.read_bytes() for f in files}) == 3  # each certificate is personalised
    assert not list((settings.storage_dir / job_id).glob("*.tmp"))  # atomic writes leave no temp files
