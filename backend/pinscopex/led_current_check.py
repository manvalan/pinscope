"""Deterministic LED forward-current check.

For each LED, compute the worst-case forward current per channel
``I = (V_rail - Vf) / R`` (0 V driver drop) and compare against the LED's
datasheet forward-current rating.  Over-current is a hard ERROR; ambiguous cases
(unknown rail, no rating, no resistor found, possible constant-current driver)
are left alone or flagged WARNING rather than guessed.  One finding per LED —
the worst offending channel.

All inputs come straight off the design graph — the LED's extracted specs
(``Component.specs.values``: per-colour ``forward_voltage_*_v``,
``forward_current_per_channel_a`` / ``forward_current_a``) and the series
resistor's ``value_ohms`` (or parsed ``value`` string).  Nothing is re-fetched.
"""

from __future__ import annotations

import re

from backend.pinscopex.models import ComponentType, DesignGraph, Finding, NetType
from backend.pinscopex.resolve_passives import _parse_spice_value

_COLOR_TOKENS = {
    "R": "red", "RED": "red",
    "G": "green", "GRN": "green", "GREEN": "green",
    "B": "blue", "BLU": "blue", "BLUE": "blue",
}


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------

def _num(v: object) -> float | None:
    """Parse a free-form spec value ("13mA", "2.8V", "3.3V typ, 4V max", or a
    bare float) to a float in base units, or None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    for cand in (s, *re.findall(r"[-+]?\d*\.?\d+\s*[a-zA-Zµ]*", s)):
        cand = cand.strip()
        if not cand:
            continue
        try:
            return _parse_spice_value(cand)
        except ValueError:
            pass
        m = re.match(r"^[-+]?\d*\.?\d+", cand)
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                pass
    return None


def _parse_resistance(v: object) -> float | None:
    """Parse a resistance string to ohms: "5.6K"->5600, "5K6"->5600,
    "150R"->150, "4R7"->4.7, "1M"->1e6, "0"->0."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).strip().upper().replace("OHMS", "").replace("OHM", "").replace("Ω", "").replace(" ", "")
    if not t:
        return None
    mult = {"R": 1.0, "K": 1e3, "M": 1e6, "G": 1e9}
    m = re.match(r"^(\d+)([RKMG])(\d+)$", t)          # 5K6, 4R7, 1M5
    if m:
        return (float(m.group(1)) + float(f"0.{m.group(3)}")) * mult[m.group(2)]
    m = re.match(r"^(\d*\.?\d+)([RKMG])$", t)          # 5.6K, 150R, 1M
    if m:
        return float(m.group(1)) * mult[m.group(2)]
    try:
        return float(t)
    except ValueError:
        return None


def _spec(values: dict, *keys: str) -> float | None:
    for k in keys:
        if k in values:
            n = _num(values[k])
            if n is not None:
                return n
    return None


def _imax(values: dict) -> float | None:
    """LED forward-current rating in amps."""
    i = _spec(values, "forward_current_per_channel_a", "forward_current_a",
              "max_forward_current_a", "if_max_a")
    if i is None:
        return None
    # A per-channel LED current >= 1 A is almost certainly mA written without a
    # unit (e.g. "13" meaning 13 mA) — scale down.
    if i >= 1.0:
        i = i / 1000.0
    return i


def _vf(values: dict, color: str | None) -> float | None:
    vf = None
    if color:
        vf = _spec(values, f"forward_voltage_{color}_v")
    if vf is None:
        vf = _spec(values, "forward_voltage_v", "vf_v")
    if vf is None:
        cands = [_spec(values, f"forward_voltage_{c}_v") for c in ("red", "green", "blue")]
        cands = [c for c in cands if c is not None]
        vf = min(cands) if cands else None  # lowest Vf = most conservative (highest I)
    if vf is not None and vf > 20:  # mV given without scaling
        vf = vf / 1000.0
    return vf


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------

def _net_voltage(graph: DesignGraph, net_name: str | None) -> float | None:
    if not net_name:
        return None
    net = graph.nets.get(net_name)
    return net.voltage if net else None


def _is_rail_net(graph: DesignGraph, net_name: str) -> bool:
    net = graph.nets.get(net_name)
    if not net:
        return False
    return net.net_type in (NetType.POWER, NetType.GROUND) or net.voltage is not None


def _series_resistor(graph: DesignGraph, net_name: str, exclude_ref: str):
    """Return (resistor_ref, ohms, far_net) for a 2-terminal series resistor on a
    private (degree-2) net, or None.  Requiring degree 2 ensures the resistor is
    truly in series with the LED leg, not merely sharing a bus/rail net."""
    net = graph.nets.get(net_name)
    if not net or len(net.pins) != 2:
        return None
    for pc in net.pins:
        if pc.component_ref == exclude_ref:
            continue
        c = graph.components.get(pc.component_ref)
        if not c or c.component_type != ComponentType.RESISTOR:
            continue
        rval = getattr(c.specs, "value_ohms", None) if c.specs else None
        if rval is None:
            rval = _parse_resistance(c.value)
        if rval is None or rval <= 0:
            continue
        far = next((n for n in c.pins.values() if n != net_name), None)
        return (pc.component_ref, float(rval), far)
    return None


def _leg_to_ic(graph: DesignGraph, net_name: str, exclude_ref: str) -> bool:
    """True if an IC sits on this leg net (possible constant-current driver)."""
    for r in graph.components_on_net(net_name):
        if r == exclude_ref:
            continue
        c = graph.components.get(r)
        if c and c.component_type == ComponentType.IC:
            return True
    return False


def _leg_color(pid: str, comp) -> str | None:
    if pid.upper() in _COLOR_TOKENS:
        return _COLOR_TOKENS[pid.upper()]
    specs = comp.specs
    pin = specs.pin_by_number(pid) if specs and hasattr(specs, "pin_by_number") else None
    if pin:
        for tok in re.split(r"[\s_/-]+", pin.name.upper()):
            if tok in _COLOR_TOKENS:
                return _COLOR_TOKENS[tok]
    return None


# ---------------------------------------------------------------------------
# Per-LED check
# ---------------------------------------------------------------------------

def check_led_current(graph: DesignGraph) -> list[Finding]:
    findings: list[Finding] = []
    for ref in sorted(graph.components_by_subtype("discrete.led")):
        comp = graph.components.get(ref)
        if not comp or not comp.specs:
            continue
        values = getattr(comp.specs, "values", None)
        if not values:
            continue
        imax = _imax(values)
        if imax is None:
            continue  # no forward-current rating -> nothing to check against
        finding = _check_led(graph, ref, comp, values, imax)
        if finding is not None:
            findings.append(finding)
    return findings


def _check_led(graph, ref, comp, values, imax) -> Finding | None:
    pins = comp.pins  # pid -> net
    pin_volts = [v for v in (_net_voltage(graph, n) for n in pins.values()) if v is not None]

    # Channels carrying current sit on private (signal) nets; for a 2-pin LED the
    # single channel is whichever pin actually has a series resistor.
    if len(pins) <= 2:
        leg = next(
            ((pid, net, _series_resistor(graph, net, ref))
             for pid, net in pins.items()
             if _series_resistor(graph, net, ref)),
            None,
        )
        if leg is None:
            cand = next(((pid, net) for pid, net in pins.items()
                         if not _is_rail_net(graph, net)), None)
            legs_iter = [(cand[0], cand[1], None)] if cand else []
        else:
            legs_iter = [leg]
    else:
        legs_iter = [
            (pid, net, _series_resistor(graph, net, ref))
            for pid, net in pins.items()
            if not _is_rail_net(graph, net)
        ]

    worst = None   # (i, color, net, vrail, vf, rval, rref)
    no_res = None  # (color, net, vrail, vf)
    for pid, net, res in legs_iter:
        color = _leg_color(pid, comp)
        vf = _vf(values, color)
        cand = list(pin_volts)
        if res and res[2]:
            fv = _net_voltage(graph, res[2])
            if fv is not None:
                cand.append(fv)
        vrail = max(cand) if cand else None

        if res is None:
            if no_res is None and vrail is not None and vrail > 0 and not _leg_to_ic(graph, net, ref):
                no_res = (color, net, vrail, vf)
            continue
        rref, rval, _far = res
        if vrail is None or vf is None or vrail <= vf or rval <= 0:
            continue
        i = (vrail - vf) / rval
        if i > imax and (worst is None or i > worst[0]):
            worst = (i, color, net, vrail, vf, rval, rref)

    if worst is not None:
        i, color, net, vrail, vf, rval, rref = worst
        return _over_current_finding(ref, comp, net, color, vrail, vf, rval, rref, imax, i)
    if no_res is not None:
        color, net, vrail, vf = no_res
        return _no_resistor_finding(ref, comp, net, color, vrail, vf, imax)
    return None


def _chan(color: str | None) -> str:
    return f"{color} channel" if color else "LED"


def _over_current_finding(ref, comp, net, color, vrail, vf, rval, rref, imax, i) -> Finding:
    rmin = (vrail - vf) / imax
    return Finding(
        designator=ref,
        mpn=comp.mpn or "",
        aspect="led_current",
        source="led_current_check",
        source_page=None,
        status="ERROR",
        finding=(
            f"{ref} {_chan(color)} forward current is ~{i * 1000:.0f} mA, "
            f"exceeding its {imax * 1000:.0f} mA forward-current rating."
        ),
        why=(
            f"With the supply at {vrail:.1f} V and Vf≈{vf:.1f} V, series resistor "
            f"{rref} ({rval:.0f} Ω) on net '{net}' passes "
            f"~({vrail:.1f}−{vf:.1f})/{rval:.0f} = {i * 1000:.0f} mA (worst case, "
            f"0 V driver drop) — above the {imax * 1000:.0f} mA rating."
        ),
        recommendation=(
            f"Increase the series resistor to at least {rmin:.0f} Ω to keep the "
            f"{_chan(color)} at or below {imax * 1000:.0f} mA."
        ),
        reference=f"{comp.mpn or ref} LED specs",
    )


def _no_resistor_finding(ref, comp, net, color, vrail, vf, imax) -> Finding:
    rec = "Add a series current-limiting resistor, or confirm a constant-current driver."
    if vf is not None and vrail > vf:
        rec = (
            f"Add a series resistor of at least {((vrail - vf) / imax):.0f} Ω "
            f"(or confirm a constant-current driver)."
        )
    return Finding(
        designator=ref,
        mpn=comp.mpn or "",
        aspect="led_current",
        source="led_current_check",
        source_page=None,
        status="WARNING",
        finding=(
            f"Unverified: {ref} {_chan(color)} has no series current-limiting "
            f"resistor on net '{net}'."
        ),
        why=(
            f"The {_chan(color)} on net '{net}' has no series resistor between the "
            f"LED and the {vrail:.1f} V supply. If it is not driven by a "
            f"constant-current source, forward current can exceed the "
            f"{imax * 1000:.0f} mA rating."
        ),
        recommendation=rec,
        reference=f"{comp.mpn or ref} LED specs",
    )
