"""Layout F2 placement_pack — gated; no invented millimetres."""

from __future__ import annotations

from pathlib import Path

from backend.periscopex.functional_groups import (
    PlacementIcGroup,
    PlacementSatellite,
    FunctionalGroupsReport,
    build_functional_groups,
)
from backend.periscopex.models import (
    DesignGraph,
    LayoutFootprint,
    LayoutGraph,
    LayoutPad,
)
from backend.periscopex.placement_pack import build_placement_pack

SIMPLE = Path(__file__).resolve().parents[1] / "simple_project"


def _graph() -> DesignGraph:
    return DesignGraph.model_validate_json(
        (SIMPLE / "design_graph.json").read_text(encoding="utf-8"),
    )


def test_simple_project_without_pcb_skips_pack():
    plan = build_functional_groups(_graph())
    pack = build_placement_pack(plan, None)
    assert pack.status == "skipped"
    assert pack.skip_reason == "no_pcb_footprints"
    assert pack.placements == []


def test_layout_without_numeric_rules_skips():
    plan = FunctionalGroupsReport(
        groups=[
            PlacementIcGroup(
                ref="U1",
                layout_rules=[{"kind": "decoupling_proximity", "pin": "5"}],
                satellites=[
                    PlacementSatellite(
                        ref="C4",
                        component_type="capacitor",
                        role_hint="decoupling",
                        nets=["+3V3"],
                    ),
                ],
            ),
        ],
    )
    layout = LayoutGraph(
        footprints={
            "U1": LayoutFootprint(
                reference="U1", x=0, y=0, layer="F.Cu",
                pads=[LayoutPad(number="5", x=0.0, y=0.0, net="+3V3")],
            ),
        },
    )
    pack = build_placement_pack(plan, layout)
    assert pack.status == "skipped"
    assert pack.skip_reason == "no_numeric_layout_rules"


def test_numeric_rule_proposes_satellite_within_limit():
    limit = 2.0
    plan = FunctionalGroupsReport(
        groups=[
            PlacementIcGroup(
                ref="U1",
                layout_rules=[{
                    "kind": "decoupling_proximity",
                    "pin": "5",
                    "max_distance_mm": limit,
                }],
                satellites=[
                    PlacementSatellite(
                        ref="C4",
                        component_type="capacitor",
                        role_hint="decoupling",
                        nets=["+3V3"],
                    ),
                ],
            ),
        ],
    )
    layout = LayoutGraph(
        footprints={
            "U1": LayoutFootprint(
                reference="U1", x=0, y=0, layer="F.Cu",
                pads=[LayoutPad(number="5", x=10.0, y=20.0, net="+3V3")],
            ),
        },
    )
    pack = build_placement_pack(plan, layout)
    assert pack.status == "packed"
    assert pack.skip_reason is None
    assert len(pack.placements) == 1
    p = pack.placements[0]
    assert p.ref == "C4"
    assert p.anchor_ref == "U1"
    assert p.max_distance_mm == limit
    assert p.layer == "F.Cu"
    dist = ((p.proposed_x - 10.0) ** 2 + (p.proposed_y - 20.0) ** 2) ** 0.5
    assert dist <= limit + 1e-6
    assert dist > 0
