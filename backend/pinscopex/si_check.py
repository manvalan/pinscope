"""G1 SI: intra-pair skew only when the datasheet gives millimetres.

Pair names (_DP/_DM, _P/_N) only identify which nets to compare. The
limit is never 3W, USB spec folklore, or a default millimetre.
"""

from __future__ import annotations

import math

from backend.pinscopex.models import DesignGraph, Finding, LayoutGraph, LayoutSegment
from backend.pinscopex.validate import _match_constraints

_PAIR_SUFFIXES = (("_DP", "_DM"), ("_P", "_N"), ("+", "-"))


def _seg_len(seg: LayoutSegment) -> float:
    return math.hypot(seg.end[0] - seg.start[0], seg.end[1] - seg.start[1])


def net_length_mm(layout: LayoutGraph, net: str) -> float:
    return sum(_seg_len(s) for s in layout.segments if s.net == net)


def partner_net(name: str) -> str | None:
    for a, b in _PAIR_SUFFIXES:
        if name.endswith(a):
            return name[: -len(a)] + b
        if name.endswith(b):
            return name[: -len(b)] + a
    return None


def _length_match_limit_mm(constraints_map: dict, graph: DesignGraph) -> tuple[float, int | None] | None:
    for comp in graph.components.values():
        cons = _match_constraints(comp.mpn, constraints_map)
        if not cons:
            continue
        for rule in cons.layout_rules or []:
            if rule.get("kind") != "length_match":
                continue
            mm = rule.get("max_distance_mm")
            if mm is None:
                continue
            return float(mm), rule.get("source_page")
    return None


def check_si(
    graph: DesignGraph,
    constraints_map: dict,
    layout: LayoutGraph | None,
) -> list[Finding]:
    if layout is None or not layout.segments:
        return []
    limit = _length_match_limit_mm(constraints_map, graph)
    if limit is None:
        return []
    max_mm, page = limit
    seen: set[tuple[str, str]] = set()
    findings: list[Finding] = []
    names = {s.net for s in layout.segments if s.net}
    for net in names:
        partner = partner_net(net)
        if not partner or partner not in names:
            continue
        key = tuple(sorted((net, partner)))
        if key in seen:
            continue
        seen.add(key)
        skew = abs(net_length_mm(layout, net) - net_length_mm(layout, partner))
        if skew <= max_mm:
            continue
        findings.append(Finding(
            designator="layout",
            mpn="",
            aspect="si",
            finding=(
                f"Intra-pair skew {skew:.1f} mm on {key[0]}/{key[1]} "
                f"(datasheet max {max_mm:g} mm)."
            ),
            why=f"length_match max_distance_mm={max_mm:g}.",
            status="ERROR",
            recommendation="Length-match the differential pair.",
            source="si_check",
            rule_id="PS-SI-001",
            net=net,
            pins=[],
            source_page=page,
        ))
    return findings
