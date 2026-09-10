"""G2 placement vs simple_project — no invented 15 mm / 2 mm boards.

Favor: real U1 + caps on +3V3 exist; eval stays 3 keys.
Against: no .kicad_pcb → no PS-PLC-001; no 3 mm default.
"""

from __future__ import annotations

from pathlib import Path

from backend.pinscopex.eval_report import eval_simple_project
from backend.pinscopex.models import DesignGraph
from backend.pinscopex.placement_check import check_placement

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


def test_simple_project_without_pcb_has_no_ps_plc_001():
    findings = check_placement(_graph(), {}, None)
    assert findings == []
    assert all(f.rule_id != "PS-PLC-001" for f in findings)


def test_simple_project_eval_has_no_placement_keys():
    scores = eval_simple_project(SIMPLE)
    assert scores.finding_count == 3
    assert scores.precision == 1.0
    assert scores.recall == 1.0
    assert not any(k.startswith("PS-PLC-") for k in scores.extra_keys)
