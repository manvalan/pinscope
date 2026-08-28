"""submit_review parsing — ERROR without a datasheet quote is demoted."""

from backend.pinscopex.validate import _parse_review


def test_error_without_quote_becomes_unverified_warning():
    result = _parse_review(
        {
            "findings": [
                {
                    "finding": "U14 unidirectional clamp clips audio",
                    "why": "IO-to-GND diode conducts at -0.8 V.",
                    "status": "ERROR",
                    "source_page": 3,
                    "source_quote": "",
                    "recommendation": "Use a bidirectional array.",
                }
            ],
            "checked_areas": ["ESD"],
        },
        "U14",
        "TPD2E007DCKR",
    )
    assert len(result.findings) == 1
    f = result.findings[0]
    assert f.status == "WARNING"
    assert f.why.startswith("Unverified: no verbatim datasheet quote.")


def test_error_with_quote_stays_error():
    result = _parse_review(
        {
            "findings": [
                {
                    "finding": "FB5 floating",
                    "why": "FB5 is NC; DCDC5 SW is loaded.",
                    "status": "ERROR",
                    "source_page": 12,
                    "source_quote": "Connect FB5 to the output sense node.",
                }
            ],
            "checked_areas": [],
        },
        "U16",
        "AXP2101",
    )
    assert result.findings[0].status == "ERROR"
    assert not result.findings[0].why.startswith("Unverified:")
