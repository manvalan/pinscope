"""Regulator current margin and explicit series-R IR drop.

Iout_max is the rating, never the load. IQ/load are summed only when every
IC on the rail has a spec. Trace resistance is never estimated.
"""

from __future__ import annotations

from backend.pinscopex.led_current_check import _net_voltage, _parse_resistance
from backend.pinscopex.models import (
    Component,
    ComponentConstraints,
    ComponentType,
    DesignGraph,
    Finding,
    InductorSpecs,
    ResistorSpecs,
)
from backend.pinscopex.passive_rail_check import _is_ground_net
from backend.pinscopex.thermal_check import (
    _IOUT_MAX_KEYS,
    _LOAD_KEYS,
    _VIN_PIN,
    _VOUT_PIN,
    _first,
    _is_ldo,
    _pin_net_by_role,
    _specs_values,
)
from backend.pinscopex.validate import _match_constraints

_IQ_KEYS = (
    "iq_a", "quiescent_current_a", "supply_current_a", "idd_a", "icc_a",
)
_IR_FRAC = 0.05  # 5% of the rail — wide, not a datasheet number


def _two_nets(comp: Component) -> tuple[str, str] | None:
    nets = list(dict.fromkeys(comp.pins.values()))
    if len(nets) != 2:
        return None
    return nets[0], nets[1]


def _series_ohms(comp: Component) -> float | None:
    if comp.component_type == ComponentType.RESISTOR:
        if isinstance(comp.specs, ResistorSpecs) and comp.specs.value_ohms >= 0:
            return float(comp.specs.value_ohms)
        return _parse_resistance(comp.value)
    if comp.component_type == ComponentType.INDUCTOR:
        if isinstance(comp.specs, InductorSpecs) and comp.specs.dcr_ohms is not None:
            return float(comp.specs.dcr_ohms)
    return None


def _expand_rail(graph: DesignGraph, start: str) -> set[str]:
    """Follow series R/L between nets; do not walk through ICs (VIN/VOUT)."""
    seen = {start}
    stack = [start]
    while stack:
        n = stack.pop()
        for ref in graph.components_on_net(n):
            c = graph.components.get(ref)
            if not c or c.component_type not in (
                ComponentType.RESISTOR, ComponentType.INDUCTOR,
            ):
                continue
            pair = _two_nets(c)
            if not pair:
                continue
            other = pair[1] if pair[0] == n else pair[0]
            if other in seen or _is_ground_net(graph, other):
                continue
            seen.add(other)
            stack.append(other)
    return seen


def _regulators(graph: DesignGraph, cmap: dict[str, ComponentConstraints]):
    for ref, comp in sorted(graph.components.items()):
        if comp.component_type != ComponentType.IC:
            continue
        cons = _match_constraints(comp.mpn or comp.value, cmap)
        vin = _pin_net_by_role(graph, comp, cons, _VIN_PIN)
        vout = _pin_net_by_role(graph, comp, cons, _VOUT_PIN)
        if not (vin and vout) and not _is_ldo(comp, cons):
            continue
        if not (vin and vout):
            continue
        yield ref, comp, cons, vin, vout


def check_power_margin(
    graph: DesignGraph,
    constraints_map: dict[str, ComponentConstraints] | None = None,
) -> list[Finding]:
    cmap = constraints_map or {}
    findings: list[Finding] = []
    for ref, comp, cons, vin, vout in _regulators(graph, cmap):
        values = _specs_values(comp)
        iout_max = _first(values, _IOUT_MAX_KEYS)
        i_load = _first(values, _LOAD_KEYS)
        ics: list[Component] = []
        missing_iq = False
        iq_sum = 0.0
        for net in _expand_rail(graph, vout):
            for r in graph.components_on_net(net):
                c = graph.components.get(r)
                if not c or c.component_type != ComponentType.IC or r == ref:
                    continue
                if c in ics:
                    continue
                ics.append(c)
                iq = _first(_specs_values(c), _IQ_KEYS)
                if iq is None:
                    missing_iq = True
                else:
                    iq_sum += iq
        i_total = None
        if i_load is not None and not missing_iq:
            i_total = i_load + iq_sum
        elif i_load is not None and not ics:
            i_total = i_load
        elif not missing_iq and ics and i_load is None:
            i_total = iq_sum
        if iout_max is not None and i_total is not None and i_total > iout_max:
            findings.append(Finding(
                designator=ref,
                mpn=comp.mpn or "",
                aspect="power",
                source="power_margin_check",
                status="WARNING",
                finding=(
                    f"{ref} load ≈ {i_total:.3g} A exceeds Iout_max {iout_max:.3g} A "
                    f"on '{vout}'."
                ),
                why="Sum of specified IQ on the rail plus I_load. Missing IQ was not guessed.",
                recommendation="Raise the regulator rating or cut the load.",
                reference="regulator Iout_max",
                net=vout,
                pins=[ref],
                rule_id="PS-PWR-001",
            ))

        # IR drop only through an explicit series R/ferrite on VIN or VOUT.
        if i_load is None:
            continue
        for r in graph.components_on_net(vin):
            c = graph.components.get(r)
            if not c or c.component_type not in (
                ComponentType.RESISTOR, ComponentType.INDUCTOR,
            ):
                continue
            pair = _two_nets(c)
            if not pair:
                continue
            ohms = _series_ohms(c)
            if ohms is None or ohms <= 0:
                continue
            drop = i_load * ohms
            vrail = _net_voltage(graph, vin) or _net_voltage(graph, vout)
            if vrail is None or vrail <= 0:
                continue
            if drop <= _IR_FRAC * vrail:
                continue
            findings.append(Finding(
                designator=r,
                mpn=c.mpn or "",
                aspect="power",
                source="power_margin_check",
                status="WARNING",
                finding=(
                    f"{r} series drop ≈ {drop:.3g} V at I_load={i_load:.3g} A "
                    f"into {ref} VIN '{vin}'."
                ),
                why="IR from an explicit series R/ferrite DCR. Trace resistance was not estimated.",
                recommendation="Lower DCR or the load, or accept the drop if it is intended.",
                reference="netlist series R",
                net=vin,
                pins=[r],
                rule_id="PS-PWR-001",
            ))
    return findings
