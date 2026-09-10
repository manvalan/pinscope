"""G1 SI vs simple_project — no invented millimetres or USBPHY boards.

Favor: real /USB.D+ and /USB.D- pair by suffix; eval stays 3 keys.
Against: no .kicad_pcb → no PS-SI-001; 3W is not invented.
"""

from __future__ import annotations

from pathlib import Path

from backend.pinscopex.eval_report import eval_simple_project
from backend.pinscopex.models import DesignGraph
from backend.pinscopex.si_check import check_si, partner_net

SIMPLE = Path(__file__).resolve().parents[1] / "simple_project"


def _graph() -> DesignGraph:
    return DesignGraph.model_validate_json(
        (SIMPLE / "design_graph.json").read_text()
    )


def test_simple_project_usb_dp_dm_are_a_named_pair():
    g = _graph()
    assert "/USB.D+" in g.nets
    assert "/USB.D-" in g.nets
    assert partner_net("/USB.D+") == "/USB.D-"
    assert partner_net("/USB.D-") == "/USB.D+"
    assert partner_net("/USBC.D+") == "/USBC.D-"


def test_simple_project_without_pcb_has_no_ps_si_001():
    findings = check_si(_graph(), {}, None)
    assert findings == []
    assert all(f.rule_id != "PS-3W-001" for f in findings)


def test_simple_project_eval_has_no_si_keys():
    scores = eval_simple_project(SIMPLE)
    assert scores.finding_count == 3
    assert scores.precision == 1.0
    assert scores.recall == 1.0
    assert not any(
        k.startswith("PS-SI-") or k.startswith("PS-3W-")
        for k in scores.extra_keys
    )
