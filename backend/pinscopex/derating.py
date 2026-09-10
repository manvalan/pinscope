"""Build a capacitor voltage derating table from the design graph. No AI — pure computation."""

from __future__ import annotations

import re

from backend.pinscopex.models import ComponentType, DesignGraph, NetType
from backend.pinscopex.resolve_passives import _format_value
from backend.pinscopex.utils import natural_sort_key

# Dielectric strings that indicate ceramic capacitors
_CERAMIC_DIELECTRICS = {"X7R", "X5R", "C0G", "NP0", "Y5V", "X7S", "X6S", "X8R", "C0G (NP0)"}

# Remaining C/C0 vs V/Vrated. Empirical stima, not a vendor lot curve.
_BIAS_CURVES: dict[str, list[tuple[float, float]]] = {
    "c0g": [(0.0, 1.0), (1.2, 1.0)],
    "x7r": [(0.0, 1.0), (0.25, 0.90), (0.50, 0.70), (0.75, 0.45), (1.0, 0.30), (1.2, 0.22)],
    "x5r": [(0.0, 1.0), (0.25, 0.82), (0.50, 0.55), (0.75, 0.32), (1.0, 0.18), (1.2, 0.12)],
    "y5v": [(0.0, 1.0), (0.25, 0.50), (0.50, 0.20), (0.80, 0.12), (1.0, 0.10)],
}


def _lerp(curve: list[tuple[float, float]], x: float) -> float:
    if x <= curve[0][0]:
        return curve[0][1]
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if x <= x1:
            if x1 == x0:
                return y1
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return curve[-1][1]


def _bias_family(dielectric: str | None) -> str | None:
    if not dielectric:
        return None
    u = dielectric.upper()
    if "C0G" in u or "NP0" in u or "NPO" in u:
        return "c0g"
    if "Y5V" in u:
        return "y5v"
    if "X5R" in u or "X6S" in u:
        return "x5r"
    if "X7R" in u or "X7S" in u or "X8R" in u:
        return "x7r"
    return None


def dc_bias_remaining(
    dielectric: str | None,
    v_op: float | None,
    rated_v: float | None,
) -> float | None:
    """Fraction of nominal C remaining under DC bias, or None if not modelled.

    Labelled a *stima*: class-2 MLCC curves vary by lot, thickness and vendor.
    """
    family = _bias_family(dielectric)
    if family is None or v_op is None or rated_v is None or rated_v <= 0:
        return None
    return _lerp(_BIAS_CURVES[family], max(0.0, v_op) / rated_v)


def _parse_voltage_rating(s: str | None) -> float | None:
    """Extract numeric voltage from a rating string like '16V', '25V', '2.5V'."""
    if not s:
        return None
    m = re.match(r"([\d.]+)", s)
    return float(m.group(1)) if m else None


def _dielectric_category(component_subtype: str | None, dielectric: str | None) -> str | None:
    """Map component subtype / dielectric to a derating category."""
    if component_subtype:
        low = component_subtype.lower()
        if "tantalum" in low:
            return "tantalum"
        if "electrolytic" in low:
            return "electrolytic"
        if "ceramic" in low:
            return "ceramic"

    if dielectric:
        upper = dielectric.upper().strip()
        if upper in _CERAMIC_DIELECTRICS or any(d in upper for d in _CERAMIC_DIELECTRICS):
            return "ceramic"
        low = dielectric.lower()
        if "tantalum" in low or low == "ta":
            return "tantalum"
        if "electrolytic" in low or low == "al":
            return "electrolytic"

    # Default to ceramic (most common)
    return "ceramic"


def build_derating_table(graph: DesignGraph) -> list[dict]:
    """Build a capacitor voltage derating table from the design graph.

    For each capacitor, determines:
      - Rated voltage (from specs)
      - Operating voltage (from connected net voltages)
      - Dielectric category (ceramic / tantalum / electrolytic)

    Returns a sorted list of dicts, one per capacitor designator.
    """
    rows: list[dict] = []

    for comp in graph.components.values():
        if comp.component_type != ComponentType.CAPACITOR:
            continue

        # Rated voltage from specs
        rated_v: float | None = None
        value_fmt: str | None = None
        dielectric: str | None = None
        c_nom: float | None = None
        if comp.specs and hasattr(comp.specs, "voltage_rating_v"):
            rated_v = _parse_voltage_rating(comp.specs.voltage_rating_v)
            value_fmt = getattr(comp.specs, "value_formatted", None)
            dielectric = getattr(comp.specs, "dielectric", None)
            c_nom = getattr(comp.specs, "value_farads", None)

        # Operating voltage: max non-zero voltage among connected nets
        op_voltage: float | None = None
        op_source: str | None = None
        for net_name in comp.pins.values():
            net = graph.nets.get(net_name)
            if net and net.voltage is not None and net.voltage > 0:
                if op_voltage is None or net.voltage > op_voltage:
                    op_voltage = net.voltage
                    op_source = net_name

        # Determine net+ (highest voltage) and net- (ground / lowest voltage).
        # Deduplicate net names (multi-pin caps may connect twice to same net).
        seen: set[str] = set()
        connected: list[tuple[str, float | None, NetType | None]] = []
        for net_name in comp.pins.values():
            if net_name in seen:
                continue
            seen.add(net_name)
            net = graph.nets.get(net_name)
            v = net.voltage if net else None
            nt = net.net_type if net else None
            connected.append((net_name, v, nt))

        net_plus: str | None = None
        net_minus: str | None = None
        if len(connected) == 1:
            # Single-net cap (both pins on same net) — show as net+
            net_plus = connected[0][0]
        elif len(connected) >= 2:
            # Sort: ground first, then ascending by voltage (None < any number)
            by_v = sorted(connected, key=lambda c: (
                c[2] != NetType.GROUND,  # ground nets first
                c[1] is not None,        # None before numbers
                c[1] or 0,               # ascending voltage
            ))
            net_minus = by_v[0][0]
            net_plus = by_v[-1][0]

        factor = dc_bias_remaining(dielectric, op_voltage, rated_v)
        c_eff = (c_nom * factor) if (c_nom is not None and factor is not None) else None
        c_eff_fmt = _format_value(c_eff, "F") if c_eff is not None else None

        rows.append({
            "designator": comp.reference,
            "mpn": comp.mpn,
            "value_formatted": value_fmt,
            "rated_voltage_v": rated_v,
            "operating_voltage_v": op_voltage,
            "operating_voltage_source": op_source,
            "net_plus": net_plus,
            "net_minus": net_minus,
            "dielectric_category": _dielectric_category(comp.component_subtype, dielectric),
            "dielectric": dielectric,
            "c_nominal_f": c_nom,
            "dc_bias_factor": factor,
            "c_eff_f": c_eff,
            "c_eff_formatted": c_eff_fmt,
            "dc_bias_model": "stima" if factor is not None else None,
        })

    rows.sort(key=lambda r: natural_sort_key(r["designator"]))
    return rows
