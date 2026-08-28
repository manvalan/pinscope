"""PDF ingest: PyMuPDF text, keyword-selected vision pages, abs-max coerce."""

from __future__ import annotations

from pathlib import Path

from backend.services.extraction import _coerce_abs_max
from backend.services.llm.pdf_ingest import (
    extract_pdf_text,
    make_text_pdf,
    relevant_page_indices,
    render_pdf_page_jpegs,
)


def test_extract_pdf_text_reads_later_pages(tmp_path: Path):
    pdf = tmp_path / "wide.pdf"
    pdf.write_bytes(make_text_pdf([
        "Title page",
        "Pin configuration VCC GND TXD",
        "Absolute maximum ratings VCC 6.0 V",
    ]))
    text = extract_pdf_text(pdf)
    assert "--- page 1 ---" in text
    assert "--- page 3 ---" in text
    assert "6.0 V" in text


def test_relevant_pages_prefer_abs_max_over_front_padding(tmp_path: Path):
    pages = [f"Filler overview page {i}" for i in range(1, 8)]
    pages.append("Absolute maximum ratings\nSupply voltage VCC 6.0 V")
    pdf = tmp_path / "long.pdf"
    pdf.write_bytes(make_text_pdf(pages))
    idx = relevant_page_indices(pdf, max_pages=6)
    assert (len(pdf.read_bytes()) > 0)
    # 0-based: keyword is on page 8 → index 7, plus neighbor 6
    assert 7 in idx
    assert len(idx) <= 6


def test_render_keyword_pages_not_only_front(tmp_path: Path):
    pages = ["Cover"] * 5
    pages.append("Absolute maximum ratings table VCC 6 V")
    pdf = tmp_path / "img.pdf"
    pdf.write_bytes(make_text_pdf(pages))
    images = render_pdf_page_jpegs(pdf, max_pages=4)
    page_nos = [n for n, _ in images]
    assert 6 in page_nos
    assert images
    assert images[0][1][:2] == b"\xff\xd8"  # JPEG


def test_sparse_page_is_flagged(tmp_path: Path):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(make_text_pdf(["   "]))
    text = extract_pdf_text(pdf)
    assert "low-text page" in text


def test_one_table_markdown_from_extract_rows():
    from backend.pinscopex.pdf_text import _one_table_markdown

    class _Table:
        def to_markdown(self):
            raise RuntimeError("no markdown")

        def extract(self):
            return [["Pin", "Name"], ["1", "VCC"]]

    md = _one_table_markdown(_Table())
    assert "VCC" in md
    assert "Pin" in md


def test_coerce_abs_max_keeps_valid_drops_junk():
    rows = _coerce_abs_max([
        {"parameter": "VCC", "max": "6", "unit": "V", "source_page": 12},
        {"parameter": "bad", "unit": "V"},  # no page
        "nope",
        {"parameter": "Tstg", "min": -40, "max": 125, "unit": "°C", "source_page": 12},
    ])
    assert len(rows) == 2
    assert rows[0]["parameter"] == "VCC"
    assert rows[0]["max"] == 6.0
    assert rows[0]["min"] is None
    assert rows[1]["min"] == -40.0
