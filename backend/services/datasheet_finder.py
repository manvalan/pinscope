"""Automatic datasheet lookup — LCSC, manufacturer URLs, optional DigiKey.

DeepSeek-adapted Pinscope still needs the actual PDF. The original wizard
only auto-fetched via DigiKey, which requires paid API keys and often
fails when the manufacturer CDN blocks the download.

This module tries, in order:

1. LCSC product search (no API key) — exact MPN match, then packing-suffix
   variants (``/TR``, ``SPTR``, …).
2. Direct manufacturer URLs for vendors with stable datasheet paths (TI).
3. DigiKey, if ``DIGIKEY_CLIENT_ID`` / ``SECRET`` are configured.

Never raises: every failure is captured on :class:`DatasheetHit`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from backend.config import settings

log = logging.getLogger(__name__)

_PDF_MAGIC = b"%PDF-"
_MIN_PDF_SIZE = 5_000
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Pinscope/2.8"
)
_LCSC_BASE = "https://wmsc.lcsc.com/ftps/wm"

# Remainder after a common prefix that we treat as packing / orderable-code,
# not a different die (CH340 vs CH340E is a different part — rejected).
_PACKING_REMAINDER = re.compile(
    r"^(S?P?TR|TR|T|R|MTR|PBF|CT|AT|XT|G4|EVM|ND)$",
    re.IGNORECASE,
)

_TI_PREFIXES = (
    "mspm", "msp430", "tms", "tlv", "tps", "sn74", "sn54", "iso", "tmp1",
    "tmp2", "tmp3", "ina", "ads1", "ads8", "ads9", "tcan", "tmux", "opa",
    "ths", "ref3", "ref5", "ref6", "ucc", "bq2", "bq3", "csd", "drv",
    "tpd", "txb", "txs", "am26", "lm3", "lm2", "lm7", "lmx", "sitara",
)


@dataclass
class DatasheetHit:
    mpn: str
    pdf_bytes: bytes | None = None
    error: str | None = None
    url: str | None = None
    source: str | None = None  # "lcsc" | "ti" | "digikey" | ...
    catalog_mpn: str | None = None  # orderable code that actually matched

    @property
    def ok(self) -> bool:
        return self.pdf_bytes is not None

    @property
    def alias_mpns(self) -> list[str]:
        extra = (self.catalog_mpn or "").strip()
        if extra and extra.upper() != (self.mpn or "").upper():
            return [extra]
        return []


def _alnum(mpn: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", mpn.upper())


def mpn_matches(query: str, candidate: str) -> bool:
    """True when ``candidate`` is the same part as ``query``, allowing
    packing / tape-reel suffixes but not variant letters (CH340 vs CH340E)."""
    q = _alnum(query)
    c = _alnum(candidate)
    if not q or not c:
        return False
    if q == c:
        return True
    longer, shorter = (q, c) if len(q) >= len(c) else (c, q)
    if not longer.startswith(shorter):
        return False
    return bool(_PACKING_REMAINDER.match(longer[len(shorter):]))


# Base MPNs this long are treated as a manufacturer family code: DigiKey/LCSC
# orderable strings may add package/temp (24AA025E64 vs 24AA025E64-I/SN).
# Keep this above short tokens like "10uF" / "CH340" so we do not steal a
# sibling die's datasheet.
_MIN_FAMILY_LEN = 7


def mpn_query_variants(mpn: str) -> list[str]:
    """Search strings to try when catalogs spell the same MPN differently."""
    raw = (mpn or "").strip()
    if not raw:
        return []
    variants: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            variants.append(value)

    add(raw)
    add(raw.replace("_", "/"))
    add(raw.replace("/", "_"))
    for sep in (" — ", " – ", " - "):
        if sep in raw:
            add(raw.split(sep, 1)[0])
            break
    if "," in raw:
        add(raw.split(",", 1)[0])
    if len(_alnum(raw)) > 8 and raw[-1] in "Rr" and raw[-2].isalnum():
        add(raw[:-1])
    stripped = _strip_packing_alnum(raw)
    if stripped:
        add(stripped)
    return variants


def mpn_catalog_match(query: str, candidate: str) -> bool:
    """Same part for datasheet lookup: punctuation, packing, or orderable suffix."""
    if mpn_matches(query, candidate):
        return True
    q = _alnum(query)
    c = _alnum(candidate)
    if len(q) < _MIN_FAMILY_LEN or not c:
        return False
    return c.startswith(q) and len(c) > len(q)


def find_local_pdf(pdf_dir: Path, mpn: str) -> Path | None:
    """Find a datasheet PDF whose filename is this MPN or a catalog alias.

    ``ESP32-S31-WROOM-3`` matches ``ESP32-S31-WROOM-3-N16R16V.pdf`` and the
    reverse — packing / flash-size suffixes, not sibling dies (CH340 vs CH340E).
    """
    from backend.pinscopex.utils import safe_mpn

    if not mpn or not pdf_dir.is_dir():
        return None
    for name in mpn_query_variants(mpn) or [mpn]:
        hit = pdf_dir / f"{safe_mpn(name)}.pdf"
        if hit.is_file():
            return hit
    want = _alnum(mpn)
    if len(want) < _MIN_FAMILY_LEN:
        return None
    family_hit: Path | None = None
    for hit in pdf_dir.glob("*.pdf"):
        stem = hit.stem
        if mpn_matches(mpn, stem) or mpn_catalog_match(mpn, stem) or mpn_catalog_match(stem, mpn):
            got = _alnum(stem)
            if got == want or mpn_matches(mpn, stem):
                return hit
            family_hit = family_hit or hit
    return family_hit


def _pick_lcsc_product(mpn: str, products: list[dict]) -> dict | None:
    exact: dict | None = None
    loose: dict | None = None
    family: dict | None = None
    want = _alnum(mpn)
    for p in products:
        model = (
            p.get("productModel")
            or p.get("productName")
            or p.get("productIntroEn")
            or ""
        )
        if not model:
            continue
        got = _alnum(model)
        if got == want:
            exact = p
            break
        if loose is None and mpn_matches(mpn, model):
            loose = p
        elif family is None and mpn_catalog_match(mpn, model):
            family = p
    return exact or loose or family


async def _download_pdf(url: str) -> bytes:
    async with httpx.AsyncClient(
        timeout=25, follow_redirects=True, headers={"User-Agent": _UA, "Accept": "*/*"},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.content
    if not data.startswith(_PDF_MAGIC):
        raise ValueError("Downloaded file is not a valid PDF")
    if len(data) < _MIN_PDF_SIZE:
        raise ValueError(f"PDF too small ({len(data)} bytes)")
    return data


async def _lcsc_search(keyword: str) -> list[dict]:
    async with httpx.AsyncClient(
        timeout=20,
        headers={"User-Agent": _UA, "Content-Type": "application/json", "Accept": "application/json"},
    ) as client:
        resp = await client.post(
            f"{_LCSC_BASE}/product/query/list",
            json={"keyword": keyword, "currentPage": 1, "pageSize": 30},
        )
        resp.raise_for_status()
        data = resp.json()
    result = data.get("result") or {}
    return result.get("dataList") or []


async def _lcsc_detail(product_code: str) -> dict | None:
    async with httpx.AsyncClient(
        timeout=20,
        headers={"User-Agent": _UA, "Accept": "application/json"},
    ) as client:
        resp = await client.get(
            f"{_LCSC_BASE}/product/detail",
            params={"productCode": product_code},
        )
        resp.raise_for_status()
        data = resp.json()
    result = data.get("result")
    return result if isinstance(result, dict) else None


def _pdf_url_from_product(product: dict) -> str | None:
    url = product.get("pdfUrl") or product.get("pdfURL") or product.get("pdfLinkUrl")
    if url and isinstance(url, str) and url.startswith("http"):
        return url
    return None


async def _from_lcsc(mpn: str, lcsc_id: str | None) -> DatasheetHit | None:
    product: dict | None = None
    if lcsc_id:
        code = lcsc_id.strip().upper()
        if not code.startswith("C"):
            code = "C" + code
        try:
            product = await _lcsc_detail(code)
        except Exception as exc:
            log.info("LCSC detail %s failed: %s", code, exc)

    if product is not None and not _pdf_url_from_product(product):
        product = None

    if product is None:
        keywords = mpn_query_variants(mpn)
        for keyword in keywords:
            try:
                products = await _lcsc_search(keyword)
            except Exception as exc:
                log.info("LCSC search %s failed: %s", keyword, exc)
                continue
            product = _pick_lcsc_product(mpn, products)
            if product:
                break

    if not product:
        return None
    url = _pdf_url_from_product(product)
    if not url:
        return None
    try:
        pdf = await _download_pdf(url)
    except Exception as exc:
        log.info("LCSC PDF download failed for %s (%s): %s", mpn, url, exc)
        return DatasheetHit(mpn, error=f"LCSC download failed: {exc}", url=url, source="lcsc")
    catalog = (
        product.get("productModel")
        or product.get("productName")
        or ""
    )
    log.info("Fetched datasheet for %s via LCSC (%d KB)", mpn, len(pdf) // 1024)
    return DatasheetHit(
        mpn, pdf_bytes=pdf, url=url, source="lcsc",
        catalog_mpn=str(catalog) or None,
    )


def _strip_packing_alnum(mpn: str) -> str | None:
    """Return the alnum MPN with a trailing packing code removed, if any."""
    compact = _alnum(mpn)
    for suf in ("SPTR", "PTR", "MTR", "TR"):
        if compact.endswith(suf) and len(compact) > len(suf) + 3:
            return compact[: -len(suf)]
    return None


_TI_PACKAGE_SUFFIXES = (
    "dbvr", "dbvt", "dbv", "pwr", "pwt", "pw", "rger", "rget", "rge",
    "dgsr", "dgsk", "dgs", "ydtr", "ydt", "dcnr", "dcnt", "dcn",
    "rgtr", "rgtt", "rgt", "runr", "runt", "dckr", "dckt", "dck",
)


def _ti_slugs(mpn: str) -> list[str]:
    """Candidate TI datasheet slugs, most specific first."""
    raw = mpn.lower().replace("/", "-").strip("-")
    slugs: list[str] = []

    def add(value: str) -> None:
        value = value.strip("-")
        if value and value not in slugs:
            slugs.append(value)

    add(raw)
    for suffix in ("-t/r", "/tr", "-tr", "-reel", "sptr", "ptr", "mtr", "tr"):
        if raw.endswith(suffix) and len(raw) > len(suffix) + 3:
            add(raw[: -len(suffix)].rstrip("-"))
            break
    for pkg in _TI_PACKAGE_SUFFIXES:
        if raw.endswith(pkg) and len(raw) > len(pkg) + 4:
            add(raw[: -len(pkg)])
            break
    # Orderable INA228AQDGSRQ1 → datasheet ina228-q1 / ina228
    prefixes = sorted(_TI_PREFIXES, key=len, reverse=True)
    for prefix in prefixes:
        if not raw.startswith(prefix):
            continue
        rest = raw[len(prefix):]
        m = re.match(r"(\d{2,})", rest)
        if not m:
            break
        family = f"{prefix}{m.group(1)}"
        add(family)
        if "q1" in raw:
            add(f"{family}-q1")
        break
    return slugs


def _looks_like_ti(mpn: str) -> bool:
    s = mpn.lower()
    return any(s.startswith(p) for p in _TI_PREFIXES)


async def _from_ti(mpn: str) -> DatasheetHit | None:
    if not _looks_like_ti(mpn):
        return None
    last_err = None
    last_url = None
    for slug in _ti_slugs(mpn):
        url = f"https://www.ti.com/lit/ds/symlink/{slug}.pdf"
        last_url = url
        try:
            pdf = await _download_pdf(url)
        except Exception as exc:
            last_err = exc
            continue
        log.info("Fetched datasheet for %s via TI (%s, %d KB)", mpn, slug, len(pdf) // 1024)
        return DatasheetHit(mpn, pdf_bytes=pdf, url=url, source="ti")
    if last_err:
        log.info("TI lookup missed %s: %s", mpn, last_err)
        return DatasheetHit(mpn, error=f"TI download failed: {last_err}", url=last_url, source="ti")
    return None


async def _from_digikey(mpn: str) -> DatasheetHit | None:
    if not settings.use_digikey:
        return None
    from backend.services.digikey import fetch_datasheet
    result = await fetch_datasheet(mpn)
    if result.ok:
        return DatasheetHit(
            mpn, pdf_bytes=result.pdf_bytes, url=result.url, source="digikey",
            catalog_mpn=getattr(result, "catalog_mpn", None),
        )
    return DatasheetHit(mpn, error=result.error, url=result.url, source="digikey")


async def find_datasheet(mpn: str, lcsc_id: str | None = None) -> DatasheetHit:
    """Find and download a datasheet PDF for ``mpn``.

    Tries LCSC, then TI (when the MPN looks like a TI part), then DigiKey.
    """
    mpn = (mpn or "").strip()
    if not mpn:
        return DatasheetHit(mpn, error="Empty MPN")

    errors: list[str] = []
    last_url: str | None = None

    for source_fn in (_from_lcsc, _from_ti, _from_digikey):
        try:
            if source_fn is _from_lcsc:
                hit = await _from_lcsc(mpn, lcsc_id)
            else:
                hit = await source_fn(mpn)  # type: ignore[misc]
        except Exception as exc:
            log.info("Datasheet source %s raised for %s: %s", source_fn.__name__, mpn, exc)
            errors.append(f"{source_fn.__name__}: {exc}")
            continue
        if hit is None:
            continue
        if hit.ok:
            return hit
        if hit.error:
            errors.append(f"{hit.source or source_fn.__name__}: {hit.error}")
        if hit.url:
            last_url = hit.url

    detail = "; ".join(errors) if errors else "No datasheet found"
    return DatasheetHit(mpn, error=detail, url=last_url)
