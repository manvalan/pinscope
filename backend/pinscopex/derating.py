"""Build a capacitor voltage derating table from the design graph. No AI — pure computation."""

from __future__ import annotations

import re

from backend.pinscopex.models import ComponentType, DesignGraph, NetType
from backend.pinscopex.utils import natural_sort_key

# Dielectric strings that indicate ceramic capacitors
_CERAMIC_DIELECTRICS = {"X7R", "X5R", "C0G", "NP0", "Y5V", "X7S", "X6S", "X8R", "C0G (NP0)"}


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
        if comp.specs and hasattr(comp.specs, "voltage_rating_v"):
            rated_v = _parse_voltage_rating(comp.specs.voltage_rating_v)
            value_fmt = getattr(comp.specs, "value_formatted", None)
            dielectric = getattr(comp.specs, "dielectric", None)

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
        })

    rows.sort(key=lambda r: natural_sort_key(r["designator"]))
    return rows
