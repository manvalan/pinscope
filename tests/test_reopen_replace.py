"""Reopen a finished project and replace BOM/netlist without deleting it."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.services import projects as proj_svc
from backend.services.storage import LocalStorageBackend

_BOM_V1 = (
    b"Reference,Value,Manufacturer Part Number\n"
    b"U1,MCU,STM32F103C8T6\n"
)
_BOM_V2 = (
    b"Reference,Value,Manufacturer Part Number\n"
    b"U1,MCU,STM32F103C8T6\n"
    b"R1,10k,RC0603FR-0710KL\n"
)
_NETLIST = b"""*PADS-PCB*
*PART*
U1 LQFP48
*NET*
*SIGNAL* GND
U1.1
*END*
"""


def _client(tmp_path) -> TestClient:
    from backend.main import app

    app.state.storage = LocalStorageBackend(tmp_path)
    return TestClient(app)


def test_reopen_then_replace_bom_and_netlist(tmp_path):
    client = _client(tmp_path)
    meta = client.post("/api/projects", json={"name": "board"}).json()
    pid = meta["id"]

    resp = client.post(
        f"/api/projects/{pid}/upload/bom",
        files={"file": ("bom.csv", _BOM_V1, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    resp = client.post(
        f"/api/projects/{pid}/upload/netlist",
        files={"file": ("netlist.asc", _NETLIST, "text/plain")},
    )
    assert resp.status_code == 200, resp.text

    storage = client.app.state.storage
    proj_svc.update_project(
        storage, "local", pid,
        status="complete",
        total_cost_usd=1.23,
        summary={"ERROR": 1, "WARNING": 0, "INFO": 0, "total": 1},
    )
    prefix = f"users/local/projects/{pid}"
    storage.write_json(f"{prefix}/report.json", {"findings": []})
    storage.write_text(f"{prefix}/api_logs.jsonl", '{"cost_usd": 1.23}\n')

    reopen = client.post(f"/api/projects/{pid}/reopen")
    assert reopen.status_code == 200, reopen.text
    body = reopen.json()
    assert body["status"] == "draft"
    assert body["id"] == pid
    assert body["has_bom"] is True
    assert body["has_netlist"] is True
    assert body["total_cost_usd"] == 1.23
    assert not storage.exists(f"{prefix}/report.json")

    resp = client.post(
        f"/api/projects/{pid}/upload/bom",
        files={"file": ("bom.csv", _BOM_V2, "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["components"] == 2

    resp = client.post(
        f"/api/projects/{pid}/upload/netlist",
        files={"file": ("netlist.asc", _NETLIST, "text/plain")},
    )
    assert resp.status_code == 200, resp.text

    fresh = client.get(f"/api/projects/{pid}").json()
    assert fresh["id"] == pid
    assert fresh["status"] == "draft"
    assert fresh["has_bom"] is True
    assert fresh["has_netlist"] is True
    assert "RC0603FR-0710KL" in (fresh.get("component_mpns") or {}).get("passive", [])
