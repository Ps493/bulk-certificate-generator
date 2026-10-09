"""Certificate rendering: the single predefined template, drawn with ReportLab.

``render_certificate`` is a pure function (data in, PDF bytes out). It knows
nothing about the database or the filesystem, which keeps it easy to test and
easy to swap for another engine.
"""
from __future__ import annotations

import io
import threading
from datetime import date
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
REGULAR = "CertSans"
BOLD = "CertSans-Bold"

NAVY = HexColor("#1b2a49")
GOLD = HexColor("#b8892b")
GREY = HexColor("#555555")
PAPER = HexColor("#fffdf7")

PAGE_W, PAGE_H = landscape(A4)  # 842 x 595 pt

_font_lock = threading.Lock()
_fonts_ready = False


class CertificateRenderError(Exception):
    """Raised when the data cannot be drawn on the template."""


def _ensure_fonts() -> None:
    global _fonts_ready
    if _fonts_ready:
        return
    with _font_lock:
        if _fonts_ready:
            return
        pdfmetrics.registerFont(TTFont(REGULAR, str(FONT_DIR / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont(BOLD, str(FONT_DIR / "DejaVuSans-Bold.ttf")))
        _fonts_ready = True


def _check_glyphs(text: str, font_name: str, label: str) -> None:
    """Fail loudly instead of printing blank boxes for characters the font lacks."""
    face = pdfmetrics.getFont(font_name).face
    missing = sorted({ch for ch in text if ord(ch) not in face.charToGlyph})
    if missing:
        raise CertificateRenderError(
            f"{label} contains characters the certificate font cannot render: {''.join(missing)!r}"
        )


def _fit_font_size(text: str, font: str, start: float, minimum: float, max_width: float) -> float:
    size = start
    while size > minimum and pdfmetrics.stringWidth(text, font, size) > max_width:
        size -= 1
    if pdfmetrics.stringWidth(text, font, size) > max_width:
        raise CertificateRenderError(f"text is too long to fit on the certificate: {text[:30]!r}...")
    return size


def _draw_wrapped(c, text, font, size, y, max_width, leading, max_lines) -> float:
    lines = simpleSplit(text, font, size, max_width)
    if len(lines) > max_lines:
        raise CertificateRenderError(f"text needs {len(lines)} lines, template allows {max_lines}")
    # simpleSplit cannot break a single very long word; don't let it spill off the page.
    if any(pdfmetrics.stringWidth(line, font, size) > max_width for line in lines):
        raise CertificateRenderError(f"text contains a word too long to fit on the certificate: {text[:30]!r}...")
    c.setFont(font, size)
    for line in lines:
        c.drawCentredString(PAGE_W / 2, y, line)
        y -= leading
    return y


def _format_date(d: date) -> str:
    return f"{d.day} {d:%B %Y}"


def render_certificate(
    *,
    recipient_name: str,
    achievement: str,
    event_name: str,
    issuer: str,
    issued_on: date,
    certificate_id: str,
) -> bytes:
    _ensure_fonts()
    for text, font, label in (
        (recipient_name, BOLD, "name"),
        (achievement, REGULAR, "achievement"),
        (event_name, BOLD, "event name"),
        (issuer, REGULAR, "issuer"),
    ):
        _check_glyphs(text, font, label)

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H), pageCompression=1)
    c.setTitle(f"Certificate - {recipient_name}")
    c.setAuthor(issuer)
    c.setSubject(event_name)

    # Background and borders
    c.setFillColor(PAPER)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    c.setStrokeColor(NAVY)
    c.setLineWidth(6)
    c.rect(24, 24, PAGE_W - 48, PAGE_H - 48)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.5)
    c.rect(36, 36, PAGE_W - 72, PAGE_H - 72)

    cx = PAGE_W / 2
    text_width = PAGE_W - 200

    # Heading
    c.setFillColor(NAVY)
    c.setFont(BOLD, 44)
    c.drawCentredString(cx, PAGE_H - 120, "CERTIFICATE")
    c.setFillColor(GOLD)
    c.setFont(REGULAR, 16)
    c.drawCentredString(cx, PAGE_H - 148, "OF ACHIEVEMENT")
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.line(cx - 60, PAGE_H - 162, cx + 60, PAGE_H - 162)

    # Recipient
    c.setFillColor(GREY)
    c.setFont(REGULAR, 15)
    c.drawCentredString(cx, PAGE_H - 200, "This certificate is proudly presented to")

    name_size = _fit_font_size(recipient_name, BOLD, 40, 10, PAGE_W - 160)
    c.setFillColor(NAVY)
    c.setFont(BOLD, name_size)
    c.drawCentredString(cx, PAGE_H - 255, recipient_name)
    half = min(pdfmetrics.stringWidth(recipient_name, BOLD, name_size) / 2 + 20, (PAGE_W - 160) / 2)
    c.setStrokeColor(GOLD)
    c.line(cx - half, PAGE_H - 268, cx + half, PAGE_H - 268)

    # Reason + event
    c.setFillColor(GREY)
    y = _draw_wrapped(
        c, f"in recognition of their contribution as {achievement} in", REGULAR, 15,
        PAGE_H - 305, text_width, 20, max_lines=2,
    )
    c.setFillColor(NAVY)
    _draw_wrapped(c, event_name, BOLD, 24, y - 12, text_width, 30, max_lines=3)

    # Footer: date (left), issuer (right), certificate id (centre)
    for x_center, value, caption, font in (
        (170, _format_date(issued_on), "Date of issue", REGULAR),
        (PAGE_W - 170, issuer, "Issued by", REGULAR),
    ):
        size = _fit_font_size(value, font, 14, 8, 220)
        c.setFillColor(NAVY)
        c.setFont(font, size)
        c.drawCentredString(x_center, 112, value)
        c.setStrokeColor(GOLD)
        c.line(x_center - 100, 106, x_center + 100, 106)
        c.setFillColor(GREY)
        c.setFont(REGULAR, 9)
        c.drawCentredString(x_center, 92, caption)

    c.setFillColor(GREY)
    c.setFont(REGULAR, 9)
    c.drawCentredString(cx, 60, f"Certificate ID: {certificate_id}")

    c.showPage()
    c.save()
    return buf.getvalue()
