"""Automatic datasheet lookup — LCSC, manufacturer URLs, optional DigiKey.

DeepSeek-adapted Pinscope still needs the actual PDF. The original wizard
only auto-fetched via DigiKey, which requires paid API keys and often
fails when the manufacturer CDN blocks the download.

This module tries, in order:

1. Explicit BOM datasheet URL (``url_hint``).
2. LCSC product search (no API key) — exact MPN match, then packing-suffix
   variants (``/TR``, ``SPTR``, …).
3. Direct manufacturer URLs (TI, Espressif, ST, Analog, NXP, onsemi,
   Microchip, Murata, Silicon Labs) with HTML-interstitial follow when the
   CDN returns a page instead of a PDF.
4. Mouser, if ``MOUSER_API_KEY`` is configured.
5. DigiKey, if ``DIGIKEY_CLIENT_ID`` / ``SECRET`` are configured.

On a miss, ``suggested_urls`` lists every catalog/vendor link we found so
the wizard can open them in the user's browser (datacenter IPs are often
blocked).

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
    source: str | None = None  # "lcsc" | "ti" | "mouser" | "digikey" | ...
    catalog_mpn: str | None = None  # orderable code that actually matched
    suggested_urls: list[str] | None = None  # browser-open links on a miss

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


def _referer_for(url: str) -> str:
    from urllib.parse import urlparse

    p = urlparse(url)
    if not p.scheme or not p.netloc:
        return "https://www.google.com/"
    return f"{p.scheme}://{p.netloc}/"


def _looks_like_html(data: bytes) -> bool:
    head = data.lstrip()[:400].lower()
    return (
        head.startswith(b"<!doctype html")
        or head.startswith(b"<html")
        or b"<head" in head
        or b"<title" in head
    )


_PDF_HREF_RE = re.compile(
    r"""(?:href|src|content)\s*=\s*["']([^"']+\.pdf(?:\?[^"']*)?)["']""",
    re.IGNORECASE,
)


def _pdf_links_in_html(html: str, base_url: str, mpn: str) -> list[str]:
    """PDF hrefs on a landing page that still look like this MPN's datasheet."""
    from urllib.parse import urljoin, urlparse
    from pathlib import PurePosixPath

    want = _alnum(mpn)
    if len(want) < 5:
        return []
    stem_prefix = want[: min(6, len(want))]
    out: list[str] = []
    seen: set[str] = set()
    for match in _PDF_HREF_RE.finditer(html):
        href = match.group(1).strip()
        abs_url = urljoin(base_url, href)
        key = abs_url.split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        stem = PurePosixPath(urlparse(abs_url).path).stem
        blob = _alnum(stem) + _alnum(abs_url)
        if mpn_catalog_match(mpn, stem) or mpn_matches(mpn, stem) or stem_prefix in blob:
            out.append(key)
        if len(out) >= 3:
            break
    return out


async def _http_get(url: str) -> bytes:
    headers = {
        "User-Agent": _UA,
        "Accept": "application/pdf,application/octet-stream;q=0.9,text/html;q=0.8,*/*;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": _referer_for(url),
    }
    async with httpx.AsyncClient(
        timeout=25, follow_redirects=True, headers=headers,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def _as_pdf_bytes(data: bytes) -> bytes:
    if not data.startswith(_PDF_MAGIC):
        raise ValueError("Downloaded file is not a valid PDF")
    if len(data) < _MIN_PDF_SIZE:
        raise ValueError(f"PDF too small ({len(data)} bytes)")
    return data


async def _download_pdf(url: str, *, mpn: str | None = None, _hops: int = 0) -> bytes:
    """GET a URL and return PDF bytes.

    Vendor CDNs often 200 an HTML interstitial (captcha, cookie wall). If the
    body is HTML, follow at most one in-page ``.pdf`` link that still matches
    ``mpn``. ``http://`` is retried as ``https://``.
    """
    candidates = [url]
    if url.startswith("http://"):
        candidates.append("https://" + url[len("http://"):])
    last_err: Exception | None = None
    for candidate in candidates:
        try:
            data = await _http_get(candidate)
        except Exception as exc:
            last_err = exc
            continue
        try:
            return _as_pdf_bytes(data)
        except ValueError as exc:
            last_err = exc
            if _hops >= 1 or not mpn or not _looks_like_html(data):
                continue
            try:
                html = data.decode("utf-8", errors="ignore")
            except Exception:
                continue
            for href in _pdf_links_in_html(html, candidate, mpn):
                try:
                    return await _download_pdf(href, mpn=mpn, _hops=_hops + 1)
                except Exception as hop_exc:
                    last_err = hop_exc
                    continue
    raise last_err or ValueError("Download failed")


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
        pdf = await _download_pdf(url, mpn=mpn)
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


def _ti_urls(mpn: str) -> list[str]:
    if not _looks_like_ti(mpn):
        return []
    urls: list[str] = []
    for slug in _ti_slugs(mpn):
        urls.append(f"https://www.ti.com/lit/ds/symlink/{slug}.pdf")
        urls.append(f"https://www.ti.com/lit/gpn/{slug}.pdf")
    return urls


def _espressif_urls(mpn: str) -> list[str]:
    s = mpn.strip().lower().replace("_", "-")
    if not s.startswith("esp"):
        return []
    s = re.sub(r"-n\d+r\d+v?$", "", s)
    slugs: list[str] = []
    for val in (s, re.split(r"-wroom|-wrover|-pico", s)[0]):
        val = val.strip("-")
        if val and val not in slugs:
            slugs.append(val)
    base = "https://www.espressif.com/sites/default/files/documentation"
    return [f"{base}/{slug}_datasheet_en.pdf" for slug in slugs]


_ST_PREFIXES = (
    "stm32", "stm8", "stusb", "stspin", "usblc", "esda", "sm6t", "stth",
    "l78", "ld1117", "m24c", "vl53", "lsm6",
)


def _st_urls(mpn: str) -> list[str]:
    s = mpn.lower()
    if not any(s.startswith(p) for p in _ST_PREFIXES):
        return []
    compact = re.sub(r"[^a-z0-9-]", "", s.replace("/", "-"))
    return [f"https://www.st.com/resource/en/datasheet/{compact}.pdf"]


def _adi_family(mpn: str) -> str | None:
    s = re.sub(r"[^A-Z0-9]", "", mpn.upper())
    m = re.match(r"^((?:AD|LT|OP|ADP|LTC)[A-Z]*\d+)", s)
    return m.group(1) if m else None


def _adi_urls(mpn: str) -> list[str]:
    fam = _adi_family(mpn)
    if not fam:
        return []
    root = "https://www.analog.com/media/en/technical-documentation/data-sheets"
    return [f"{root}/{fam}.pdf", f"{root}/{fam.lower()}.pdf"]


_NXP_PREFIXES = ("pca", "pcf", "lpc", "imx", "tja", "pn5", "pn7", "kw4")


def _nxp_urls(mpn: str) -> list[str]:
    s = mpn.lower()
    if not any(s.startswith(p) for p in _NXP_PREFIXES):
        return []
    token = re.sub(r"[^A-Z0-9-]", "", mpn.split("/")[0].split(",")[0].strip().upper())
    if not token:
        return []
    return [f"https://www.nxp.com/docs/en/data-sheet/{token}.pdf"]


_ONSEMI_PREFIXES = ("ncp", "ncv", "ntd", "fdc", "cat24", "fusb")


def _onsemi_urls(mpn: str) -> list[str]:
    s = mpn.lower()
    if not any(s.startswith(p) for p in _ONSEMI_PREFIXES):
        return []
    slug = re.sub(r"[^a-z0-9]", "", s)
    return [f"https://www.onsemi.com/pdf/datasheet/{slug}.pdf"]


_MICROCHIP_PREFIXES = (
    "mcp", "pic16", "pic18", "pic24", "pic32", "dspic", "atsam", "atmega",
    "attiny", "24aa", "24lc", "24fc", "25aa", "25lc", "lan87", "lan74",
    "ksz", "usb25", "enc28",
)


def _microchip_family(mpn: str) -> str | None:
    s = mpn.lower()
    if not any(s.startswith(p) for p in _MICROCHIP_PREFIXES):
        return None
    token = re.split(r"[-/,]", s)[0]
    token = re.sub(r"[^a-z0-9]", "", token)
    return token or None


def _microchip_urls(mpn: str) -> list[str]:
    fam = _microchip_family(mpn)
    if not fam:
        return []
    compact = re.sub(r"[^A-Za-z0-9]", "", mpn.split("/")[0].split(",")[0])
    return [
        f"https://www.microchip.com/en-us/product/{fam}",
        f"https://ww1.microchip.com/downloads/en/DeviceDoc/{compact}.pdf",
        f"https://ww1.microchip.com/downloads/en/DeviceDoc/{fam.upper()}.pdf",
    ]


_MURATA_PREFIXES = ("grm", "gqm", "gjm", "lqw", "lqm", "lqg", "blm", "nfm", "dlw", "nfe")


def _murata_stem(mpn: str) -> str | None:
    s = mpn.strip().upper()
    if not any(s.startswith(p.upper()) for p in _MURATA_PREFIXES):
        return None
    stem = re.split(r"[/,]", s)[0]
    if stem.endswith("D") and len(stem) > 8:
        stem = stem[:-1]
    return stem


def _murata_urls(mpn: str) -> list[str]:
    stem = _murata_stem(mpn)
    if not stem:
        return []
    raw = mpn.split("/")[0].split(",")[0].strip()
    return [
        f"https://search.murata.co.jp/Ceramy/image/img/A01X/G101/ENG/{stem}-01.pdf",
        f"https://www.murata.com/en-us/products/productdetail?partno={raw}",
    ]


_SILABS_PREFIXES = ("si4", "si5", "efr32", "cp21", "bgm", "wgm", "efm32")


def _silabs_urls(mpn: str) -> list[str]:
    s = mpn.lower()
    if not any(s.startswith(p) for p in _SILABS_PREFIXES):
        return []
    compact = re.sub(r"[^A-Za-z0-9-]", "", mpn.split("/")[0].split(",")[0])
    parts = compact.split("-")
    family = parts[0]
    urls = [
        f"https://www.silabs.com/documents/public/data-sheets/{compact}.pdf",
        f"https://www.silabs.com/documents/public/data-sheets/{family}.pdf",
    ]
    if len(parts) >= 2:
        urls.insert(
            1,
            f"https://www.silabs.com/documents/public/data-sheets/{parts[0]}-{parts[1]}.pdf",
        )
    return urls


def manufacturer_pdf_candidates(mpn: str) -> list[tuple[str, str]]:
    """Stable vendor PDF URLs for this MPN (source, url), first match wins."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(source: str, urls: list[str]) -> None:
        for url in urls:
            if url not in seen:
                seen.add(url)
                out.append((source, url))

    add("ti", _ti_urls(mpn))
    add("espressif", _espressif_urls(mpn))
    add("st", _st_urls(mpn))
    add("analog", _adi_urls(mpn))
    add("nxp", _nxp_urls(mpn))
    add("onsemi", _onsemi_urls(mpn))
    add("microchip", _microchip_urls(mpn))
    add("murata", _murata_urls(mpn))
    add("silabs", _silabs_urls(mpn))
    return out


def suggested_pdf_urls(mpn: str, *extras: str | None) -> list[str]:
    """Deduped links the wizard can open in the user's browser."""
    out: list[str] = []
    seen: set[str] = set()

    def add(url: str | None) -> None:
        url = (url or "").strip()
        if url.startswith("http") and url not in seen:
            seen.add(url)
            out.append(url)

    for extra in extras:
        add(extra)
    for _source, url in manufacturer_pdf_candidates(mpn):
        add(url)
    return out


async def _from_manufacturer(mpn: str) -> DatasheetHit | None:
    """Direct manufacturer datasheet URLs (TI, ST, ADI, Espressif, Microchip, …)."""
    last_err = None
    last_url = None
    last_source = None
    for source, url in manufacturer_pdf_candidates(mpn):
        last_url = url
        last_source = source
        try:
            pdf = await _download_pdf(url, mpn=mpn)
        except Exception as exc:
            last_err = exc
            continue
        log.info(
            "Fetched datasheet for %s via %s (%s, %d KB)",
            mpn, source, url, len(pdf) // 1024,
        )
        return DatasheetHit(mpn, pdf_bytes=pdf, url=url, source=source)
    if last_err:
        log.info("Manufacturer lookup missed %s: %s", mpn, last_err)
        return DatasheetHit(
            mpn, error=f"{last_source} download failed: {last_err}",
            url=last_url, source=last_source,
        )
    return None


async def _from_ti(mpn: str) -> DatasheetHit | None:
    """Historical name — manufacturer URL table (TI plus other stable vendors)."""
    return await _from_manufacturer(mpn)


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


async def _from_mouser(mpn: str) -> DatasheetHit | None:
    if not settings.use_mouser:
        return None
    from backend.services.mouser import fetch_datasheet
    result = await fetch_datasheet(mpn)
    if result.ok:
        return DatasheetHit(
            mpn, pdf_bytes=result.pdf_bytes, url=result.url, source="mouser",
            catalog_mpn=getattr(result, "catalog_mpn", None),
        )
    if result.error or result.url:
        return DatasheetHit(mpn, error=result.error, url=result.url, source="mouser")
    return None


async def find_datasheet(
    mpn: str, lcsc_id: str | None = None, url_hint: str | None = None,
) -> DatasheetHit:
    """Find and download a datasheet PDF for ``mpn``.

    Tries an explicit BOM URL first, then LCSC, then manufacturer PDF
    URLs, then Mouser, then DigiKey. A miss always includes
    ``suggested_urls`` for a browser download.
    """
    mpn = (mpn or "").strip()
    if not mpn:
        return DatasheetHit(mpn, error="Empty MPN")

    errors: list[str] = []
    found_urls: list[str] = []

    hint = (url_hint or "").strip()
    if hint.startswith("http"):
        try:
            pdf = await _download_pdf(hint, mpn=mpn)
            return DatasheetHit(mpn, pdf_bytes=pdf, url=hint, source="bom")
        except Exception as exc:
            log.info("BOM datasheet URL missed %s: %s", mpn, exc)
            errors.append(f"bom: {exc}")
            found_urls.append(hint)

    for source_fn in (_from_lcsc, _from_ti, _from_mouser, _from_digikey):
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
            found_urls.append(hit.url)

    urls = suggested_pdf_urls(mpn, *found_urls)
    detail = "; ".join(errors) if errors else "No datasheet found"
    return DatasheetHit(
        mpn, error=detail, url=urls[0] if urls else None,
        suggested_urls=urls,
    )
