"""HF decoupling coverage — bulk without a small ceramic.

Without a switching frequency this does not invent a Z(f) target.
INFO only: HF coverage depends on a ~100 nF close to the pin.
"""

from __future__ import annotations

from backend.pinscopex.models import ComponentType, DesignGraph, Finding, NetType
from backend.pinscopex.passive_rail_check import (
    _cap_farads,
    _is_ground_net,
    _is_ic_supply_pin,
    _is_nc_net,
    _is_regulator_output_pin,
    _pin_label,
)
from backend.pinscopex.validate import _match_constraints

_BULK_MIN_F = 1e-6
_HF_MAX_F = 1e-6
_HF_MIN_F = 1e-9


def _esl_hint(footprint: str) -> str:
    fp = (footprint or "").upper()
    if "0402" in fp:
        return "typical ESL ~0.4 nH (0402 stima)"
    if "0603" in fp:
        return "typical ESL ~0.6 nH (0603 stima)"
    if "0805" in fp:
        return "typical ESL ~0.8 nH (0805 stima)"
    return "ESL depends on package (stima)"


def _valued_gnd_caps(graph: DesignGraph, net_name: str) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    unknown = False
    for ref in graph.capacitors_on_net(net_name):
        cap = graph.components[ref]
        others = {n for n in cap.pins.values() if n != net_name}
        if not any(_is_ground_net(graph, n) for n in others):
            continue
        farads = _cap_farads(cap)
        if farads is None:
            unknown = True
            continue
        out.append((ref, farads))
    if unknown:
        return []
    return out


def check_hf_decoupling_coverage(
    graph: DesignGraph,
    constraints_map: dict,
) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[str] = set()
    for ref, comp in sorted(graph.components.items()):
        if comp.component_type != ComponentType.IC:
            continue
        cons = _match_constraints(comp.mpn or comp.value, constraints_map)
        for pin_num, net_name in sorted(comp.pins.items(), key=lambda x: str(x[0])):
            if net_name in seen or _is_nc_net(net_name):
                continue
            is_rail = _is_ic_supply_pin(graph, cons, pin_num, net_name) or (
                _is_regulator_output_pin(cons, pin_num)
            )
            if not is_rail:
                continue
            net = graph.nets.get(net_name)
            if net and net.net_type == NetType.GROUND:
                continue
            seen.add(net_name)
            caps = _valued_gnd_caps(graph, net_name)
            if not caps:
                continue
            has_bulk = any(c >= _BULK_MIN_F for _, c in caps)
            has_hf = any(_HF_MIN_F <= c < _HF_MAX_F for _, c in caps)
            if not (has_bulk and not has_hf):
                continue
            bulk_ref = next(r for r, c in caps if c >= _BULK_MIN_F)
            fp = graph.components[bulk_ref].footprint
            pin_label = _pin_label(cons, pin_num, net_name)
            findings.append(Finding(
                designator=ref,
                mpn=comp.mpn or "",
                aspect="decoupling",
                source="hf_coverage_check",
                status="INFO",
                finding=(
                    f"{ref} net '{net_name}' ({pin_label}) has bulk capacitance "
                    f"but no ~100 nF ceramic for HF."
                ),
                why=(
                    f"Parallel Z(f) of large C is inductive above a few hundred "
                    f"kHz ({_esl_hint(fp)}). Without f_sw this is not an Ω target."
                ),
                recommendation=(
                    f"Add a 10–100 nF ceramic from '{net_name}' to ground near "
                    f"{ref}, in parallel with the bulk cap."
                ),
                reference="netlist topology (stima)",
                net=net_name,
                pins=[f"{ref}.{pin_num}"],
                rule_id="PS-ESR-001",
            ))
    return findings
