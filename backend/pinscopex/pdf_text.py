"""Stronger datasheet page text: reading-order blocks + table markdown.

Used by DeepSeek PDF ingest (review/extraction) and by quote verification
so both see the same reconstructed page.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

_SPARSE_CHARS = 80


def pdf_page_texts(pdf_path: Path | str) -> list[str]:
    """1-based page texts (index 0 unused). Empty list if the file cannot be read."""
    path = Path(pdf_path)
    blob = _pages_pymupdf(path)
    if blob is None:
        blob = _pages_pypdf(path)
    return blob


def extract_pdf_document_text(pdf_path: Path | str, *, max_chars: int) -> str:
    """Full datasheet dump with ``--- page N ---`` markers, truncated."""
    path = Path(pdf_path)
    pages = pdf_page_texts(path)
    n = max(0, len(pages) - 1)
    parts = [f"[PDF: {path.name}, {n} pages]"]
    for i in range(1, n + 1):
        body = (pages[i] or "").strip()
        parts.append(f"--- page {i} ---\n{body}")
    blob = "\n\n".join(parts)
    if len(blob) > max_chars:
        blob = blob[:max_chars] + "\n\n[truncated: remaining pages omitted]"
    return blob


def page_is_sparse(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    return len(compact) < _SPARSE_CHARS


def _pages_pymupdf(pdf_path: Path) -> list[str] | None:
    try:
        import fitz
    except ImportError:
        return None
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        log.warning("PyMuPDF failed to open %s: %s", pdf_path, exc)
        return None
    try:
        pages = [""]
        for page in doc:
            pages.append(fitz_page_text(page))
        return pages
    finally:
        doc.close()


def _pages_pypdf(pdf_path: Path) -> list[str]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(pdf_path))
    except Exception as exc:
        log.warning("pypdf failed to open %s: %s", pdf_path, exc)
        return []
    pages = [""]
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return pages


def fitz_page_text(page) -> str:
    """Reading-order text plus any reconstructed tables; flag sparse scans."""
    tables = _table_markdown(page)
    blocks = _blocks_text(page)
    chunks = [c for c in (blocks, tables) if c]
    text = "\n\n".join(chunks).strip()
    if page_is_sparse(text):
        note = "[low-text page: diagram or scan — use the page image]"
        text = f"{text}\n{note}".strip() if text else note
    return text


def _blocks_text(page) -> str:
    try:
        blocks = page.get_text("blocks") or []
    except Exception:
        try:
            return (page.get_text("text") or "").strip()
        except Exception:
            return ""
    lines: list[str] = []
    # (x0, y0, x1, y1, text, block_no, block_type, ...)
    textual = [b for b in blocks if len(b) >= 5 and str(b[4]).strip()]
    textual.sort(key=lambda b: (round(float(b[1]) / 6.0), float(b[0])))
    for b in textual:
        piece = str(b[4]).strip()
        if piece:
            lines.append(piece)
    if lines:
        return "\n".join(lines)
    try:
        return (page.get_text("text") or "").strip()
    except Exception:
        return ""


def _table_markdown(page) -> str:
    try:
        finder = page.find_tables()
    except Exception:
        return ""
    tables = getattr(finder, "tables", None) or []
    chunks: list[str] = []
    for table in tables:
        md = _one_table_markdown(table)
        if md:
            chunks.append(md)
    return "\n\n".join(chunks)


def _one_table_markdown(table) -> str:
    try:
        md = table.to_markdown()
        if md and md.strip():
            return md.strip()
    except Exception:
        pass
    try:
        rows = table.extract()
    except Exception:
        return ""
    if not rows:
        return ""
    out: list[str] = []
    for row in rows:
        cells = [re.sub(r"\s+", " ", str(c or "")).strip() for c in row]
        if any(cells):
            out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)
