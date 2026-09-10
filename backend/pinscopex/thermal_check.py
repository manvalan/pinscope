"""Schematic thermal estimates for LDOs and dissipating resistors.

I_load is never inferred from Iout_max. θJA is never invented: missing
theta_ja after a known P is INFO only. Ta defaults to 25 °C.
"""

from __future__ import annotations

import re

from backend.pinscopex.led_current_check import (
    _leg_color,
    _net_voltage,
    _parse_resistance,
    _series_resistor,
    _vf,
)
from backend.pinscopex.models import (
    Component,
    ComponentConstraints,
    ComponentType,
    DesignGraph,
    Finding,
    ResistorSpecs,
)
from backend.pinscopex.passive_rail_check import _pin_name_tokens
from backend.pinscopex.resolve_passives import _parse_spice_value
from backend.pinscopex.validate import _match_constraints

_TA_C = 25.0
_TJ_WARN_C = 125.0
_LOAD_KEYS = (
    "i_load", "i_load_a", "load_current_a", "typical_load_a",
    "iout_typical_a", "typical_output_current_a",
)
_IOUT_MAX_KEYS = (
    "iout_max", "iout_max_a", "i_out_max", "max_output_current_a",
    "output_current_max_a",
)
_THETA_KEYS = ("theta_ja", "theta_ja_c_per_w", "thermal_resistance_ja", "rth_ja")
_VIN_PIN = re.compile(r"(?:^|[_/])(VIN|IN)(?:$|[_/\d])", re.I)
_VOUT_PIN = re.compile(r"(?:^|[_/])(VOUT|V_OUT|VO|OUT)(?:$|[_/\d])", re.I)
_NOT_OUT = re.compile(r"\b(EN|FB|NC|GND|PG)\b", re.I)


def _num(v: object) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    try:
        return _parse_spice_value(s)
    except ValueError:
        m = re.match(r"^[-+]?\d*\.?\d+", s)
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                return None
    return None


def _specs_values(comp: Component) -> dict:
    specs = comp.specs
    values = getattr(specs, "values", None) if specs else None
    return values if isinstance(values, dict) else {}


def _first(values: dict, keys: tuple[str, ...]) -> float | None:
    for k in keys:
        if k in values:
            n = _num(values[k])
            if n is not None:
                return n
    return None


def _power_rating_w(comp: Component) -> float | None:
    specs = comp.specs
    if isinstance(specs, ResistorSpecs) and specs.power_rating_w:
        raw = specs.power_rating_w
        s = str(raw).strip().upper().replace("W", "")
        if "/" in s:
            try:
                a, b = s.split("/", 1)
                return float(a) / float(b)
            except (TypeError, ValueError):
                pass
        return _num(raw) or _num(s)
    return None


def _is_ldo(comp: Component, cons: ComponentConstraints | None) -> bool:
    sub = (comp.component_subtype or "") + " " + ((cons.component_subtype if cons else "") or "")
    if "ldo" in sub.lower() or "linear_regulator" in sub.lower():
        return True
    return False


def _pin_net_by_role(
    graph: DesignGraph,
    comp: Component,
    cons: ComponentConstraints | None,
    role_re: re.Pattern,
) -> str | None:
    for pin_num, net in comp.pins.items():
        tokens = _pin_name_tokens(cons, pin_num) or [pin_num]
        if any(role_re.search(t) and not _NOT_OUT.search(t) for t in tokens):
            return net
        if role_re.search(net or ""):
            return net
    return None


def check_thermal(
    graph: DesignGraph,
    constraints_map: dict[str, ComponentConstraints] | None = None,
) -> list[Finding]:
    cmap = constraints_map or {}
    findings: list[Finding] = []
    findings.extend(_ldo_thermal(graph, cmap))
    findings.extend(_resistor_thermal(graph))
    return findings


def _ldo_thermal(
    graph: DesignGraph,
    cmap: dict[str, ComponentConstraints],
) -> list[Finding]:
    out: list[Finding] = []
    for ref, comp in sorted(graph.components.items()):
        if comp.component_type != ComponentType.IC:
            continue
        cons = _match_constraints(comp.mpn or comp.value, cmap)
        if not _is_ldo(comp, cons):
            # VIN+VOUT names still count as a regulator for this check.
            vin_n = _pin_net_by_role(graph, comp, cons, _VIN_PIN)
            vout_n = _pin_net_by_role(graph, comp, cons, _VOUT_PIN)
            if not (vin_n and vout_n):
                continue
        else:
            vin_n = _pin_net_by_role(graph, comp, cons, _VIN_PIN)
            vout_n = _pin_net_by_role(graph, comp, cons, _VOUT_PIN)
        values = _specs_values(comp)
        i_load = _first(values, _LOAD_KEYS)
        if i_load is None:
            # Explicitly ignore Iout_max — that is not a load.
            continue
        vin = _net_voltage(graph, vin_n) if vin_n else None
        vout = _net_voltage(graph, vout_n) if vout_n else None
        if vin is None or vout is None or vin <= vout:
            continue
        p = i_load * (vin - vout)
        theta = _first(values, _THETA_KEYS)
        net = vout_n or vin_n
        if theta is None:
            out.append(Finding(
                designator=ref,
                mpn=comp.mpn or "",
                aspect="thermal",
                source="thermal_check",
                status="INFO",
                finding=(
                    f"{ref} dissipation ≈ {p:.3g} W "
                    f"(I_load={i_load:.3g} A, Vin-Vout={vin - vout:.3g} V); "
                    f"manca theta_ja."
                ),
                why="θJA is not in the IC specs; Tj is not estimated.",
                recommendation="Add theta_ja (or θJA) from the datasheet package table.",
                reference="thermal estimate",
                net=net,
                pins=[ref],
                rule_id="PS-TH-001",
            ))
            continue
        tj = _TA_C + p * theta
        status = "WARNING" if tj >= _TJ_WARN_C else "INFO"
        rule = "PS-TH-002" if status == "WARNING" else "PS-TH-001"
        out.append(Finding(
            designator=ref,
            mpn=comp.mpn or "",
            aspect="thermal",
            source="thermal_check",
            status=status,
            finding=(
                f"{ref} Tj ≈ {tj:.0f} °C at Ta={_TA_C:.0f} °C "
                f"(P≈{p:.3g} W, θJA={theta:.3g} °C/W)."
            ),
            why="P = I_load × (Vin−Vout); Tj = Ta + P·θJA. Iout_max was not used as load.",
            recommendation="Lower I_load, drop, or θJA (better copper / package) if Tj is high.",
            reference="thermal estimate",
            net=net,
            pins=[ref],
            rule_id=rule,
        ))
    return out


def _resistor_thermal(graph: DesignGraph) -> list[Finding]:
    out: list[Finding] = []
    seen: set[str] = set()

    for ref in sorted(graph.components_by_subtype("discrete.led")):
        led = graph.components.get(ref)
        if not led or not led.specs:
            continue
        values = getattr(led.specs, "values", None) or {}
        for pid, net in led.pins.items():
            res = _series_resistor(graph, net, ref)
            if not res:
                continue
            rref, rval, far = res
            if rref in seen:
                continue
            rcomp = graph.components.get(rref)
            rating = _power_rating_w(rcomp) if rcomp else None
            if rating is None:
                continue
            color = _leg_color(pid, led)
            vf = _vf(values, color)
            vrail = _net_voltage(graph, far)
            if vrail is None:
                vrail = max(
                    (v for v in (_net_voltage(graph, n) for n in led.pins.values()) if v is not None),
                    default=None,
                )
            if vrail is None or vf is None or vrail <= vf or rval <= 0:
                continue
            i = (vrail - vf) / rval
            p = i * i * rval
            if p <= rating:
                continue
            seen.add(rref)
            out.append(Finding(
                designator=rref,
                mpn=(rcomp.mpn if rcomp else "") or "",
                aspect="thermal",
                source="thermal_check",
                status="WARNING",
                finding=(
                    f"{rref} dissipates ≈ {p:.3g} W on the LED path, "
                    f"above its {rating:.3g} W rating."
                ),
                why="P = I²R with I from (Vrail−Vf)/R. Rating comes from power_rating_w.",
                recommendation="Use a higher-wattage resistor or raise R to cut current.",
                reference="resistor power rating",
                net=net,
                pins=[rref],
                rule_id="PS-TH-003",
            ))

    for ref, comp in sorted(graph.components.items()):
        if ref in seen or comp.component_type != ComponentType.RESISTOR:
            continue
        rating = _power_rating_w(comp)
        ohms = None
        if isinstance(comp.specs, ResistorSpecs):
            ohms = float(comp.specs.value_ohms)
        if ohms is None:
            ohms = _parse_resistance(comp.value)
        if rating is None or ohms is None or ohms <= 0:
            continue
        nets = list(dict.fromkeys(comp.pins.values()))
        if len(nets) != 2:
            continue
        v1, v2 = _net_voltage(graph, nets[0]), _net_voltage(graph, nets[1])
        if v1 is None or v2 is None:
            continue
        dv = abs(v1 - v2)
        if dv <= 0:
            continue
        i = dv / ohms
        p = i * i * ohms
        if p <= rating:
            continue
        out.append(Finding(
            designator=ref,
            mpn=comp.mpn or "",
            aspect="thermal",
            source="thermal_check",
            status="WARNING",
            finding=(
                f"{ref} shunt dissipates ≈ {p:.3g} W "
                f"(ΔV={dv:.3g} V / {ohms:.3g} Ω), above its {rating:.3g} W rating."
            ),
            why="P = I²R with I = ΔV/R from known net voltages. No guessed current.",
            recommendation="Raise the wattage rating or the resistance.",
            reference="resistor power rating",
            net=nets[0],
            pins=[ref],
            rule_id="PS-TH-003",
        ))
    return out
