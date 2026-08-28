"""Datasheet quote must appear in the PDF text, not just in the model output."""

from pathlib import Path

from backend.pinscopex.models import Finding
from backend.pinscopex.quote_verify import (
    locate_quote,
    quote_in_text,
    verify_finding_citations,
)
from backend.pinscopex.validate import _parse_review
from backend.services.llm.pdf_ingest import make_text_pdf


def test_quote_in_text_folds_whitespace_and_mu():
    assert quote_in_text(
        "CIO = 12 pF  typical",
        "CIO = 12\npF typical",
    )
    assert quote_in_text("I/O capacitance 12 µF", "I/O capacitance 12 μF")
    assert not quote_in_text(
        "TPD2E007 IO-to-GND diode forward-conducts near -0.8 V",
        "The TPD2E007 is a bidirectional ESD protection device.",
    )


def test_locate_quote_page_window(tmp_path: Path):
    pdf = tmp_path / "part.pdf"
    pdf.write_bytes(make_text_pdf([
        "cover",
        "Working voltage Vrwm is ±13 V bidirectional back-to-back diodes.",
        "package drawing",
    ]))
    reason, page = locate_quote(
        pdf, 1,
        "Working voltage Vrwm is ±13 V bidirectional back-to-back diodes.",
    )
    assert reason == "ok"
    assert page == 2  # cited p.1, found on p.2 within ±1


def test_fake_quote_demotes_error(tmp_path: Path):
    pdf = tmp_path / "TPD2E007.pdf"
    pdf.write_bytes(make_text_pdf([
        "TPD2E007 2-channel ESD protection",
        "Bidirectional working voltage ±13 V. Suitable for audio interfaces.",
    ]))
    findings = [
        Finding(
            designator="U14",
            mpn="TPD2E007",
            finding="unidirectional clamp clips audio",
            why="IO-to-GND diode conducts at -0.8 V.",
            status="ERROR",
            source_page=2,
            source_quote="IO-to-GND diode forward-conducts near -0.8 V clipping audio",
            reference="TPD2E007 datasheet p.2",
        )
    ]
    verify_finding_citations(
        findings,
        default_pdf=pdf,
        default_mpn="TPD2E007",
    )
    assert findings[0].status == "WARNING"
    assert findings[0].why.startswith("Unverified: cited text not found")


def test_real_quote_keeps_error(tmp_path: Path):
    pdf = tmp_path / "AXP.pdf"
    pdf.write_bytes(make_text_pdf([
        "Connect FB5 to the output sense node of DCDC5.",
    ]))
    findings = [
        Finding(
            designator="U16",
            mpn="AXP2101",
            finding="FB5 floating",
            why="FB5 is NC.",
            status="ERROR",
            source_page=1,
            source_quote="Connect FB5 to the output sense node of DCDC5.",
            reference="AXP2101 datasheet p.1",
        )
    ]
    verify_finding_citations(
        findings, default_pdf=pdf, default_mpn="AXP2101",
    )
    assert findings[0].status == "ERROR"
    assert not findings[0].why.startswith("Unverified:")


def test_parse_warning_without_quote():
    result = _parse_review(
        {
            "findings": [
                {
                    "finding": "maybe clip",
                    "why": "typical unidirectional array",
                    "status": "WARNING",
                    "source_page": 3,
                    "source_quote": "",
                }
            ],
            "checked_areas": [],
        },
        "U14",
        "TPD2E007",
    )
    assert result.findings[0].status == "WARNING"
    assert result.findings[0].why.startswith("Unverified: no verbatim datasheet quote.")
