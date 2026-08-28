"""Mouser Search API — optional fourth datasheet source (same MPN matching as DigiKey)."""

from __future__ import annotations

import logging

import httpx

from backend.config import settings
from backend.services.datasheet_finder import (
    _alnum,
    _download_pdf,
    mpn_catalog_match,
    mpn_matches,
    mpn_query_variants,
)

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.mouser.com/api/v1/search/partnumber"


class MouserFetchResult:
    def __init__(
        self,
        mpn: str,
        pdf_bytes: bytes | None = None,
        error: str | None = None,
        url: str | None = None,
        catalog_mpn: str | None = None,
    ):
        self.mpn = mpn
        self.pdf_bytes = pdf_bytes
        self.error = error
        self.url = url
        self.catalog_mpn = catalog_mpn

    @property
    def ok(self) -> bool:
        return self.pdf_bytes is not None


def _product_mpn(product: dict) -> str:
    return (
        product.get("ManufacturerPartNumber")
        or product.get("MouserPartNumber")
        or ""
    )


def _product_ds(product: dict) -> str:
    url = product.get("DataSheetUrl") or product.get("DatasheetUrl") or ""
    if url.startswith("//"):
        url = "https:" + url
    return url


def _pick_product(mpn: str, products: list[dict]) -> dict | None:
    exact: dict | None = None
    loose: dict | None = None
    family: dict | None = None
    want = _alnum(mpn)
    for product in products:
        cand = _product_mpn(product)
        if not cand:
            continue
        got = _alnum(cand)
        if got == want:
            exact = product
            break
        if loose is None and mpn_matches(mpn, cand):
            loose = product
        elif family is None and mpn_catalog_match(mpn, cand):
            family = product
    return exact or loose or family


async def _keyword_search(mpn: str) -> list[dict]:
    key = settings.mouser_api_key
    payload = {
        "SearchByPartRequest": {
            "mouserPartNumber": mpn,
            "partSearchOptions": "Exact",
        }
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            _SEARCH_URL,
            params={"apiKey": key},
            json=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
    result = data.get("SearchResults") or {}
    return result.get("Parts") or []


def pick_mouser_product(mpn: str, products: list[dict]) -> dict | None:
    """Public for tests — same family/packing rules as DigiKey/LCSC."""
    return _pick_product(mpn, products)


async def fetch_datasheet(mpn: str) -> MouserFetchResult:
    if not settings.use_mouser:
        return MouserFetchResult(mpn, error="Mouser API not configured")

    product: dict | None = None
    last_err: str | None = None
    for query in mpn_query_variants(mpn):
        try:
            products = await _keyword_search(query)
        except httpx.HTTPStatusError as exc:
            last_err = f"Mouser search failed ({exc.response.status_code})"
            logger.info("Mouser search %s: %s", query, last_err)
            continue
        except Exception as exc:
            last_err = f"Mouser search error: {exc}"
            logger.info("Mouser search %s: %s", query, last_err)
            continue
        product = _pick_product(mpn, products)
        if product:
            break

    if not product:
        return MouserFetchResult(mpn, error=last_err or "No datasheet found on Mouser")

    url = _product_ds(product)
    catalog = _product_mpn(product) or None
    if not url:
        return MouserFetchResult(
            mpn, error="No datasheet URL on Mouser", catalog_mpn=catalog,
        )

    try:
        pdf = await _download_pdf(url, mpn=mpn)
    except Exception as exc:
        logger.info("Mouser PDF download failed for %s (%s): %s", mpn, url, exc)
        return MouserFetchResult(
            mpn, error=f"Mouser download failed: {exc}", url=url, catalog_mpn=catalog,
        )
    logger.info("Fetched datasheet for %s via Mouser (%d KB)", mpn, len(pdf) // 1024)
    return MouserFetchResult(mpn, pdf_bytes=pdf, url=url, catalog_mpn=catalog)
