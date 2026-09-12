"""Layout F1 functional_groups on simple_project — topology only, no mm."""

from __future__ import annotations

import json
from pathlib import Path

from backend.pinscopex.functional_groups import build_functional_groups
from backend.pinscopex.models import DesignGraph

SIMPLE = Path(__file__).resolve().parents[1] / "simple_project"


def _graph() -> DesignGraph:
    return DesignGraph.model_validate_json(
        (SIMPLE / "design_graph.json").read_text(encoding="utf-8"),
    )


def test_simple_project_has_mcu_ldo_bridge_groups():
    report = build_functional_groups(_graph())
    assert report.objective == "routing"
    refs = {g.ref for g in report.groups}
    assert {"U1", "U2", "U3"} <= refs
    # No millimetre fields on the report model dump
    raw = json.loads(report.model_dump_json())
    blob = json.dumps(raw)
    assert "max_distance_mm" not in blob or all(
        r.get("max_distance_mm") is None
        for g in raw["groups"]
        for r in g.get("layout_rules") or []
    )


def test_u3_owns_crystal_load_caps():
    report = build_functional_groups(_graph())
    u3 = next(g for g in report.groups if g.ref == "U3")
    sat = {s.ref: s.role_hint for s in u3.satellites}
    assert "X1" in sat
    assert sat["X1"] == "crystal"
    assert sat.get("C9") == "load_cap" or sat.get("C10") == "load_cap"
    assert "C9" in sat and "C10" in sat
    assert sat["C9"] == "load_cap"
    assert sat["C10"] == "load_cap"


def test_u1_has_decoupling_or_bulk_on_rails():
    report = build_functional_groups(_graph())
    u1 = next(g for g in report.groups if g.ref == "U1")
    roles = {s.role_hint for s in u1.satellites}
    assert roles & {"decoupling", "bulk"}


def test_domains_cover_all_ics():
    report = build_functional_groups(_graph())
    covered = {r for d in report.domains for r in d.ic_refs}
    assert covered == {"U1", "U2", "U3"}
    assert all(d.assemble_order for d in report.domains)


def test_build_placement_plan_alias():
    from backend.pinscopex.functional_groups import build_placement_plan
    report = build_placement_plan(_graph())
    assert report.objective == "routing"
    assert {g.ref for g in report.groups} >= {"U1", "U2", "U3"}

