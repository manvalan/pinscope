"""ImpedenceFinder analysis on specified nets — widths/stackup from the board.

Favor: named net Z0 matches zsolver on the same w/h/εr/t.
Against: empty net list; missing net; no stackup.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.pinscopex.impedance import GeometryError
from backend.pinscopex.impedance_traces import (
    analyze_specified_nets,
    analyze_where_needed,
)
from backend.pinscopex.models import (
    DesignGraph,
    LayoutDielectric,
    LayoutGraph,
    LayoutSegment,
    LayoutStackup,
    LayoutZone,
    Net,
    NetType,
)
from backend.pinscopex.parsers_kicad_pcb import parse_kicad_pcb
from backend.vendor_path import ensure_impedancefinder

ensure_impedancefinder()
from impedancefinder import zsolver

_H = 0.15
_ER = 4.3
_T = 0.035
_W = 0.2
_PITCH = 2.0


def _layout() -> LayoutGraph:
    return LayoutGraph(
        segments=[
            LayoutSegment(
                start=(0.0, 0.0), end=(10.0, 0.0),
                width=_W, layer="F.Cu", net="SIG",
            ),
            LayoutSegment(
                start=(0.0, 2.0), end=(4.0, 2.0),
                width=0.3, layer="F.Cu", net="OTHER",
            ),
        ],
        stackup=LayoutStackup(
            copper_layers=["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"],
            dielectrics=[
                LayoutDielectric(name="prepreg_top", er=_ER, height_mm=_H),
                LayoutDielectric(name="core", er=4.4, height_mm=0.7),
                LayoutDielectric(name="prepreg_bottom", er=4.3, height_mm=0.15),
            ],
            copper_thickness_mm=_T,
        ),
        zones=[
            LayoutZone(
                net="GND",
                layer="In1.Cu",
                outlines=[[(-5.0, -5.0), (50.0, -5.0), (50.0, 5.0), (-5.0, 5.0)]],
            ),
        ],
    )


def test_specified_net_z0_matches_impedancefinder_zsolver():
    rows = analyze_specified_nets(_layout(), ["SIG"], _PITCH)
    assert len(rows) == 1
    row = rows[0]
    assert row["net_name"] == "SIG"
    expect = zsolver.microstrip_z0(_W, _H, _ER, _T)
    assert row["z0_avg_ohms"] == pytest.approx(expect, rel=1e-6)
    assert row["z0_min_ohms"] == pytest.approx(expect, rel=1e-6)


def test_only_specified_nets_are_analyzed():
    rows = analyze_specified_nets(_layout(), ["SIG"], _PITCH)
    assert [r["net_name"] for r in rows] == ["SIG"]


def test_missing_net_is_error_not_invented_z0():
    rows = analyze_specified_nets(_layout(), ["NO_SUCH_NET"], _PITCH)
    assert rows[0]["error"] == "no segments on this net"
    assert "z0_avg_ohms" not in rows[0] or rows[0].get("z0_avg_ohms") is None


def test_empty_net_list_is_silent():
    assert analyze_specified_nets(_layout(), ["  ", ""], _PITCH) == []


def test_no_stackup_raises():
    layout = LayoutGraph(
        segments=[LayoutSegment(start=(0, 0), end=(1, 0), width=0.2, layer="F.Cu", net="SIG")],
    )
    with pytest.raises(GeometryError, match="stackup"):
        analyze_specified_nets(layout, ["SIG"], _PITCH)


_PCB_STACKUP = """(kicad_pcb (version 20240108) (generator pcbnew)
  (net 0 "")
  (net 1 "GND")
  (net 2 "SIG")
  (setup
    (stackup
      (layer "F.Cu" (type copper) (thickness 0.035))
      (layer "dielectric 1" (type core) (thickness 0.15) (epsilon_r 4.3))
      (layer "In1.Cu" (type copper) (thickness 0.035))
      (layer "dielectric 2" (type core) (thickness 0.7) (epsilon_r 4.4))
      (layer "In2.Cu" (type copper) (thickness 0.035))
      (layer "dielectric 3" (type prepreg) (thickness 0.15) (epsilon_r 4.3))
      (layer "B.Cu" (type copper) (thickness 0.035))
    )
  )
  (segment (start 0 0) (end 10 0) (width 0.2) (layer "F.Cu") (net 2))
  (zone (net 1) (net_name "GND") (layer "In1.Cu")
    (filled_polygon (layer "In1.Cu")
      (pts (xy -5 -5) (xy 50 -5) (xy 50 5) (xy -5 5))
    )
  )
)
"""


def test_parse_stackup_and_zone_then_analyze_sig(tmp_path: Path):
    p = tmp_path / "board.kicad_pcb"
    p.write_text(_PCB_STACKUP)
    layout = parse_kicad_pcb(p)
    assert layout.stackup is not None
    assert layout.stackup.copper_layers[0] == "F.Cu"
    assert layout.stackup.dielectrics[0].er == 4.3
    assert layout.zones and layout.zones[0].net == "GND"
    rows = analyze_specified_nets(layout, ["SIG"], _PITCH)
    expect = zsolver.microstrip_z0(_W, _H, _ER, _T)
    assert rows[0]["z0_avg_ohms"] == pytest.approx(expect, rel=1e-6)


def test_project_analysis_skips_power_and_runs_signal():
    graph = DesignGraph(nets={
        "SIG": Net(name="SIG", net_type=NetType.SIGNAL),
        "OTHER": Net(name="OTHER", net_type=NetType.POWER),
    })
    report = analyze_where_needed(_layout(), graph, pitch_mm=_PITCH)
    assert report["skipped"] is None
    names = [r["net_name"] for r in report["nets"]]
    assert names == ["SIG"]
    expect = zsolver.microstrip_z0(_W, _H, _ER, _T)
    assert report["nets"][0]["z0_avg_ohms"] == pytest.approx(expect, rel=1e-6)


def test_project_analysis_skips_without_stackup():
    layout = LayoutGraph(
        segments=[LayoutSegment(start=(0, 0), end=(1, 0), width=0.2, layer="F.Cu", net="SIG")],
    )
    report = analyze_where_needed(layout, None, pitch_mm=_PITCH)
    assert report["nets"] == []
    assert report["skipped"] == "no stackup"


def test_pipeline_writes_impedance_nets_for_signal(tmp_path: Path):
    import json
    from backend.services.pipeline import _write_impedance_nets

    class Ws:
        def local_path(self, rel: str) -> Path:
            return tmp_path / rel

    layout = _layout()
    (tmp_path / "layout_graph.json").write_text(layout.model_dump_json())
    graph = DesignGraph(nets={
        "SIG": Net(name="SIG", net_type=NetType.SIGNAL),
        "OTHER": Net(name="OTHER", net_type=NetType.POWER),
    })
    _write_impedance_nets(Ws(), graph)
    data = json.loads((tmp_path / "impedance_nets.json").read_text())
    assert [r["net_name"] for r in data["nets"]] == ["SIG"]


def test_get_impedance_nets_without_run_is_empty(tmp_path: Path):
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.services.storage import LocalStorageBackend

    app.state.storage = LocalStorageBackend(tmp_path)
    client = TestClient(app)
    pid = client.post("/api/projects", json={"name": "z0"}).json()["id"]
    body = client.get(f"/api/projects/{pid}/impedance/nets").json()
    assert body["nets"] == []
    assert body["skipped"] == "not run"
