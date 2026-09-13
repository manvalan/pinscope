"""Layout F2 skeleton — propose satellite xy from PCB anchors + numeric rules.

No millimetres are invented. Packing runs only when a LayoutGraph has
footprints and at least one ``decoupling_proximity`` rule carries a numeric
``max_distance_mm``. Otherwise the report is ``skipped`` with an explicit reason.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel

from backend.periscopex.functional_groups import FunctionalGroupsReport, PlacementIcGroup
from backend.periscopex.models import DesignGraph, LayoutGraph, LayoutPad

SkipReason = Literal[
    "no_pcb_footprints",
    "no_numeric_layout_rules",
    "no_packable_satellites",
]


class PlacementProposal(BaseModel):
    ref: str
    anchor_ref: str
    rule_kind: str
    max_distance_mm: float
    proposed_x: float
    proposed_y: float
    layer: str = ""
    basis: str = "ic_pad+rule"


class PlacementPackReport(BaseModel):
    objective: Literal["routing"] = "routing"
    status: Literal["packed", "skipped"] = "skipped"
    skip_reason: SkipReason | None = None
    placements: list[PlacementProposal] = []


def build_placement_pack(
    plan: FunctionalGroupsReport,
    layout: LayoutGraph | None,
    graph: DesignGraph | None = None,
) -> PlacementPackReport:
    """Propose satellite positions within extracted proximity limits."""
    if layout is None or not layout.footprints:
        return PlacementPackReport(status="skipped", skip_reason="no_pcb_footprints")

    if not _has_numeric_proximity(plan):
        return PlacementPackReport(
            status="skipped",
            skip_reason="no_numeric_layout_rules",
        )

    placements: list[PlacementProposal] = []
    used_refs: set[str] = set()

    for group in plan.groups:
        placements.extend(
            _pack_group(group, layout, graph, used_refs),
        )

    if not placements:
        return PlacementPackReport(
            status="skipped",
            skip_reason="no_packable_satellites",
        )
    return PlacementPackReport(status="packed", placements=placements)


def _has_numeric_proximity(plan: FunctionalGroupsReport) -> bool:
    for g in plan.groups:
        for rule in g.layout_rules:
            if rule.get("kind") != "decoupling_proximity":
                continue
            if _num(rule.get("max_distance_mm")) is not None:
                return True
    return False


def _num(raw) -> float | None:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _pack_group(
    group: PlacementIcGroup,
    layout: LayoutGraph,
    graph: DesignGraph | None,
    used_refs: set[str],
) -> list[PlacementProposal]:
    ic_fp = layout.footprints.get(group.ref)
    if not ic_fp:
        return []

    candidates = [
        s for s in group.satellites
        if s.role_hint in ("decoupling", "bulk") and s.ref not in used_refs
    ]
    if not candidates:
        return []

    out: list[PlacementProposal] = []
    for rule in group.layout_rules:
        if rule.get("kind") != "decoupling_proximity":
            continue
        limit = _num(rule.get("max_distance_mm"))
        if limit is None:
            continue
        pad = _anchor_pad(group, layout, graph, str(rule.get("pin") or ""))
        if pad is None:
            # Fall back to footprint origin when pin is unknown but rule is numeric.
            pad = LayoutPad(number="", x=ic_fp.x, y=ic_fp.y, net="")
            basis = "ic_origin+rule"
        else:
            basis = "ic_pad+rule"

        net = pad.net or None
        matched = [
            s for s in candidates
            if s.ref not in used_refs and (not net or net in (s.nets or []))
        ]
        if not matched:
            matched = [s for s in candidates if s.ref not in used_refs]
        if not matched:
            continue

        for i, sat in enumerate(matched):
            angle = (2.0 * math.pi * i) / max(len(matched), 8)
            radius = limit * 0.5
            px = pad.x + radius * math.cos(angle)
            py = pad.y + radius * math.sin(angle)
            layer = ic_fp.layer or ""
            out.append(PlacementProposal(
                ref=sat.ref,
                anchor_ref=group.ref,
                rule_kind="decoupling_proximity",
                max_distance_mm=limit,
                proposed_x=round(px, 4),
                proposed_y=round(py, 4),
                layer=layer,
                basis=basis,
            ))
            used_refs.add(sat.ref)
        # One numeric rule per IC is enough for the skeleton.
        break
    return out


def _anchor_pad(
    group: PlacementIcGroup,
    layout: LayoutGraph,
    graph: DesignGraph | None,
    pin_token: str,
) -> LayoutPad | None:
    fp = layout.footprints.get(group.ref)
    if not fp or not fp.pads:
        return None
    want = (pin_token or "").strip()
    if want:
        for pad in fp.pads:
            if pad.number == want:
                return pad
        if graph is not None:
            comp = graph.components.get(group.ref)
            if comp:
                for pin_num, net in comp.pins.items():
                    if str(pin_num) == want:
                        for pad in fp.pads:
                            if pad.number == str(pin_num):
                                return pad
                        # No matching pad number — pick any pad on that net.
                        for pad in fp.pads:
                            if pad.net and pad.net == net:
                                return pad
    # Prefer a pad on a power-looking net shared with decoupling sats.
    for pad in fp.pads:
        if pad.net:
            return pad
    return fp.pads[0]
