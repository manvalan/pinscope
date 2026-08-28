"""Shared component library: persist datasheets, catalog listing."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.services import projects as proj_svc
from backend.services.datasheet_store import resolve_datasheet
from backend.services.storage import LocalStorageBackend


PDF = b"%PDF-1.4\n" + b"x" * 8000


def _client(tmp_path) -> TestClient:
    from backend.main import app

    app.state.storage = LocalStorageBackend(tmp_path)
    return TestClient(app)


def test_library_alias_resolves_family_mpn(storage):
    from backend.services.datasheet_store import resolve_datasheet, store_datasheet_bytes

    store_datasheet_bytes(
        storage, PDF, "ESP32-S31-WROOM-3",
        extra_mpns=["ESP32-S31-WROOM-3-N16R16V"],
    )
    assert resolve_datasheet(storage, "ESP32-S31-WROOM-3")
    assert resolve_datasheet(storage, "ESP32-S31-WROOM-3-N16R16V")
    assert proj_svc.library_has_datasheet(storage, "ESP32-S31-WROOM-3")


def test_save_datasheet_also_stores_in_library(storage):
    meta = proj_svc.create_project(storage, "local", "board")
    key = proj_svc.save_datasheet(storage, "local", meta.id, "CH340E", PDF)
    assert key.endswith("CH340E.pdf")
    assert storage.exists(key)
    assert resolve_datasheet(storage, "CH340E")
    assert proj_svc.library_has_datasheet(storage, "CH340E")


def test_library_catalog_lists_ics_passives_and_pdfs(storage):
    storage.write_json(
        "library/extracted/CH340E.json",
        {
            "mpn": "CH340E",
            "pintable": [{"pin": 1}, {"pin": 2}],
            "component_subtype": "usb-uart",
            "absolute_maximum_ratings": {"vcc": "5V"},
        },
    )
    storage.write_json(
        "library/patterns/samsung_c.json",
        {
            "name": "Samsung CL10",
            "component_type": "capacitor",
            "description": "Samsung 0603 MLCC",
            "regex": r"^CL10",
        },
    )
    storage.write_json(
        "library/passives/CL10B474KA8NNNC.json",
        {
            "mpn": "CL10B474KA8NNNC",
            "specs": {
                "specs_type": "capacitor",
                "component_subtype": "mlcc",
                "values": {"capacitance": "470n"},
            },
        },
    )
    proj_svc.remember_datasheet(storage, "CH340E", PDF)

    cat = proj_svc.list_library_catalog(storage)
    assert len(cat["ics"]) == 1
    assert cat["ics"][0]["mpn"] == "CH340E"
    assert cat["ics"][0]["pin_count"] == 2
    assert cat["ics"][0]["has_datasheet"] is True
    assert cat["passives"][0]["mpn"] == "Samsung CL10"
    assert cat["passive_parts"][0]["mpn"] == "CL10B474KA8NNNC"
    assert cat["passive_parts"][0]["param_count"] >= 1
    assert cat["simple"] == []
    assert any(d["mpn"] == "CH340E" and d["has_extraction"] for d in cat["datasheets"])


def test_library_http_catalog_and_pdf(tmp_path):
    client = _client(tmp_path)
    empty = client.get("/api/library")
    assert empty.status_code == 200
    body = empty.json()
    assert body["ics"] == []
    assert body["datasheets"] == []

    meta = client.post("/api/projects", json={"name": "lib"}).json()
    resp = client.post(
        f"/api/projects/{meta['id']}/upload/datasheets",
        params={"mpn": "MSPM0G3507"},
        files={"file": ("msp.pdf", PDF, "application/pdf")},
    )
    assert resp.status_code == 200

    catalog = client.get("/api/library").json()
    assert any(d["mpn"] == "MSPM0G3507" for d in catalog["datasheets"])

    pdf = client.get("/api/library/datasheet/MSPM0G3507")
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-")


def test_fetch_datasheet_persists_to_library(tmp_path, monkeypatch):
    from backend.services.datasheet_finder import DatasheetHit

    async def fake_find(mpn, lcsc_id=None):
        return DatasheetHit(
            mpn,
            pdf_bytes=PDF,
            url="https://datasheet.lcsc.com/x.pdf",
            source="lcsc",
        )

    monkeypatch.setattr(
        "backend.services.datasheet_finder.find_datasheet", fake_find,
    )
    client = _client(tmp_path)
    resp = client.get("/api/datasheets/fetch", params={"mpn": "CH340E"})
    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF-")

    catalog = client.get("/api/library").json()
    assert any(d["mpn"] == "CH340E" for d in catalog["datasheets"])
    assert client.get("/api/library/datasheet/CH340E").status_code == 200
