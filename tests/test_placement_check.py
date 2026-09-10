"""G2 placement vs simple_project — no invented millimetre boards.

Favor: real U1 + C4 on +3V3; same_layer True + opposite copper → PS-PLC-003.
Against: no PCB; same copper; same_layer unset; via in courtyard.
"""

from __future__ import annotations

from pathlib import Path

from backend.pinscopex.eval_report import eval_simple_project
from backend.pinscopex.models import (
    ComponentConstraints,
    DesignGraph,
    LayoutFootprint,
    LayoutGraph,
    LayoutPad,
    LayoutSegment,
    LayoutVia,
    Pin,
)
from backend.pinscopex.placement_check import _in_poly, check_placement

SIMPLE = Path(__file__).resolve().parents[1] / "simple_project"


def _graph() -> DesignGraph:
    return DesignGraph.model_validate_json(
        (SIMPLE / "design_graph.json").read_text()
    )


def test_simple_project_has_ldo_and_3v3_caps():
    g = _graph()
    assert "U1" in g.components
    assert g.components["U1"].mpn == "SPX3819M5-L-3-3/TR"
    caps = g.capacitors_on_net("+3V3")
    assert caps, "simple_project must keep decoupling caps on +3V3"


def test_simple_project_without_pcb_has_no_ps_plc():
    findings = check_placement(_graph(), {}, None)
    assert findings == []
    assert all(not (f.rule_id or "").startswith("PS-PLC-") for f in findings)


def test_simple_project_eval_has_no_placement_keys():
    scores = eval_simple_project(SIMPLE)
    assert scores.finding_count == 3
    assert scores.precision == 1.0
    assert scores.recall == 1.0
    assert not any(k.startswith("PS-PLC-") for k in scores.extra_keys)


def test_via_count_is_calculated_from_courtyard_and_min_parameter():
    courtyard = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    via_xy = [(0.5, 0.5), (10.0, 10.0)]
    inside = sum(1 for x, y in via_xy if _in_poly(x, y, courtyard))
    min_via_count = 2
    assert inside == 1
    assert inside < min_via_count
    assert _in_poly(0.5, 0.5, courtyard) is True
    assert _in_poly(10.0, 10.0, courtyard) is False


def _ldo_cons(*, same_layer: bool | None):
    mpn = "SPX3819M5-L-3-3/TR"
    rule: dict = {"kind": "decoupling_proximity", "pin": "5"}
    if same_layer is not None:
        rule["same_layer"] = same_layer
    return {
        mpn: ComponentConstraints(
            mpn=mpn,
            pintable=[Pin(number=5, name="+3V3")],
            absolute_maximum_ratings=[],
            rules=[],
            layout_rules=[rule],
        )
    }


def _u1_c4_layout(*, ic_layer: str, cap_layer: str, via_xy=None, courtyard=None):
    vias = []
    if via_xy is not None:
        vias = [LayoutVia(x=via_xy[0], y=via_xy[1], net="+3V3")]
    return LayoutGraph(
        footprints={
            "U1": LayoutFootprint(
                reference="U1", x=0, y=0, layer=ic_layer,
                pads=[LayoutPad(number="5", x=0.0, y=0.0, net="+3V3")],
                courtyard=list(courtyard or []),
            ),
            "C4": LayoutFootprint(
                reference="C4", x=0.5, y=0, layer=cap_layer,
                pads=[LayoutPad(number="1", x=0.5, y=0.0, net="+3V3")],
            ),
        },
        vias=vias,
    )


def test_same_layer_param_opposite_layers_is_ps_plc_003():
    findings = check_placement(
        _graph(),
        _ldo_cons(same_layer=True),
        _u1_c4_layout(ic_layer="F.Cu", cap_layer="B.Cu"),
    )
    plc = [f for f in findings if f.rule_id == "PS-PLC-003"]
    assert len(plc) == 1
    assert plc[0].status == "WARNING"
    assert plc[0].net == "+3V3"
    assert plc[0].designator == "U1"


def test_same_layer_param_same_copper_is_silent():
    assert check_placement(
        _graph(),
        _ldo_cons(same_layer=True),
        _u1_c4_layout(ic_layer="F.Cu", cap_layer="F.Cu"),
    ) == []


def test_opposite_layers_without_same_layer_param_is_silent():
    assert check_placement(
        _graph(),
        _ldo_cons(same_layer=None),
        _u1_c4_layout(ic_layer="F.Cu", cap_layer="B.Cu"),
    ) == []


def test_opposite_layers_with_via_in_courtyard_is_silent():
    courtyard = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    findings = check_placement(
        _graph(),
        _ldo_cons(same_layer=True),
        _u1_c4_layout(
            ic_layer="F.Cu",
            cap_layer="B.Cu",
            via_xy=(0.5, 0.5),
            courtyard=courtyard,
        ),
    )
    assert all(f.rule_id != "PS-PLC-003" for f in findings)


def test_simple_project_has_crystal_load_caps():
    g = _graph()
    assert g.components["X1"].mpn == "AV08000301"
    assert "C9" in g.capacitors_on_net("/HFXIN")
    assert "C10" in g.capacitors_on_net("/HFXOUT")


def _xtal_cons(*, max_distance_mm: float | None):
    mpn = "AV08000301"
    rule: dict = {"kind": "decoupling_proximity", "pin": "1"}
    if max_distance_mm is not None:
        rule["max_distance_mm"] = max_distance_mm
    return {
        mpn: ComponentConstraints(
            mpn=mpn,
            pintable=[Pin(number=1, name="/HFXIN")],
            absolute_maximum_ratings=[],
            rules=[],
            layout_rules=[rule],
        )
    }


def _x1_c9_layout(*, segments=None, cap_x: float = 0.5):
    return LayoutGraph(
        footprints={
            "X1": LayoutFootprint(
                reference="X1", x=0, y=0, layer="F.Cu",
                pads=[LayoutPad(number="1", x=0.0, y=0.0, net="/HFXIN")],
            ),
            "C9": LayoutFootprint(
                reference="C9", x=cap_x, y=0, layer="F.Cu",
                pads=[LayoutPad(number="1", x=cap_x, y=0.0, net="/HFXIN")],
            ),
        },
        segments=list(segments or []),
    )


def test_crystal_load_cap_beyond_max_distance_mm_is_ps_plc_001():
    limit = 2.0
    findings = check_placement(
        _graph(),
        _xtal_cons(max_distance_mm=limit),
        _x1_c9_layout(cap_x=10.0),
    )
    plc = [f for f in findings if f.rule_id == "PS-PLC-001"]
    assert len(plc) == 1
    assert plc[0].designator == "X1"
    assert plc[0].net == "/HFXIN"


def test_crystal_load_cap_within_max_distance_mm_is_silent():
    assert check_placement(
        _graph(),
        _xtal_cons(max_distance_mm=2.0),
        _x1_c9_layout(cap_x=0.5),
    ) == []


def test_track_path_longer_than_max_distance_mm_is_ps_plc_001():
    limit = 2.0
    segs = [
        LayoutSegment(start=(0.0, 0.0), end=(0.0, 10.0), width=0.2, layer="F.Cu", net="/HFXIN"),
        LayoutSegment(start=(0.0, 10.0), end=(1.0, 10.0), width=0.2, layer="F.Cu", net="/HFXIN"),
        LayoutSegment(start=(1.0, 10.0), end=(1.0, 0.0), width=0.2, layer="F.Cu", net="/HFXIN"),
    ]
    findings = check_placement(
        _graph(),
        _xtal_cons(max_distance_mm=limit),
        _x1_c9_layout(cap_x=1.0, segments=segs),
    )
    plc = [f for f in findings if f.rule_id == "PS-PLC-001"]
    assert len(plc) == 1
    assert plc[0].net == "/HFXIN"
