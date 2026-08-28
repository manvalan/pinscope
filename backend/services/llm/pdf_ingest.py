"""Convert datasheet PDFs into text (and optional page images).

DeepSeek's Chat Completions API does not accept native PDF documents.
Anthropic/Gemini providers send the file bytes; DeepSeek instead extracts
text with PyMuPDF (pypdf fallback) and, on a vision model, renders the
pages that actually matter (pin tables, abs-max, electrical, application)
rather than always the first N pages.
"""

from __future__ import annotations

import base64
import io
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT_MAX_CHARS = 500_000
_DEFAULT_MAX_IMAGES = 32
_RENDER_ZOOM = 1.55

# Pages whose diagrams/tables the model must actually see.
_PAGE_KEYWORDS = re.compile(
    r"pin\s*(out|diagram|configuration|description|assignment|function|name|table|map)"
    r"|ball\s*map|package\s*(pin|drawing|outline)|signal\s+description"
    r"|absolute\s+maximum|recommended\s+operating|electrical\s+characteristics"
    r"|power\s+supply|thermal\s+(resistance|shutdown|pad)|ESD\s+(rating|tolerance)"
    r"|decoupling|bypass\s+capacitor|typical\s+application"
    r"|application\s+(circuit|schematic|information|note)|reference\s+design"
    r"|ordering\s+information|device\s+information",
    re.IGNORECASE,
)


def extract_pdf_text(path: Path | str, *, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """Return datasheet text with page markers, truncated to ``max_chars``.

    Prefers PyMuPDF (better on datasheet tables) and falls back to pypdf.
    """
    pdf_path = Path(path)
    blob = _extract_text_pymupdf(pdf_path)
    if blob is None:
        blob = _extract_text_pypdf(pdf_path)
    if len(blob) > max_chars:
        blob = blob[:max_chars] + "\n\n[truncated: remaining pages omitted]"
    return blob


def _extract_text_pymupdf(pdf_path: Path) -> str | None:
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
        parts: list[str] = [f"[PDF: {pdf_path.name}, {len(doc)} pages]"]
        for i, page in enumerate(doc, start=1):
            try:
                text = page.get_text("text") or ""
            except Exception:
                text = ""
            parts.append(f"--- page {i} ---\n{text.strip()}")
        return "\n\n".join(parts)
    finally:
        doc.close()


def _extract_text_pypdf(pdf_path: Path) -> str:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(pdf_path))
    except Exception as exc:
        log.warning("Failed to open PDF %s: %s", pdf_path, exc)
        return f"[PDF {pdf_path.name}: unreadable ({exc})]"

    parts: list[str] = [f"[PDF: {pdf_path.name}, {len(reader.pages)} pages]"]
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        parts.append(f"--- page {i} ---\n{text.strip()}")
    return "\n\n".join(parts)


def relevant_page_indices(
    path: Path | str,
    *,
    max_pages: int,
    keywords: re.Pattern[str] = _PAGE_KEYWORDS,
) -> list[int]:
    """0-based page indices to send as images: front matter + keyword hits."""
    pdf_path = Path(path)
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
    except Exception:
        return list(range(max_pages))
    try:
        total = len(doc)
        if total <= max_pages:
            return list(range(total))
        hits: set[int] = set()
        for i in range(total):
            try:
                text = doc[i].get_text("text") or ""
            except Exception:
                text = ""
            if keywords.search(text):
                for neighbor in (i - 1, i, i + 1):
                    if 0 <= neighbor < total:
                        hits.add(neighbor)
        front = set(range(min(5, total)))
        ranked_hits = sorted(hits)
        if len(ranked_hits) >= max_pages:
            keep_front = [i for i in ranked_hits if i < 5][:2]
            rest = [i for i in ranked_hits if i not in keep_front]
            need = max_pages - len(keep_front)
            return sorted(keep_front + rest[-need:])
        chosen = set(hits)
        for i in sorted(front) + list(range(total)):
            if len(chosen) >= max_pages:
                break
            chosen.add(i)
        return sorted(chosen)
    finally:
        doc.close()


def render_pdf_page_jpegs(
    path: Path | str,
    *,
    max_pages: int = _DEFAULT_MAX_IMAGES,
    zoom: float = _RENDER_ZOOM,
    page_indices: list[int] | None = None,
) -> list[tuple[int, bytes]]:
    """Render selected pages as JPEG bytes.

    ``page_indices`` is 0-based. When omitted, keyword-relevant pages are
    chosen instead of always rendering the front of the PDF.

    Returns a list of (1-based page number, jpeg bytes). Empty if PyMuPDF
    is not installed or rendering fails — callers should still send text.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        log.info("PyMuPDF not installed — DeepSeek vision page images skipped")
        return []

    pdf_path = Path(path)
    if page_indices is None:
        page_indices = relevant_page_indices(pdf_path, max_pages=max_pages)

    out: list[tuple[int, bytes]] = []
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        log.warning("PyMuPDF failed to open %s: %s", pdf_path, exc)
        return []

    try:
        matrix = fitz.Matrix(zoom, zoom)
        for i in page_indices:
            if i < 0 or i >= len(doc):
                continue
            page = doc[i]
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            jpeg = pix.tobytes("jpeg")
            out.append((i + 1, jpeg))
            if len(out) >= max_pages:
                break
    except Exception as exc:
        log.warning("PyMuPDF render failed for %s: %s", pdf_path, exc)
        return out
    finally:
        doc.close()
    return out


def jpeg_data_url(jpeg: bytes) -> str:
    b64 = base64.standard_b64encode(jpeg).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def pdf_to_openai_content(
    path: Path | str,
    *,
    vision: bool,
    max_chars: int = _DEFAULT_MAX_CHARS,
    max_images: int = _DEFAULT_MAX_IMAGES,
) -> list[dict]:
    """OpenAI-style content parts for one PDF: text, plus images if vision."""
    pdf_path = Path(path)
    try:
        mtime = pdf_path.stat().st_mtime_ns
    except OSError:
        mtime = 0
    key = (str(pdf_path.resolve()), mtime, vision, max_chars, max_images)
    cached = _PDF_CONTENT_CACHE.get(key)
    if cached is not None:
        return cached
    parts = _pdf_to_openai_content_uncached(
        pdf_path, vision=vision, max_chars=max_chars, max_images=max_images,
    )
    if len(_PDF_CONTENT_CACHE) > 32:
        _PDF_CONTENT_CACHE.clear()
    _PDF_CONTENT_CACHE[key] = parts
    return parts


_PDF_CONTENT_CACHE: dict[tuple, list[dict]] = {}


def _pdf_to_openai_content_uncached(
    path: Path,
    *,
    vision: bool,
    max_chars: int,
    max_images: int,
) -> list[dict]:
    text = extract_pdf_text(path, max_chars=max_chars)
    parts: list[dict] = [{"type": "text", "text": text}]
    if not vision:
        return parts
    images = render_pdf_page_jpegs(path, max_pages=max_images)
    if not images:
        return parts
    parts.append({
        "type": "text",
        "text": (
            f"The following {len(images)} image(s) are rendered pages of "
            f"{path.name} (pin tables, abs-max, electrical, and "
            f"application sections preferred over the front matter). "
            f"Use them for diagrams and tables that text extraction may have missed."
        ),
    })
    for page_no, jpeg in images:
        parts.append({
            "type": "text",
            "text": f"[page {page_no} image]",
        })
        parts.append({
            "type": "image_url",
            "image_url": {"url": jpeg_data_url(jpeg), "detail": "high"},
        })
    return parts


def make_text_pdf(pages: list[str]) -> bytes:
    """Build a tiny text-only PDF for tests. Uses PyMuPDF when available,
    otherwise a hand-rolled one-page PDF."""
    try:
        import fitz
        doc = fitz.open()
        for body in pages:
            page = doc.new_page()
            page.insert_text((72, 72), body, fontsize=11)
        buf = io.BytesIO()
        doc.save(buf)
        doc.close()
        return buf.getvalue()
    except ImportError:
        pass
    # Minimal one-page PDF with the first page's text.
    payload = (pages[0] if pages else "test").encode("latin-1", "replace")
    stream = b"BT /F1 12 Tf 72 720 Td (" + payload.replace(b"(", b"[").replace(b")", b"]") + b") Tj ET"
    return (
        b"%PDF-1.1\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        b"4 0 obj<</Length " + str(len(stream)).encode() + b">>stream\n"
        + stream + b"\nendstream\nendobj\n"
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"xref\n0 6\n0000000000 65535 f \n"
        b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n0\n%%EOF\n"
    )
