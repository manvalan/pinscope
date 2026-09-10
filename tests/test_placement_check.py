"""G2 placement vs datasheet layout_rules — only with a LayoutGraph.

Favor: decoupling cap 15 mm from VDD pad with max_distance_mm=2 → PS-PLC-001;
null mm uses declared 3 mm default as WARNING.
Against: no PCB → silent; empty layout_rules skip; cap at 1 mm is ok;
no cap on the net does not invent a millimetre.
"""

from __future__ import annotations

from backend.pinscopex.models import (
    Component,
    ComponentConstraints,
    ComponentType,
    DesignGraph,
    LayoutFootprint,
    LayoutGraph,
    LayoutPad,
    Net,
    NetType,
    Pin,
    PinConnection,
)
from backend.pinscopex.placement_check import check_placement


def _graph():
    u = Component(
        reference="U1", value="", footprint="",
        component_type=ComponentType.IC, mpn="UTEST",
        pins={"1": "VDD", "2": "GND"},
    )
    c = Component(
        reference="C1", value="100n", footprint="",
        component_type=ComponentType.CAPACITOR, mpn="C",
        pins={"1": "+3V3", "2": "GND"},
    )
    return DesignGraph(
        components={"U1": u, "C1": c},
        nets={
            "+3V3": Net(name="+3V3", net_type=NetType.POWER, pins=[
                PinConnection(component_ref="U1", pin_number="1"),
                PinConnection(component_ref="C1", pin_number="1"),
            ]),
            "GND": Net(name="GND", net_type=NetType.GROUND, pins=[
                PinConnection(component_ref="U1", pin_number="2"),
                PinConnection(component_ref="C1", pin_number="2"),
            ]),
        },
    )


def _cons(max_mm: float | None):
    return {
        "UTEST": ComponentConstraints(
            mpn="UTEST",
            pintable=[Pin(number=1, name="VDD"), Pin(number=2, name="GND")],
            absolute_maximum_ratings=[], rules=[],
            layout_rules=[{
                "kind": "decoupling_proximity",
                "pin": "VDD",
                "max_distance_mm": max_mm,
                "source_page": 14,
            }],
        )
    }


def _layout(cap_x: float) -> LayoutGraph:
    return LayoutGraph(
        footprints={
            "U1": LayoutFootprint(
                reference="U1", x=0, y=0, layer="F.Cu",
                pads=[
                    LayoutPad(number="1", x=0.0, y=0.0, net="+3V3"),
                    LayoutPad(number="2", x=0.0, y=1.0, net="GND"),
                ],
            ),
            "C1": LayoutFootprint(
                reference="C1", x=cap_x, y=0, layer="F.Cu",
                pads=[
                    LayoutPad(number="1", x=cap_x, y=0.0, net="+3V3"),
                    LayoutPad(number="2", x=cap_x, y=0.5, net="GND"),
                ],
            ),
        }
    )


def test_cap_15mm_from_2mm_rule_is_ps_plc_001():
    findings = check_placement(_graph(), _cons(2.0), _layout(15.0))
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == "PS-PLC-001"
    assert f.source == "placement_check"
    assert f.status == "ERROR"
    assert f.net == "+3V3"
    assert "1" in (f.pins or [])
    assert f.source_page == 14


def test_cap_1mm_is_silent():
    assert check_placement(_graph(), _cons(2.0), _layout(1.0)) == []


def test_no_layout_graph_is_silent():
    assert check_placement(_graph(), _cons(2.0), None) == []


def test_empty_layout_rules_skip():
    cons = {
        "UTEST": ComponentConstraints(
            mpn="UTEST",
            pintable=[Pin(number=1, name="VDD"), Pin(number=2, name="GND")],
            absolute_maximum_ratings=[], rules=[],
            layout_rules=[],
        )
    }
    assert check_placement(_graph(), cons, _layout(15.0)) == []


def test_null_mm_uses_declared_3mm_default_as_warning():
    findings = check_placement(_graph(), _cons(None), _layout(10.0))
    assert len(findings) == 1
    assert findings[0].status == "WARNING"
    assert findings[0].rule_id == "PS-PLC-001"
    assert "3 mm" in findings[0].finding or "3 mm" in (findings[0].why or "")


def test_no_capacitor_does_not_invent_distance():
    g = _graph()
    g.components.pop("C1")
    g.nets["+3V3"].pins = [PinConnection(component_ref="U1", pin_number="1")]
    assert check_placement(g, _cons(2.0), _layout(15.0)) == []
