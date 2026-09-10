"""Deterministic supply decoupling and I2C/reset pull-up checks.

These only fire when the graph already shows a pintable supply pin, an I2C
net/pin name, or a reset pin — they do not guess capacitor values, mux
alt-functions, or datasheet µF minima.
"""

from __future__ import annotations

import re

from backend.pinscopex.models import (
    CapacitorSpecs,
    Component,
    ComponentConstraints,
    ComponentType,
    DesignGraph,
    Finding,
    NetType,
    ResistorSpecs,
)
from backend.pinscopex.validate import _match_constraints
from backend.pinscopex.led_current_check import _parse_resistance
from backend.pinscopex.resolve_passives import _parse_spice_value

_SUPPLY_PIN_RE = re.compile(
    r"(?:^|[_/])(VDD|VCC|VDDA|VDDD|VDDIO|DVDD|AVDD|IOVDD|VDD33|VDD18|"
    r"VIN|VBAT|VBUS|VCORE)(?:$|[_/\d])",
    re.IGNORECASE,
)
_RAIL_PIN_RE = re.compile(r"^(?:\+?\d+V\d*)$", re.IGNORECASE)
_NOT_SUPPLY_RE = re.compile(
    r"\b(VSS|GND|VEE|VOUT|VREF|SW|LX|FB|BOOT|NC|VPP)\b",
    re.IGNORECASE,
)
_I2C_RE = re.compile(r"(?:^|[^A-Za-z0-9])(SDA|SCL)(\d+)?(?:$|[^A-Za-z0-9])", re.IGNORECASE)
_SPI_NAME_RE = re.compile(r"(?i)\b(MISO|MOSI|SCLK|SCK)\b")
_RESET_RE = re.compile(
    r"\b(N?RST(?:N|B)?|NRST|RESET(?:_?N|_?B)?|NRESET|CHIP_PU)\b",
    re.IGNORECASE,
)
_NC_NET_RE = re.compile(
    r"^(?:n/?c|n\.c\.|nc|unconnected|no[_-]?connect|not[_-]?connected)$",
    re.IGNORECASE,
)
_OUT_PIN_RE = re.compile(
    r"(?:^|[_/])(VOUT|V_OUT|VO|VREG|SWOUT)(?:$|[_/\d])",
    re.IGNORECASE,
)
_ACTIVE_LOW_RESET_RE = re.compile(
    r"(?:N/?RST|NRST|NRESET|RESET[_-]?N|RSTN)\b",
    re.IGNORECASE,
)
# NXP UM10204-style Rp window, widened so 2.2k–10k never false-positives.
_RP_MIN_OHM = 1_000.0
_RP_MAX_OHM = 22_000.0
_VDD_MIN_FARADS = 50e-9
_VOUT_MIN_FARADS = 0.47e-6


def check_supply_decoupling(
    graph: DesignGraph,
    constraints_map: dict[str, ComponentConstraints],
) -> list[Finding]:
    """WARNING when an IC supply/VOUT net has no capacitor to ground, or
    only farads well below a typical Cin/Cout when every cap is valued."""
    findings: list[Finding] = []
    seen_nets: set[str] = set()
    for ref, comp in sorted(graph.components.items()):
        if comp.component_type != ComponentType.IC:
            continue
        cons = _match_constraints(comp.mpn or comp.value, constraints_map)
        for pin_num, net_name in sorted(comp.pins.items(), key=lambda x: str(x[0])):
            if net_name in seen_nets:
                continue
            if _is_nc_net(net_name):
                continue
            role = None
            if _is_ic_supply_pin(graph, cons, pin_num, net_name):
                role = "supply"
            elif _is_regulator_output_pin(cons, pin_num):
                role = "output"
            if role is None:
                continue
            seen_nets.add(net_name)
            pin_label = _pin_label(cons, pin_num, net_name)
            if not _capacitor_to_ground(graph, net_name):
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
                        if role == "supply"
                        else (
                            f"{ref} regulator output '{net_name}' ({pin_label}) "
                            f"has no Cout capacitor to ground."
                        )
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
                    net=net_name,
                    pins=[f"{ref}.{pin_num}"],
                    rule_id="PS-DEC-001",
                ))
                continue
            min_f = _VOUT_MIN_FARADS if role == "output" else _VDD_MIN_FARADS
            max_c = _max_known_cap_farads(graph, net_name)
            if max_c is not None and max_c < min_f:
                findings.append(Finding(
                    designator=ref,
                    mpn=comp.mpn or "",
                    aspect="decoupling",
                    source="supply_decoupling_check",
                    source_page=None,
                    status="WARNING",
                    finding=(
                        f"{ref} net '{net_name}' ({pin_label}) only has "
                        f"{max_c * 1e6:.3g} µF to ground; typical "
                        f"{'Cout' if role == 'output' else 'decoupling'} is larger."
                    ),
                    why=(
                        "Cap values are known on this net and the largest is "
                        "below a wide typical minimum. This is not a datasheet "
                        "µF requirement — treat as a sizing hint."
                    ),
                    recommendation=(
                        f"Add bulk capacitance on '{net_name}' (often ≥1 µF on "
                        f"LDO VOUT, ≥100 nF on MCU VDD) if the datasheet agrees."
                    ),
                    reference="netlist topology",
                    net=net_name,
                    pins=[f"{ref}.{pin_num}"],
                    rule_id="PS-DEC-002",
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
            if _is_nc_net(net_name):
                continue
            if not _is_i2c_pin(graph, cons, pin_num, net_name):
                continue
            seen_nets.add(net_name)
            net = graph.nets.get(net_name)
            if net and net.net_type in (NetType.POWER, NetType.GROUND):
                continue
            if _resistor_to_power(graph, net_name):
                ohms = _parallel_pullup_ohms(graph, net_name)
                if ohms is not None and (
                    ohms < _RP_MIN_OHM or ohms > _RP_MAX_OHM
                ):
                    pin_label = _pin_label(cons, pin_num, net_name)
                    findings.append(Finding(
                        designator=ref,
                        mpn=comp.mpn or "",
                        aspect="i2c_pullup",
                        source="i2c_pullup_check",
                        source_page=None,
                        status="WARNING",
                        finding=(
                            f"I2C net '{net_name}' ({ref} {pin_label}) pull-up "
                            f"is {ohms:.3g} Ω (wide NXP-style band "
                            f"{_RP_MIN_OHM:.0f}–{_RP_MAX_OHM:.0f} Ω)."
                        ),
                        why=(
                            "UM10204 Rp depends on Vdd, Iol and bus capacitance. "
                            "This bound is wide on purpose; 2.2–10 kΩ at 3.3 V "
                            "is typical. Unknown resistor values are not sized."
                        ),
                        recommendation=(
                            f"Use a pull-up on '{net_name}' inside "
                            f"{_RP_MIN_OHM:.0f}–{_RP_MAX_OHM:.0f} Ω unless the "
                            f"bus capacitance/Iol calculation says otherwise."
                        ),
                        reference="NXP UM10204 (wide bound)",
                        net=net_name,
                        pins=[f"{ref}.{pin_num}"],
                        rule_id="PS-I2C-002",
                    ))
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
                net=net_name,
                pins=[f"{ref}.{pin_num}"],
                rule_id="PS-I2C-001",
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
            if _is_nc_net(net_name):
                continue
            if not _is_reset_pin(graph, cons, pin_num, net_name):
                continue
            seen_nets.add(net_name)
            net = graph.nets.get(net_name)
            if net and net.net_type in (NetType.POWER, NetType.GROUND):
                continue
            if _other_ic_on_net(graph, net_name, ref):
                continue
            pin_label = _pin_label(cons, pin_num, net_name)
            if _is_active_low_reset(cons, pin_num, net_name) and _resistor_to_ground(
                graph, net_name
            ):
                findings.append(Finding(
                    designator=ref,
                    mpn=comp.mpn or "",
                    aspect="reset_pullup",
                    source="reset_pullup_check",
                    source_page=None,
                    status="WARNING",
                    finding=(
                        f"{ref} active-low reset '{net_name}' ({pin_label}) "
                        f"has a pull-down to ground."
                    ),
                    why=(
                        "An active-low NRST/RESET_N pin held down by a resistor "
                        "will sit in reset unless a stronger pull-up wins. "
                        "Datasheets that omit an internal pull-up expect a pull-up, "
                        "not a pull-down."
                    ),
                    recommendation=(
                        f"Remove the pull-down on '{net_name}' or replace it "
                        f"with a pull-up to the I/O rail."
                    ),
                    reference="netlist topology",
                    net=net_name,
                    pins=[f"{ref}.{pin_num}"],
                    rule_id="PS-RST-002",
                ))
            if _resistor_to_power(graph, net_name):
                continue
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
                net=net_name,
                pins=[f"{ref}.{pin_num}"],
                rule_id="PS-RST-001",
            ))
    return findings


def _pin_label(cons: ComponentConstraints | None, pin_num: str, net_name: str) -> str:
    if cons:
        pin = cons.pin_by_number(pin_num)
        if pin and pin.name:
            return f"{pin_num} ({pin.name})"
    return str(pin_num)


def _is_nc_net(name: str) -> bool:
    return bool(_NC_NET_RE.match((name or "").strip()))


def _pin_name_tokens(cons: ComponentConstraints | None, pin_num: str) -> list[str]:
    """Slash-separated pin *name* tokens only — not the mux alt-function table."""
    if not cons:
        return []
    pin = cons.pin_by_number(pin_num)
    if not pin or not pin.name:
        return []
    return [t.strip() for t in re.split(r"[/,]", pin.name) if t.strip()]


def _looks_like_supply(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if _NOT_SUPPLY_RE.search(t) and not _SUPPLY_PIN_RE.search(t):
        return False
    return bool(_SUPPLY_PIN_RE.search(t) or _RAIL_PIN_RE.match(t))


def _is_ic_supply_pin(
    graph: DesignGraph,
    cons: ComponentConstraints | None,
    pin_num: str,
    net_name: str,
) -> bool:
    tokens = _pin_name_tokens(cons, pin_num)
    if tokens:
        return any(_looks_like_supply(t) for t in tokens)
    # No pintable row: fall back to net name / POWER type.
    if _looks_like_supply(net_name or ""):
        return True
    net = graph.nets.get(net_name)
    return bool(net and net.net_type == NetType.POWER)


def _is_i2c_pin(
    graph: DesignGraph,
    cons: ComponentConstraints | None,
    pin_num: str,
    net_name: str,
) -> bool:
    net = net_name or ""
    if re.match(r"(?i)SPI([_-]|$)", net) or re.search(
        r"(?i)\bSPI[_-]?(CLK|SCK|MOSI|MISO|CS|SS)\b", net,
    ):
        return False
    tokens = _pin_name_tokens(cons, pin_num)
    if any(_SPI_NAME_RE.search(t) for t in tokens):
        return False
    if _I2C_RE.search(net):
        return True
    return any(_I2C_RE.search(t) for t in tokens)


def _is_reset_pin(
    graph: DesignGraph,
    cons: ComponentConstraints | None,
    pin_num: str,
    net_name: str,
) -> bool:
    if _RESET_RE.search(net_name or ""):
        return True
    return any(_RESET_RE.search(t) for t in _pin_name_tokens(cons, pin_num))


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
    if net and net.net_type == NetType.POWER:
        return True
    return bool(re.match(r"^\d+V\d*", (name or "").upper()))


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


def _resistor_to_ground(graph: DesignGraph, net_name: str) -> bool:
    for ref in graph.components_on_net(net_name):
        comp = graph.components[ref]
        if comp.component_type != ComponentType.RESISTOR:
            continue
        others = {n for n in comp.pins.values() if n != net_name}
        if any(_is_ground_net(graph, n) for n in others):
            return True
    return False


def _resistor_ohms(comp: Component) -> float | None:
    specs = comp.specs
    if isinstance(specs, ResistorSpecs) and specs.value_ohms > 0:
        return float(specs.value_ohms)
    return _parse_resistance(comp.value)


def _parallel_pullup_ohms(graph: DesignGraph, net_name: str) -> float | None:
    acc = 0.0
    known = 0
    for ref in graph.components_on_net(net_name):
        comp = graph.components[ref]
        if comp.component_type != ComponentType.RESISTOR:
            continue
        others = {n for n in comp.pins.values() if n != net_name}
        if not any(_is_power_net(graph, n) for n in others):
            continue
        ohms = _resistor_ohms(comp)
        if ohms is None or ohms <= 0:
            return None
        acc += 1.0 / ohms
        known += 1
    if not known or acc <= 0:
        return None
    return 1.0 / acc


def _cap_farads(comp: Component) -> float | None:
    specs = comp.specs
    if isinstance(specs, CapacitorSpecs) and specs.value_farads > 0:
        return float(specs.value_farads)
    raw = (comp.value or "").strip()
    if not raw:
        return None
    try:
        v = _parse_spice_value(raw)
    except ValueError:
        return None
    return v if v > 0 else None


def _max_known_cap_farads(graph: DesignGraph, power_net: str) -> float | None:
    known: list[float] = []
    any_unknown = False
    for ref in graph.capacitors_on_net(power_net):
        cap = graph.components[ref]
        others = {n for n in cap.pins.values() if n != power_net}
        if not any(_is_ground_net(graph, n) for n in others):
            continue
        farads = _cap_farads(cap)
        if farads is None:
            any_unknown = True
            continue
        known.append(farads)
    if any_unknown or not known:
        return None
    return max(known)


def _is_regulator_output_pin(
    cons: ComponentConstraints | None, pin_num: str,
) -> bool:
    return any(_OUT_PIN_RE.search(t) for t in _pin_name_tokens(cons, pin_num))


def _is_active_low_reset(
    cons: ComponentConstraints | None, pin_num: str, net_name: str,
) -> bool:
    if _ACTIVE_LOW_RESET_RE.search(net_name or ""):
        return True
    return any(_ACTIVE_LOW_RESET_RE.search(t) for t in _pin_name_tokens(cons, pin_num))


def _other_ic_on_net(graph: DesignGraph, net_name: str, self_ref: str) -> bool:
    for ref in graph.components_on_net(net_name):
        if ref == self_ref:
            continue
        other = graph.components.get(ref)
        if other and other.component_type == ComponentType.IC:
            return True
    return False
