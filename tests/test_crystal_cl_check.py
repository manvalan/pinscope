"""Crystal CL check — only fires when CL and cap values are known."""

from __future__ import annotations

from backend.pinscopex.crystal_cl_check import check_crystal_cl
from backend.pinscopex.models import (
    Component,
    ComponentType,
    DesignGraph,
    Net,
    NetType,
    PinConnection,
    SimpleComponentSpecs,
)


def _xtal_graph(*, cl_f: float | None, c1: str, c2: str, stray: float | None = None) -> DesignGraph:
    values: dict = {}
    if cl_f is not None:
        values["load_capacitance_f"] = cl_f
    if stray is not None:
        values["stray_capacitance_f"] = stray
    return DesignGraph(
        components={
            "X1": Component(
                reference="X1",
                value="8MHz",
                footprint="",
                component_type=ComponentType.CRYSTAL,
                mpn="XTAL",
                pins={"1": "XIN", "2": "XOUT", "3": "GND"},
                specs=SimpleComponentSpecs(specs_type="crystal", values=values) if values else None,
            ),
            "C1": Component(
                reference="C1", value=c1, footprint="",
                component_type=ComponentType.CAPACITOR,
                pins={"1": "XIN", "2": "GND"},
            ),
            "C2": Component(
                reference="C2", value=c2, footprint="",
                component_type=ComponentType.CAPACITOR,
                pins={"1": "XOUT", "2": "GND"},
            ),
        },
        nets={
            "XIN": Net(
                name="XIN", net_type=NetType.SIGNAL,
                pins=[
                    PinConnection(component_ref="X1", pin_number="1"),
                    PinConnection(component_ref="C1", pin_number="1"),
                ],
            ),
            "XOUT": Net(
                name="XOUT", net_type=NetType.SIGNAL,
                pins=[
                    PinConnection(component_ref="X1", pin_number="2"),
                    PinConnection(component_ref="C2", pin_number="1"),
                ],
            ),
            "GND": Net(
                name="GND", net_type=NetType.GROUND,
                pins=[
                    PinConnection(component_ref="X1", pin_number="3"),
                    PinConnection(component_ref="C1", pin_number="2"),
                    PinConnection(component_ref="C2", pin_number="2"),
                ],
            ),
        },
    )


def test_no_cl_in_specs_is_silent():
    g = _xtal_graph(cl_f=None, c1="18p", c2="18p")
    assert check_crystal_cl(g) == []


def test_series_above_cl_without_stray_warns():
    # series of 22p || 22p = 11p — wait we need series > CL.
    # 100p || 100p = 50p > CL 18p * 1.25
    g = _xtal_graph(cl_f=18e-12, c1="100p", c2="100p")
    findings = check_crystal_cl(g)
    assert any(f.rule_id == "PS-XTAL-002" for f in findings)


def test_with_stray_mismatch_warns():
    # 18p||18p = 9p + 2p stray = 11p vs CL 18p → below 0.75*18
    g = _xtal_graph(cl_f=18e-12, c1="18p", c2="18p", stray=2e-12)
    findings = check_crystal_cl(g)
    assert any(f.rule_id == "PS-XTAL-002" for f in findings)


def test_simple_project_without_cl_silent():
    from pathlib import Path
    from backend.pinscopex.models import DesignGraph
    path = Path(__file__).resolve().parents[1] / "simple_project" / "design_graph.json"
    g = DesignGraph.model_validate_json(path.read_text())
    assert check_crystal_cl(g) == []
