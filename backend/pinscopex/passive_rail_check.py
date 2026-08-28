"""Deterministic supply decoupling and I2C/reset pull-up checks.

These only fire when the graph already shows a power pin, an I2C net, or a
reset pin — they do not guess capacitor values or datasheet µF minima.
"""

from __future__ import annotations

import re

from backend.pinscopex.models import (
    ComponentConstraints,
    ComponentType,
    DesignGraph,
    Finding,
    NetType,
)
from backend.pinscopex.validate import _match_constraints

_SUPPLY_PIN_RE = re.compile(
    r"(?:^|[_/])(VDD|VCC|VDDA|VDDD|VDDIO|DVDD|AVDD|IOVDD|VDD33|VDD18|"
    r"VIN|VBAT|VBUS|VCORE)(?:$|[_/\d])",
    re.IGNORECASE,
)
_NOT_SUPPLY_RE = re.compile(
    r"\b(VSS|GND|VEE|VOUT|VREF|SW|LX|FB|BOOT|NC|VPP)\b",
    re.IGNORECASE,
)
_I2C_RE = re.compile(r"\b(SDA|SCL)(\d+)?\b", re.IGNORECASE)
_RESET_RE = re.compile(
    r"\b(N?RST(?:N|B)?|NRST|RESET(?:_?N|_?B)?|NRESET|CHIP_PU)\b",
    re.IGNORECASE,
)


def check_supply_decoupling(
    graph: DesignGraph,
    constraints_map: dict[str, ComponentConstraints],
) -> list[Finding]:
    """WARNING when an IC supply net has no capacitor to ground."""
    findings: list[Finding] = []
    seen_nets: set[str] = set()
    for ref, comp in sorted(graph.components.items()):
        if comp.component_type != ComponentType.IC:
            continue
        cons = _match_constraints(comp.mpn or comp.value, constraints_map)
        for pin_num, net_name in sorted(comp.pins.items(), key=lambda x: str(x[0])):
            if net_name in seen_nets:
                continue
            if not _is_ic_supply_pin(graph, cons, pin_num, net_name):
                continue
            seen_nets.add(net_name)
            if _capacitor_to_ground(graph, net_name):
                continue
            pin_label = _pin_label(cons, pin_num, net_name)
            findings.append(Finding(
                designator=ref,
                mpn=comp.mpn or "",
                aspect="decoupling",
                source="supply_decoupling_check",
                source_page=None,
                status="WARNING",
                finding=(
                    f"{ref} supply net '{net_name}' ({pin_label}) has no "
                    f"capacitor to ground."
                ),
                why=(
                    f"Pin {pin_label} sits on '{net_name}' and that net has no "
                    f"capacitor whose other end is ground. Local decoupling "
                    f"may be missing (or only present on a different island "
                    f"behind a ferrite)."
                ),
                recommendation=(
                    f"Add a decoupling capacitor from '{net_name}' to ground "
                    f"near {ref}."
                ),
                reference="netlist topology",
            ))
    return findings


def check_i2c_pullups(
    graph: DesignGraph,
    constraints_map: dict[str, ComponentConstraints],
) -> list[Finding]:
    """WARNING when an SDA/SCL net has no resistor to a power rail."""
    findings: list[Finding] = []
    seen_nets: set[str] = set()
    for ref, comp in sorted(graph.components.items()):
        if comp.component_type != ComponentType.IC:
            continue
        cons = _match_constraints(comp.mpn or comp.value, constraints_map)
        for pin_num, net_name in sorted(comp.pins.items(), key=lambda x: str(x[0])):
            if net_name in seen_nets:
                continue
            if not _is_i2c_pin(graph, cons, pin_num, net_name):
                continue
            seen_nets.add(net_name)
            net = graph.nets.get(net_name)
            if net and net.net_type in (NetType.POWER, NetType.GROUND):
                continue
            if _resistor_to_power(graph, net_name):
                continue
            pin_label = _pin_label(cons, pin_num, net_name)
            findings.append(Finding(
                designator=ref,
                mpn=comp.mpn or "",
                aspect="i2c_pullup",
                source="i2c_pullup_check",
                source_page=None,
                status="WARNING",
                finding=(
                    f"I2C net '{net_name}' ({ref} {pin_label}) has no pull-up "
                    f"resistor to a power rail."
                ),
                why=(
                    f"SDA/SCL is open-drain. Without a resistor from "
                    f"'{net_name}' to a supply, the bus cannot idle high."
                ),
                recommendation=(
                    f"Add a pull-up (typically 2.2–10 kΩ) from '{net_name}' "
                    f"to the I2C I/O rail."
                ),
                reference="netlist topology",
            ))
    return findings


def check_reset_pullups(
    graph: DesignGraph,
    constraints_map: dict[str, ComponentConstraints],
) -> list[Finding]:
    """WARNING when a reset pin's net is only this IC and has no pull-up."""
    findings: list[Finding] = []
    seen_nets: set[str] = set()
    for ref, comp in sorted(graph.components.items()):
        if comp.component_type != ComponentType.IC:
            continue
        cons = _match_constraints(comp.mpn or comp.value, constraints_map)
        for pin_num, net_name in sorted(comp.pins.items(), key=lambda x: str(x[0])):
            if net_name in seen_nets:
                continue
            if not _is_reset_pin(graph, cons, pin_num, net_name):
                continue
            seen_nets.add(net_name)
            net = graph.nets.get(net_name)
            if net and net.net_type in (NetType.POWER, NetType.GROUND):
                continue
            if _other_ic_on_net(graph, net_name, ref):
                continue
            if _resistor_to_power(graph, net_name):
                continue
            pin_label = _pin_label(cons, pin_num, net_name)
            findings.append(Finding(
                designator=ref,
                mpn=comp.mpn or "",
                aspect="reset_pullup",
                source="reset_pullup_check",
                source_page=None,
                status="WARNING",
                finding=(
                    f"{ref} reset pin {pin_label} on '{net_name}' has no "
                    f"pull-up and no other IC driving the net."
                ),
                why=(
                    f"The net only lands on {ref} (plus passives). Without a "
                    f"resistor to a supply, an active-low reset input can float."
                ),
                recommendation=(
                    f"Add a pull-up to the I/O rail, or drive '{net_name}' "
                    f"from a reset supervisor / GPIO."
                ),
                reference="netlist topology",
            ))
    return findings


def _pin_label(cons: ComponentConstraints | None, pin_num: str, net_name: str) -> str:
    if cons:
        pin = cons.pin_by_number(pin_num)
        if pin and pin.name:
            return f"{pin_num} ({pin.name})"
    return str(pin_num)


def _pin_blob(
    cons: ComponentConstraints | None, pin_num: str, net_name: str,
) -> str:
    parts = [net_name or ""]
    if cons:
        pin = cons.pin_by_number(pin_num)
        if pin:
            parts.append(pin.name or "")
            if pin.functions:
                parts.extend(pin.functions)
    return " ".join(parts)


def _is_ic_supply_pin(
    graph: DesignGraph,
    cons: ComponentConstraints | None,
    pin_num: str,
    net_name: str,
) -> bool:
    blob = _pin_blob(cons, pin_num, net_name)
    if _NOT_SUPPLY_RE.search(blob) and not _SUPPLY_PIN_RE.search(blob):
        return False
    if _SUPPLY_PIN_RE.search(blob):
        return True
    net = graph.nets.get(net_name)
    return bool(net and net.net_type == NetType.POWER)


def _is_i2c_pin(
    graph: DesignGraph,
    cons: ComponentConstraints | None,
    pin_num: str,
    net_name: str,
) -> bool:
    return bool(_I2C_RE.search(_pin_blob(cons, pin_num, net_name)))


def _is_reset_pin(
    graph: DesignGraph,
    cons: ComponentConstraints | None,
    pin_num: str,
    net_name: str,
) -> bool:
    return bool(_RESET_RE.search(_pin_blob(cons, pin_num, net_name)))


def _is_ground_net(graph: DesignGraph, name: str) -> bool:
    net = graph.nets.get(name)
    if net and net.net_type == NetType.GROUND:
        return True
    u = name.upper().replace("-", "_")
    return u in ("GND", "VSS", "AGND", "DGND", "PGND", "GNDA", "GNDD") or (
        u.startswith("GND") or u.endswith("_GND") or u.endswith("_VSS")
    )


def _is_power_net(graph: DesignGraph, name: str) -> bool:
    net = graph.nets.get(name)
    return bool(net and net.net_type == NetType.POWER)


def _capacitor_to_ground(graph: DesignGraph, power_net: str) -> bool:
    for ref in graph.capacitors_on_net(power_net):
        cap = graph.components[ref]
        others = {n for n in cap.pins.values() if n != power_net}
        if any(_is_ground_net(graph, n) for n in others):
            return True
    return False


def _resistor_to_power(graph: DesignGraph, net_name: str) -> bool:
    for ref in graph.components_on_net(net_name):
        comp = graph.components[ref]
        if comp.component_type != ComponentType.RESISTOR:
            continue
        others = {n for n in comp.pins.values() if n != net_name}
        if any(_is_power_net(graph, n) for n in others):
            return True
    return False


def _other_ic_on_net(graph: DesignGraph, net_name: str, self_ref: str) -> bool:
    for ref in graph.components_on_net(net_name):
        if ref == self_ref:
            continue
        other = graph.components.get(ref)
        if other and other.component_type == ComponentType.IC:
            return True
    return False
