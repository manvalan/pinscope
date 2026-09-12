"""NC pintable pins must not sit on an active net with other parts."""

from __future__ import annotations

import re

from backend.pinscopex.models import (
    ComponentConstraints,
    ComponentType,
    DesignGraph,
    Finding,
)

_NC_NAME_RE = re.compile(
    r"^(?:n/?c|n\.c\.|nc|unconnected|no[_-]?connect|not[_-]?connected)$",
    re.IGNORECASE,
)
_NC_NET_RE = re.compile(
    r"^(?:n/?c|n\.c\.|nc|unconnected|no[_-]?connect|not[_-]?connected)$",
    re.IGNORECASE,
)


def check_nc_pins(
    graph: DesignGraph,
    constraints_map: dict[str, ComponentConstraints],
) -> list[Finding]:
    findings: list[Finding] = []
    for ref, comp in sorted(graph.components.items()):
        if comp.component_type != ComponentType.IC:
            continue
        cons = _match(comp.mpn or comp.value, constraints_map)
        if not cons or not cons.pintable:
            continue
        for pin in cons.pintable:
            if not _is_nc_pin_name(pin.name or ""):
                continue
            net_name = comp.pins.get(str(pin.number))
            if not net_name:
                continue
            if _NC_NET_RE.match(net_name.strip()):
                continue
            others = [
                r for r in graph.components_on_net(net_name)
                if r != ref
            ]
            if not others:
                # Lone net named oddly but empty of other parts — still flag if
                # the net name looks like a real signal (not floating placeholder).
                if _looks_active_net(net_name):
                    findings.append(_finding(ref, comp.mpn or "", pin.number, pin.name, net_name, []))
                continue
            findings.append(_finding(ref, comp.mpn or "", pin.number, pin.name, net_name, others))
    return findings


def _finding(ref, mpn, pin_num, pin_name, net, others) -> Finding:
    other_s = ", ".join(others[:6]) if others else "(no other refs)"
    return Finding(
        designator=ref,
        mpn=mpn,
        aspect="connectivity",
        source="nc_pin_check",
        status="WARNING",
        finding=(
            f"{ref} pin {pin_num} ({pin_name or 'NC'}) is marked NC in the "
            f"pintable but connects to net '{net}'"
            + (f" with {other_s}." if others else ".")
        ),
        why="No-connect pins should remain unconnected or on an explicit NC net.",
        recommendation="Leave the NC pin floating or disconnect the net.",
        reference="pintable",
        rule_id="PS-NC-001",
        net=net,
        pins=[f"{ref}.{pin_num}"],
    )


def _is_nc_pin_name(name: str) -> bool:
    t = (name or "").strip()
    if not t:
        return False
    if _NC_NAME_RE.match(t):
        return True
    # Slash-separated alts: "NC/GPIO" still counts as NC-capable; only pure NC.
    parts = [p.strip() for p in re.split(r"[/,]", t) if p.strip()]
    return bool(parts) and all(_NC_NAME_RE.match(p) or p.upper() == "NC" for p in parts)


def _looks_active_net(name: str) -> bool:
    u = (name or "").strip()
    if not u or u.startswith("unconnected"):
        return False
    return not _NC_NET_RE.match(u)


def _match(
    mpn: str | None,
    datasheets: dict[str, ComponentConstraints],
) -> ComponentConstraints | None:
    if not mpn:
        return None
    if mpn in datasheets:
        return datasheets[mpn]
    norm = re.sub(r"[/_\-\s]", "", mpn).upper()
    for ds_mpn, constraints in datasheets.items():
        if re.sub(r"[/_\-\s]", "", ds_mpn).upper() == norm:
            return constraints
    return None
