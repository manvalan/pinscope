"""G2 placement vs simple_project — no invented millimetre boards.

Favor: real U1 + caps on +3V3 exist; eval stays 3 keys.
Against: no .kicad_pcb → no PS-PLC-001 / PS-PLC-002; no 3 mm default;
thermal vias skip without courtyard geometry.
"""

from __future__ import annotations

from pathlib import Path

from backend.pinscopex.eval_report import eval_simple_project
from backend.pinscopex.models import DesignGraph
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
