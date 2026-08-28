"""Deterministic check that a finding's datasheet quote is actually in the PDF.

The reviewer must cite verbatim text. This module extracts page text (PyMuPDF,
then pypdf) and looks for a normalized match on the cited page ±1. Failures
demote ERROR → WARNING and prefix ``why`` with ``Unverified:``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path

from backend.pinscopex.models import Finding
from backend.pinscopex.utils import safe_mpn

log = logging.getLogger(__name__)

_MIN_QUOTE_CHARS = 12
_EMPTY_PAGE_ALNUM = 40
_PAGE_WINDOW = 1


def normalize_quote(s: str) -> str:
    """Fold µ/μ, drop soft hyphens and linebreak hyphenation, squeeze space."""
    t = (s or "").replace("µ", "μ").replace("\u00ad", "")
    t = re.sub(r"-\s+", "", t)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9μ]+", "", normalize_quote(s))


def quote_in_text(quote: str, text: str) -> bool:
    """True if *quote* appears in *text* after the same folding the PDF viewer uses."""
    q = normalize_quote(quote)
    if len(q) < _MIN_QUOTE_CHARS:
        return False
    hay = normalize_quote(text)
    if q in hay:
        return True
    qa, ha = _alnum(quote), _alnum(text)
    return len(qa) >= _MIN_QUOTE_CHARS and qa in ha


def pdf_page_texts(pdf_path: Path) -> list[str]:
    """1-based page texts (index 0 unused). Empty list if the file cannot be read."""
    blob = _pages_pymupdf(pdf_path)
    if blob is None:
        blob = _pages_pypdf(pdf_path)
    return blob


def _pages_pymupdf(pdf_path: Path) -> list[str] | None:
    try:
        import fitz
    except ImportError:
        return None
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        log.warning("quote_verify: PyMuPDF failed on %s: %s", pdf_path, exc)
        return None
    try:
        pages = [""]
        for page in doc:
            try:
                pages.append(page.get_text("text") or "")
            except Exception:
                pages.append("")
        return pages
    finally:
        doc.close()


def _pages_pypdf(pdf_path: Path) -> list[str]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(pdf_path))
    except Exception as exc:
        log.warning("quote_verify: pypdf failed on %s: %s", pdf_path, exc)
        return []
    pages = [""]
    for page in reader.pages:
        try:
            pages.append(page.extract_text() or "")
        except Exception:
            pages.append("")
    return pages


def locate_quote(
    pdf_path: Path,
    page: int | None,
    quote: str,
    *,
    window: int = _PAGE_WINDOW,
) -> tuple[str, int | None]:
    """Return ``(ok|missing_quote|not_found|page_empty|no_pdf|bad_page, matched_page)``."""
    q = (quote or "").strip()
    if len(normalize_quote(q)) < _MIN_QUOTE_CHARS:
        return ("missing_quote", None)
    if not pdf_path.is_file():
        return ("no_pdf", None)
    pages = pdf_page_texts(pdf_path)
    n = len(pages) - 1
    if n < 1:
        return ("no_pdf", None)
    if page is None or not isinstance(page, int) or page < 1:
        # Search the whole file; keep the first hit.
        for i in range(1, n + 1):
            if quote_in_text(q, pages[i]):
                return ("ok", i)
        if max(len(_alnum(p)) for p in pages[1:]) < _EMPTY_PAGE_ALNUM:
            return ("page_empty", None)
        return ("not_found", None)

    lo = max(1, page - window)
    hi = min(n, page + window)
    matched: int | None = None
    any_text = False
    for i in range(lo, hi + 1):
        if len(_alnum(pages[i])) >= _EMPTY_PAGE_ALNUM:
            any_text = True
        if quote_in_text(q, pages[i]):
            matched = i
            break
    if matched is not None:
        return ("ok", matched)
    if not any_text:
        return ("page_empty", None)
    if page > n:
        return ("bad_page", None)
    return ("not_found", None)


_REASONS = {
    "missing_quote": "no verbatim datasheet quote.",
    "not_found": "cited text not found on the datasheet page.",
    "page_empty": "cited page has no extractable text (figure or scan).",
    "no_pdf": "datasheet PDF unavailable to check the quote.",
    "bad_page": "source_page missing or out of range.",
}


def _mark_unverified(finding: Finding, reason_key: str) -> None:
    if finding.status == "ERROR":
        finding.status = "WARNING"
    msg = _REASONS[reason_key]
    if not finding.why.startswith("Unverified:"):
        finding.why = f"Unverified: {msg} {finding.why}".strip()


def verify_finding_citations(
    findings: list[Finding],
    *,
    default_pdf: Path,
    default_mpn: str,
    pdf_dir: Path | None = None,
    mpn_by_designator: dict[str, str] | None = None,
    pdf_for_mpn: Callable[[str], Path | None] | None = None,
) -> None:
    """Mutate *findings* in place: check each ``source_quote`` against the PDF."""
    mpn_by_designator = mpn_by_designator or {}
    pdf_dir = pdf_dir or default_pdf.parent
    cache: dict[str, Path | None] = {}

    def resolve_pdf(finding: Finding) -> Path:
        mpn = default_mpn
        if finding.source_designator:
            mpn = mpn_by_designator.get(finding.source_designator) or default_mpn
        if pdf_for_mpn is not None:
            hit = pdf_for_mpn(mpn)
            if hit is not None:
                return hit
        key = mpn
        if key not in cache:
            p = pdf_dir / f"{safe_mpn(mpn)}.pdf"
            cache[key] = p if p.is_file() else None
        return cache[key] or default_pdf

    for finding in findings:
        pdf = resolve_pdf(finding)
        reason, matched = locate_quote(pdf, finding.source_page, finding.source_quote)
        if reason == "ok":
            if matched is not None and finding.source_page != matched:
                finding.source_page = matched
                finding.reference = re.sub(
                    r"p\.\S+$",
                    f"p.{matched}",
                    finding.reference or f"{default_mpn} datasheet p.{matched}",
                )
            continue
        _mark_unverified(finding, reason)
