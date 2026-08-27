"""Datasheet auto-finder: MPN matching, LCSC pick, TI slugs, routing."""

from __future__ import annotations

import asyncio

from backend.services.datasheet_finder import (
    DatasheetHit,
    _pick_lcsc_product,
    _ti_slugs,
    find_datasheet,
    mpn_catalog_match,
    mpn_matches,
    mpn_query_variants,
)


def test_mpn_matches_exact_and_packing():
    assert mpn_matches("CH340E", "CH340E")
    assert mpn_matches("SPX3819M5-L-3-3", "SPX3819M5-L-3-3/TR")
    assert mpn_matches("MSPM0G3507SPTR", "MSPM0G3507")
    assert mpn_matches("MSPM0G3507", "MSPM0G3507SPTR")
    assert mpn_matches("25AA1024-I_SM", "25AA1024-I/SM")
    # Variant letter is a different die — must not match
    assert not mpn_matches("CH340", "CH340E")
    assert not mpn_matches("CH340E", "CH340G")
    assert not mpn_matches("TLV9062", "TLV9002")


def test_mpn_catalog_match_orderable_suffix():
    assert mpn_catalog_match("ESP32-S31-WROOM-3", "ESP32-S31-WROOM-3-N16R16V")
    assert mpn_catalog_match("24AA025E64", "24AA025E64-I/SN")
    assert mpn_catalog_match("LAN8720A", "LAN8720A-CP-TR")
    assert mpn_catalog_match("W25Q128JVS", "W25Q128JVSIQ")
    assert not mpn_catalog_match("CH340", "CH340E")
    assert not mpn_catalog_match("10uF", "GRM21BR61A106KE19L")


def test_mpn_query_variants_underscore_and_reel():
    variants = mpn_query_variants("25AA1024-I_SM")
    assert "25AA1024-I/SM" in variants
    variants = mpn_query_variants("ADAU1467WBCPZ300R")
    assert "ADAU1467WBCPZ300" in variants


def test_pick_lcsc_family_orderable():
    products = [
        {
            "productModel": "ESP32-S31-WROOM-3-N16R16V",
            "pdfUrl": "http://esp.pdf",
        },
    ]
    picked = _pick_lcsc_product("ESP32-S31-WROOM-3", products)
    assert picked is not None
    assert picked["pdfUrl"] == "http://esp.pdf"


def test_pick_lcsc_prefers_exact_model():
    products = [
        {"productModel": "TPSPX3819M5-L-3-3", "pdfUrl": "http://a.pdf"},
        {"productModel": "SPX3819M5-L-3-3", "pdfUrl": "http://b.pdf"},
        {"productModel": "SPX3819M5-L-3-3/TR", "pdfUrl": "http://c.pdf"},
    ]
    picked = _pick_lcsc_product("SPX3819M5-L-3-3", products)
    assert picked is not None
    assert picked["pdfUrl"] == "http://b.pdf"


def test_pick_lcsc_packing_fallback():
    products = [
        {"productModel": "CH340E/TR", "pdfUrl": "http://e.pdf"},
    ]
    picked = _pick_lcsc_product("CH340E", products)
    assert picked is not None
    assert picked["pdfUrl"] == "http://e.pdf"


def test_pick_lcsc_rejects_unrelated():
    products = [
        {"productModel": "CH340G", "pdfUrl": "http://g.pdf"},
        {"productModel": "USB3300", "pdfUrl": "http://u.pdf"},
    ]
    assert _pick_lcsc_product("CH340E", products) is None


def test_ti_slugs_include_family():
    slugs = _ti_slugs("MSPM0G3507SPTR")
    assert slugs[0] == "mspm0g3507sptr"
    assert "mspm0g3507" in slugs
    # Must not clip "sptr" as if it were "...sp" + "tr".
    assert "mspm0g3507sp" not in slugs
    assert "mspm0g3507s" not in slugs


def test_find_datasheet_uses_lcsc_then_skips_empty(monkeypatch):
    async def fake_lcsc(mpn, lcsc_id=None):
        return DatasheetHit(
            mpn, pdf_bytes=b"%PDF-" + b"x" * 8000, url="https://datasheet.lcsc.com/x.pdf",
            source="lcsc",
        )

    monkeypatch.setattr(
        "backend.services.datasheet_finder._from_lcsc", fake_lcsc,
    )

    async def boom(mpn):
        raise AssertionError("later sources should not run")

    monkeypatch.setattr("backend.services.datasheet_finder._from_ti", boom)
    monkeypatch.setattr("backend.services.datasheet_finder._from_digikey", boom)

    hit = asyncio.run(find_datasheet("CH340E"))
    assert hit.ok
    assert hit.source == "lcsc"
    assert hit.pdf_bytes.startswith(b"%PDF-")


def test_find_datasheet_falls_through_to_ti(monkeypatch):
    async def miss_lcsc(mpn, lcsc_id=None):
        return None

    async def hit_ti(mpn):
        return DatasheetHit(
            mpn, pdf_bytes=b"%PDF-" + b"t" * 8000,
            url="https://www.ti.com/lit/ds/symlink/mspm0g3507.pdf",
            source="ti",
        )

    monkeypatch.setattr("backend.services.datasheet_finder._from_lcsc", miss_lcsc)
    monkeypatch.setattr("backend.services.datasheet_finder._from_ti", hit_ti)

    async def boom(mpn):
        raise AssertionError("digikey should not run")

    monkeypatch.setattr("backend.services.datasheet_finder._from_digikey", boom)

    hit = asyncio.run(find_datasheet("MSPM0G3507SPTR"))
    assert hit.ok
    assert hit.source == "ti"


def test_find_datasheet_all_miss(monkeypatch):
    async def miss(*args, **kwargs):
        return None

    monkeypatch.setattr("backend.services.datasheet_finder._from_lcsc", miss)
    monkeypatch.setattr("backend.services.datasheet_finder._from_ti", miss)
    monkeypatch.setattr("backend.services.datasheet_finder._from_digikey", miss)

    hit = asyncio.run(find_datasheet("NOTAREALPART123"))
    assert not hit.ok
    assert "No datasheet found" in (hit.error or "")
