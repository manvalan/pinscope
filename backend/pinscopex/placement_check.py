"""G2: decoupling proximity on the PCB vs datasheet layout_rules.

Runs only when a LayoutGraph is present and a decoupling_proximity rule
has a numeric max_distance_mm. Null millimetres skip — no 3 mm default.
Thermal vias (`PS-PLC-002`) skip without courtyard vertices and without
min_via_count — no invented pad radius. same_layer (`PS-PLC-003`) uses
the boolean parameter plus footprint layers from the PCB. Crystals use
the same decoupling_proximity rule. Track length is shortest path on
segments vs max_distance_mm — no invented “much larger than euclidean”.
"""

from __future__ import annotations

import heapq
import math

from backend.pinscopex.models import (
    ComponentConstraints,
    ComponentType,
    DesignGraph,
    Finding,
    LayoutGraph,
    LayoutPad,
)
from backend.pinscopex.validate import _match_constraints


def _pad_for(layout: LayoutGraph, ref: str, number: str) -> LayoutPad | None:
    fp = layout.footprints.get(ref)
    if not fp:
        return None
    for pad in fp.pads:
        if pad.number == str(number):
            return pad
    return None


def _pin_number(cons: ComponentConstraints, token: str) -> str | None:
    want = str(token).strip()
    if not want:
        return None
    for pin in cons.pintable:
        if str(pin.number) == want or (pin.name or "").upper() == want.upper():
            return str(pin.number)
    return None


def _dist(a: LayoutPad, b: LayoutPad) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _xy_key(x: float, y: float) -> tuple[float, float]:
    return (round(x, 3), round(y, 3))


def _path_mm(layout: LayoutGraph, net: str, a: LayoutPad, b: LayoutPad) -> float | None:
    segs = [s for s in layout.segments if s.net == net]
    if not segs:
        return None
    adj: dict[tuple[float, float], list[tuple[tuple[float, float], float]]] = {}
    for s in segs:
        p = _xy_key(s.start[0], s.start[1])
        q = _xy_key(s.end[0], s.end[1])
        length = math.hypot(s.end[0] - s.start[0], s.end[1] - s.start[1])
        adj.setdefault(p, []).append((q, length))
        adj.setdefault(q, []).append((p, length))
    src = _xy_key(a.x, a.y)
    dst = _xy_key(b.x, b.y)
    if src not in adj or dst not in adj:
        return None
    dist = {src: 0.0}
    heap: list[tuple[float, tuple[float, float]]] = [(0.0, src)]
    while heap:
        d, node = heapq.heappop(heap)
        if d > dist.get(node, math.inf):
            continue
        if node == dst:
            return d
        for nxt, w in adj.get(node, []):
            nd = d + w
            if nd < dist.get(nxt, math.inf):
                dist[nxt] = nd
                heapq.heappush(heap, (nd, nxt))
    return None


def _reach_mm(layout: LayoutGraph, net: str, a: LayoutPad, b: LayoutPad) -> float:
    path = _path_mm(layout, net, a, b)
    if path is None:
        return _dist(a, b)
    return path


def _net_for_pin(graph: DesignGraph, ref: str, pin_no: str) -> str | None:
    for net in graph.nets.values():
        for pc in net.pins:
            if pc.component_ref == ref and str(pc.pin_number) == str(pin_no):
                return net.name
    return graph.pin_net(ref, pin_no)


def check_placement(
    graph: DesignGraph,
    constraints_map: dict,
    layout: LayoutGraph | None,
) -> list[Finding]:
    if layout is None or not layout.footprints:
        return []
    findings: list[Finding] = []
    for ref, comp in graph.components.items():
        if comp.component_type not in (ComponentType.IC, ComponentType.CRYSTAL):
            continue
        cons = _match_constraints(comp.mpn, constraints_map)
        if not cons or not cons.layout_rules:
            continue
        for rule in cons.layout_rules:
            kind = rule.get("kind")
            if kind == "decoupling_proximity":
                findings.extend(
                    _decoupling_finding(ref, comp, cons, rule, graph, layout)
                )
                findings.extend(
                    _same_layer_finding(ref, comp, cons, rule, graph, layout)
                )
            elif kind == "thermal_via":
                findings.extend(_thermal_via_finding(ref, comp, cons, rule, layout))
    return findings


def _decoupling_finding(ref, comp, cons, rule, graph: DesignGraph, layout: LayoutGraph) -> list[Finding]:
    pin_no = _pin_number(cons, str(rule.get("pin") or ""))
    if not pin_no:
        return []
    net = _net_for_pin(graph, ref, pin_no)
    if not net:
        return []
    ic_pad = _pad_for(layout, ref, pin_no)
    if not ic_pad:
        return []
    cap_pads: list[LayoutPad] = []
    for cref in graph.capacitors_on_net(net):
        fp = layout.footprints.get(cref)
        if not fp:
            continue
        for pad in fp.pads:
            if pad.net == net or pad.net == ic_pad.net:
                cap_pads.append(pad)
    if not cap_pads:
        return []
    nearest = min(_reach_mm(layout, net, ic_pad, p) for p in cap_pads)
    extracted = rule.get("max_distance_mm")
    if extracted is None:
        return []
    limit = float(extracted)
    if nearest <= limit:
        return []
    return [Finding(
        designator=ref,
        mpn=comp.mpn or cons.mpn,
        aspect="placement",
        finding=(
            f"Decoupling on {net} is {nearest:.1f} mm from {ref}.{pin_no} "
            f"(limit {limit:g} mm)."
        ),
        why=f"layout_rules max_distance_mm={limit:g}.",
        status="ERROR",
        recommendation="Place the decoupling capacitor closer to the supply pin.",
        source="placement_check",
        rule_id="PS-PLC-001",
        net=net,
        pins=[pin_no],
        source_page=rule.get("source_page"),
    )]


def _copper_side(layer: str) -> str | None:
    s = (layer or "").strip().upper()
    if s.startswith("F."):
        return "F"
    if s.startswith("B."):
        return "B"
    return None


def _same_layer_finding(ref, comp, cons, rule, graph: DesignGraph, layout: LayoutGraph) -> list[Finding]:
    if rule.get("same_layer") is not True:
        return []
    pin_no = _pin_number(cons, str(rule.get("pin") or ""))
    if not pin_no:
        return []
    net = _net_for_pin(graph, ref, pin_no)
    if not net:
        return []
    ic_fp = layout.footprints.get(ref)
    if not ic_fp:
        return []
    ic_side = _copper_side(ic_fp.layer)
    if ic_side is None:
        return []
    placed = []
    for cref in graph.capacitors_on_net(net):
        fp = layout.footprints.get(cref)
        if not fp:
            continue
        side = _copper_side(fp.layer)
        if side is None:
            continue
        placed.append((cref, side, fp))
    if not placed:
        return []
    if any(side == ic_side for _, side, _ in placed):
        return []
    if len(ic_fp.courtyard) >= 3:
        for v in layout.vias:
            if v.net and v.net != net:
                continue
            if _in_poly(v.x, v.y, ic_fp.courtyard):
                return []
    return [Finding(
        designator=ref,
        mpn=comp.mpn or cons.mpn,
        aspect="placement",
        finding=(
            f"Decoupling on {net} is on the opposite copper from {ref} "
            f"(same_layer=true)."
        ),
        why="layout_rules same_layer=true.",
        status="WARNING",
        recommendation="Place the decoupling capacitor on the same layer or add a via in the courtyard.",
        source="placement_check",
        rule_id="PS-PLC-003",
        net=net,
        pins=[pin_no],
        source_page=rule.get("source_page"),
    )]


def _in_poly(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _thermal_via_finding(ref, comp, cons, rule, layout: LayoutGraph) -> list[Finding]:
    min_n = rule.get("min_via_count")
    if min_n is None:
        return []
    fp = layout.footprints.get(ref)
    if not fp or len(fp.courtyard) < 3:
        return []
    n = sum(1 for v in layout.vias if _in_poly(v.x, v.y, fp.courtyard))
    if n >= int(min_n):
        return []
    pin = str(rule.get("pin") or "").strip()
    return [Finding(
        designator=ref,
        mpn=comp.mpn or cons.mpn,
        aspect="placement",
        finding=(
            f"{n} thermal vias in courtyard of {ref} "
            f"(min_via_count {int(min_n)})."
        ),
        why=f"layout_rules min_via_count={int(min_n)}.",
        status="ERROR",
        recommendation="Add vias in the thermal pad courtyard.",
        source="placement_check",
        rule_id="PS-PLC-002",
        pins=[pin] if pin else [],
        source_page=rule.get("source_page"),
    )]
