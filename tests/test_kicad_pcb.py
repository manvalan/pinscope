"""KiCad PCB ingest — parse only, no SI/creepage checks.

Favor: footprint+pad net; named nets; segment on a net.
Against: .kicad_sch rejected; missing Reference skipped; empty board ok.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.pinscopex.parsers_kicad_pcb import parse_kicad_pcb

_PCB = """(kicad_pcb (version 20240108) (generator pcbnew)
  (net 0 "")
  (net 1 "GND")
  (net 2 "+3V3")
  (footprint "Resistor_SMD:R_0603_1608Metric"
    (layer "F.Cu")
    (at 10 20 0)
    (property "Reference" "R1" (at 0 0 0) (effects (font (size 1 1))))
    (property "Value" "10k" (at 0 0 0) (effects (font (size 1 1))))
    (pad "1" smd roundrect (at -0.75 0) (size 0.8 0.9) (layers "F.Cu") (net 2 "+3V3"))
    (pad "2" smd roundrect (at 0.75 0) (size 0.8 0.9) (layers "F.Cu") (net 1 "GND"))
  )
  (footprint "Resistor_SMD:R_0603_1608Metric"
    (layer "F.Cu")
    (at 0 0 0)
    (property "Reference" "#PWR01" (at 0 0 0) (effects (font (size 1 1))))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "GND"))
  )
  (segment (start 10 20) (end 12 20) (width 0.25) (layer "F.Cu") (net 1))
  (via (at 11 20) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net 1))
)
"""


def test_parse_footprint_pads_and_nets(tmp_path: Path):
    p = tmp_path / "board.kicad_pcb"
    p.write_text(_PCB)
    g = parse_kicad_pcb(p)
    assert "GND" in g.nets and "+3V3" in g.nets
    assert "R1" in g.footprints
    r1 = g.footprints["R1"]
    assert r1.x == 10 and r1.y == 20
    by_num = {pad.number: pad for pad in r1.pads}
    assert by_num["1"].net == "+3V3"
    assert by_num["2"].net == "GND"
    assert by_num["1"].x == pytest.approx(9.25)
    assert by_num["2"].x == pytest.approx(10.75)
    assert r1.courtyard == []


def test_parse_segment_and_via_resolve_net_name(tmp_path: Path):
    p = tmp_path / "board.kicad_pcb"
    p.write_text(_PCB)
    g = parse_kicad_pcb(p)
    assert len(g.segments) == 1
    assert g.segments[0].net == "GND"
    assert len(g.vias) == 1
    assert g.vias[0].net == "GND"


def test_power_flag_footprint_is_skipped(tmp_path: Path):
    p = tmp_path / "board.kicad_pcb"
    p.write_text(_PCB)
    g = parse_kicad_pcb(p)
    assert "#PWR01" not in g.footprints


def test_kicad_sch_is_rejected_as_pcb(tmp_path: Path):
    p = tmp_path / "sheet.kicad_sch"
    p.write_text("(kicad_sch (version 20250114) (uuid \"1\"))\n")
    with pytest.raises(ValueError, match="kicad_pcb"):
        parse_kicad_pcb(p)


def test_empty_board_parses(tmp_path: Path):
    p = tmp_path / "empty.kicad_pcb"
    p.write_text("(kicad_pcb (version 20240108) (generator pcbnew))\n")
    g = parse_kicad_pcb(p)
    assert g.footprints == {}
    assert g.segments == []


def test_upload_pcb_sets_has_pcb(tmp_path: Path):
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.services.storage import LocalStorageBackend

    app.state.storage = LocalStorageBackend(tmp_path)
    client = TestClient(app)
    pid = client.post("/api/projects", json={"name": "board"}).json()["id"]
    resp = client.post(
        f"/api/projects/{pid}/upload/pcb",
        files={"file": ("board.kicad_pcb", _PCB.encode(), "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["footprints"] == 1
    assert body["nets"] >= 2
    fresh = client.get(f"/api/projects/{pid}").json()
    assert fresh["has_pcb"] is True
    assert fresh["has_netlist"] is False


def test_upload_sch_as_pcb_is_rejected(tmp_path: Path):
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.services.storage import LocalStorageBackend

    app.state.storage = LocalStorageBackend(tmp_path)
    client = TestClient(app)
    pid = client.post("/api/projects", json={"name": "board"}).json()["id"]
    resp = client.post(
        f"/api/projects/{pid}/upload/pcb",
        files={"file": ("sheet.kicad_sch", b"(kicad_sch (version 1))\n", "text/plain")},
    )
    assert resp.status_code == 400
    fresh = client.get(f"/api/projects/{pid}").json()
    assert not fresh.get("has_pcb")


class _FakeWs:
    def __init__(self, root: Path):
        self.root = root

    def local_path(self, rel: str) -> Path:
        return self.root / rel


def test_layout_graph_skipped_when_pcb_missing(tmp_path: Path):
    from backend.services.pipeline import _write_layout_graph

    (tmp_path / "uploads").mkdir()
    _write_layout_graph(_FakeWs(tmp_path), "p1")
    assert not (tmp_path / "layout_graph.json").is_file()


def test_layout_graph_fail_soft_on_invalid_pcb(tmp_path: Path):
    from backend.services.pipeline import _write_layout_graph

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "pcb.kicad_pcb").write_text("(kicad_sch (version 1))\n")
    _write_layout_graph(_FakeWs(tmp_path), "p1")
    assert not (tmp_path / "layout_graph.json").is_file()


def test_layout_graph_written_from_valid_pcb(tmp_path: Path):
    from backend.services.pipeline import _write_layout_graph

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "pcb.kicad_pcb").write_text(_PCB)
    _write_layout_graph(_FakeWs(tmp_path), "p1")
    out = tmp_path / "layout_graph.json"
    assert out.is_file()
    data = __import__("json").loads(out.read_text())
    assert "R1" in data["footprints"]
