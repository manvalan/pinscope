"""G2: decoupling proximity on the PCB vs datasheet layout_rules.

Runs only when a LayoutGraph is present. Empty layout_rules skip the IC.
No capacitor on the rail does not invent a distance. A null
max_distance_mm uses the declared DEFAULT_MAX_DISTANCE_MM (3 mm) as
WARNING, never as an invented datasheet number.
"""

from __future__ import annotations

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

DEFAULT_MAX_DISTANCE_MM = 3.0


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
        if comp.component_type != ComponentType.IC:
            continue
        cons = _match_constraints(comp.mpn, constraints_map)
        if not cons or not cons.layout_rules:
            continue
        for rule in cons.layout_rules:
            if rule.get("kind") != "decoupling_proximity":
                continue
            pin_no = _pin_number(cons, str(rule.get("pin") or ""))
            if not pin_no:
                continue
            net = _net_for_pin(graph, ref, pin_no)
            if not net:
                continue
            ic_pad = _pad_for(layout, ref, pin_no)
            if not ic_pad:
                continue
            caps = graph.capacitors_on_net(net)
            cap_pads: list[LayoutPad] = []
            for cref in caps:
                fp = layout.footprints.get(cref)
                if not fp:
                    continue
                for pad in fp.pads:
                    if pad.net == net or pad.net == ic_pad.net:
                        cap_pads.append(pad)
            if not cap_pads:
                continue
            nearest = min(_dist(ic_pad, p) for p in cap_pads)
            extracted = rule.get("max_distance_mm")
            if extracted is None:
                limit = DEFAULT_MAX_DISTANCE_MM
                used_default = True
                status = "WARNING"
            else:
                limit = float(extracted)
                used_default = False
                status = "ERROR"
            if nearest <= limit:
                continue
            why = (
                f"Datasheet does not specify mm; used default {DEFAULT_MAX_DISTANCE_MM:g} mm."
                if used_default
                else f"Datasheet max_distance_mm={limit:g}."
            )
            findings.append(Finding(
                designator=ref,
                mpn=comp.mpn or cons.mpn,
                aspect="placement",
                finding=(
                    f"Decoupling on {net} is {nearest:.1f} mm from {ref}.{pin_no} "
                    f"(limit {limit:g} mm)."
                ),
                why=why,
                status=status,
                recommendation="Place the decoupling capacitor closer to the supply pin.",
                source="placement_check",
                rule_id="PS-PLC-001",
                net=net,
                pins=[pin_no],
                source_page=rule.get("source_page"),
            ))
    return findings
