"""Crystal load capacitance vs load caps — numbers only when present.

CL_eff ≈ (C1·C2)/(C1+C2) + Cstray. Cstray used only if specs list it;
never invent a stray default. Without CL in specs → skip.
"""

from __future__ import annotations

from backend.pinscopex.functional_groups import (
    _cap_farads,
    _is_ground_net,
    load_capacitance_farads,
)
from backend.pinscopex.models import (
    Component,
    ComponentType,
    DesignGraph,
    Finding,
    SimpleComponentSpecs,
)

_STRAY_KEYS = ("stray_capacitance_f", "board_stray_f", "cstray_f")


def check_crystal_cl(graph: DesignGraph) -> list[Finding]:
    findings: list[Finding] = []
    for ref, comp in sorted(graph.components.items()):
        if comp.component_type != ComponentType.CRYSTAL:
            continue
        cl = load_capacitance_farads(comp)
        if cl is None:
            continue
        load_caps = _load_caps_for_crystal(graph, comp)
        if len(load_caps) < 2:
            findings.append(Finding(
                designator=ref,
                mpn=comp.mpn or "",
                aspect="clock",
                source="crystal_cl_check",
                status="WARNING",
                finding=(
                    f"{ref} specifies CL={_fmt_f(cl)} but fewer than two load "
                    f"capacitors were found on its non-ground nets "
                    f"({[c.reference for c in load_caps] or 'none'})."
                ),
                why="Crystal load capacitance needs a matched C1/C2 pair.",
                recommendation="Add or value the two load capacitors on XIN/XOUT.",
                reference="netlist topology",
                rule_id="PS-XTAL-001",
                pins=[ref],
            ))
            continue

        # Use the two caps with known farads closest to equal (typical C1≈C2).
        valued = [(c, _cap_farads(c)) for c in load_caps]
        known = [(c, f) for c, f in valued if f is not None]
        if len(known) < 2:
            continue
        known.sort(key=lambda x: x[1])
        # Prefer a pair with similar values: take the two largest known if many.
        c1, f1 = known[-2]
        c2, f2 = known[-1]
        series = (f1 * f2) / (f1 + f2) if (f1 + f2) > 0 else None
        if series is None:
            continue
        stray = _stray_farads(comp)
        c_eff = series + (stray or 0.0)

        if stray is None:
            # Without stray: only flag when series alone already exceeds CL.
            if series > cl * 1.25:
                findings.append(Finding(
                    designator=ref,
                    mpn=comp.mpn or "",
                    aspect="clock",
                    source="crystal_cl_check",
                    status="WARNING",
                    finding=(
                        f"{ref} CL={_fmt_f(cl)}; C1={c1.reference} {_fmt_f(f1)} and "
                        f"C2={c2.reference} {_fmt_f(f2)} give series≈{_fmt_f(series)} "
                        f"(already above CL; board stray not in specs)."
                    ),
                    why="Series combination of load caps exceeds specified CL without needing stray.",
                    recommendation="Reduce load caps or confirm the datasheet CL value.",
                    reference="netlist topology",
                    rule_id="PS-XTAL-002",
                    pins=[ref, c1.reference, c2.reference],
                ))
            elif series < cl * 0.5:
                findings.append(Finding(
                    designator=ref,
                    mpn=comp.mpn or "",
                    aspect="clock",
                    source="crystal_cl_check",
                    status="INFO",
                    finding=(
                        f"{ref} CL={_fmt_f(cl)}; series of {c1.reference}/{c2.reference} "
                        f"≈{_fmt_f(series)} (stray unknown — verify against datasheet)."
                    ),
                    why="Without stray capacitance in specs, effective CL cannot be fully checked.",
                    recommendation="Confirm Cstray or populate load_capacitance / stray in crystal specs.",
                    reference="netlist topology",
                    rule_id="PS-XTAL-003",
                    pins=[ref, c1.reference, c2.reference],
                ))
            continue

        if c_eff > cl * 1.25 or c_eff < cl * 0.75:
            findings.append(Finding(
                designator=ref,
                mpn=comp.mpn or "",
                aspect="clock",
                source="crystal_cl_check",
                status="WARNING",
                finding=(
                    f"{ref} CL={_fmt_f(cl)}; C_eff≈{_fmt_f(c_eff)} "
                    f"(series {_fmt_f(series)} + stray {_fmt_f(stray)}) "
                    f"from {c1.reference}/{c2.reference}."
                ),
                why="Effective load capacitance should stay near the crystal's specified CL.",
                recommendation="Adjust C1/C2 so C_eff ≈ CL.",
                reference="netlist topology",
                rule_id="PS-XTAL-002",
                pins=[ref, c1.reference, c2.reference],
            ))
    return findings


def _load_caps_for_crystal(graph: DesignGraph, crystal: Component) -> list[Component]:
    caps: dict[str, Component] = {}
    for net in crystal.pins.values():
        if not net or _is_ground_net(graph, net):
            continue
        for cref in graph.capacitors_on_net(net):
            cap = graph.components.get(cref)
            if cap:
                caps[cref] = cap
    return list(caps.values())


def _stray_farads(comp: Component) -> float | None:
    specs = comp.specs
    if not isinstance(specs, SimpleComponentSpecs):
        return None
    for key in _STRAY_KEYS:
        raw = specs.values.get(key)
        if raw is None:
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if v >= 0:
            return v
    return None


def _fmt_f(farads: float) -> str:
    if farads >= 1e-6:
        return f"{farads * 1e6:.3g}µF"
    if farads >= 1e-9:
        return f"{farads * 1e9:.3g}nF"
    return f"{farads * 1e12:.3g}pF"
