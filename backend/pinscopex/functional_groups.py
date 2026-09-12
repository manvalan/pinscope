"""Topology-only functional groups for Layout F1 (routing-first floorplan).

No millimetres. Domains = power-net islands; satellites = 1-hop neighbors
classified with role_hint; layout_rules attached from IC extraction when present.

Self-contained helpers (no import of ``validate`` / Anthropic).
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel

from backend.pinscopex.models import (
    CapacitorSpecs,
    Component,
    ComponentConstraints,
    ComponentType,
    DesignGraph,
    NetType,
    SimpleComponentSpecs,
)
from backend.pinscopex.resolve_passives import _parse_spice_value

RoleHint = Literal[
    "decoupling",
    "bulk",
    "load_cap",
    "filter",
    "pullup",
    "series",
    "divider",
    "bridge",
    "crystal",
    "other",
]

_BULK_F = 1e-6  # >= 1 µF → bulk candidate
_XTAL_RE = re.compile(
    r"(?:^|[_/])(X(?:IN|OUT)|XTAL|OSC|HFX(?:IN|OUT)|LFX(?:IN|OUT)|CLK(?:IN|OUT)?)(?:$|[_/\d])",
    re.I,
)
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
_RANK_PREFIXES: list[tuple[str, int]] = [
    ("ic.mcu", 0),
    ("ic.mpu", 0),
    ("ic.fpga", 0),
    ("ic.soc", 0),
    ("ic.power", 1),
    ("ic.interface", 2),
    ("ic.protection", 3),
    ("ic.", 4),
]


class PlacementSatellite(BaseModel):
    ref: str
    component_type: str
    component_subtype: str | None = None
    nets: list[str] = []
    hop: int = 1
    role_hint: RoleHint = "other"


class PlacementIcGroup(BaseModel):
    ref: str
    mpn: str | None = None
    component_subtype: str | None = None
    rank: int = 99
    nets: list[str] = []
    satellites: list[PlacementSatellite] = []
    layout_rules: list[dict[str, Any]] = []
    assemble_order: list[str] = []


class PlacementDomain(BaseModel):
    domain_id: str
    power_nets: list[str] = []
    ic_refs: list[str] = []
    assemble_order: list[str] = []


class FunctionalGroupsReport(BaseModel):
    """Routing-first placement topology (no coordinates)."""
    objective: Literal["routing"] = "routing"
    domains: list[PlacementDomain] = []
    groups: list[PlacementIcGroup] = []


def build_functional_groups(
    graph: DesignGraph,
    constraints_map: dict[str, ComponentConstraints] | None = None,
) -> FunctionalGroupsReport:
    """Build domains + per-IC satellite groups from the design graph."""
    cmap = constraints_map or {}
    ic_refs = [
        r for r, c in graph.components.items()
        if c.component_type == ComponentType.IC
    ]
    groups: list[PlacementIcGroup] = []
    for ref in sorted(ic_refs, key=lambda r: (_ic_rank(graph.components[r]), r)):
        groups.append(_group_for_ic(graph, ref, cmap))

    domains = _build_domains(graph, ic_refs)
    by_ref = {g.ref: g for g in groups}
    for dom in domains:
        order: list[str] = []
        ranked = sorted(
            dom.ic_refs,
            key=lambda r: (by_ref[r].rank if r in by_ref else 99, r),
        )
        for iref in ranked:
            order.append(iref)
            g = by_ref.get(iref)
            if g:
                for sat in g.satellites:
                    if sat.ref not in order:
                        order.append(sat.ref)
        dom.assemble_order = order

    return FunctionalGroupsReport(objective="routing", domains=domains, groups=groups)


def load_capacitance_farads(comp: Component) -> float | None:
    """Crystal CL from SimpleComponentSpecs.values, if present."""
    specs = comp.specs
    if not isinstance(specs, SimpleComponentSpecs):
        return None
    raw = specs.values.get("load_capacitance_f")
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _match_constraints(
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


def _ic_rank(comp: Component) -> int:
    sub = (comp.component_subtype or "").lower()
    for prefix, rank in _RANK_PREFIXES:
        if sub == prefix.rstrip(".") or sub.startswith(prefix):
            return rank
    return 9


def _group_for_ic(
    graph: DesignGraph,
    ref: str,
    cmap: dict[str, ComponentConstraints],
) -> PlacementIcGroup:
    comp = graph.components[ref]
    cons = _match_constraints(comp.mpn or comp.value, cmap)
    nets = [n for n in graph.nets_of_component(ref) if not _is_ground_net(graph, n)]
    sat_map: dict[str, PlacementSatellite] = {}

    for net_name, others in graph.neighbors(ref).items():
        if _is_ground_net(graph, net_name):
            continue
        for oref in others:
            if oref == ref or oref in sat_map:
                continue
            other = graph.components.get(oref)
            if not other or other.component_type == ComponentType.IC:
                continue
            role = _role_hint(graph, comp, cons, other, net_name)
            sat_map[oref] = PlacementSatellite(
                ref=oref,
                component_type=other.component_type.value,
                component_subtype=other.component_subtype,
                nets=sorted({n for n in other.pins.values() if n}),
                hop=1,
                role_hint=role,
            )

    for pin_num, net_name in comp.pins.items():
        if not net_name or _is_ground_net(graph, net_name):
            continue
        if not _is_ic_supply_pin(graph, cons, pin_num, net_name):
            continue
        for cref in graph.capacitors_on_net(net_name):
            if cref == ref:
                continue
            cap = graph.components.get(cref)
            if not cap:
                continue
            others = {n for n in cap.pins.values() if n != net_name}
            if not any(_is_ground_net(graph, n) for n in others):
                continue
            farads = _cap_farads(cap)
            role: RoleHint = "bulk" if farads is not None and farads >= _BULK_F else "decoupling"
            existing = sat_map.get(cref)
            if existing is None or existing.role_hint in ("other", "series"):
                sat_map[cref] = PlacementSatellite(
                    ref=cref,
                    component_type=cap.component_type.value,
                    component_subtype=cap.component_subtype,
                    nets=sorted({n for n in cap.pins.values() if n}),
                    hop=1,
                    role_hint=role,
                )

    satellites = sorted(sat_map.values(), key=lambda s: (_role_sort(s.role_hint), s.ref))
    assemble = [ref] + [s.ref for s in satellites]
    rules: list[dict[str, Any]] = list(cons.layout_rules) if cons and cons.layout_rules else []

    return PlacementIcGroup(
        ref=ref,
        mpn=comp.mpn,
        component_subtype=comp.component_subtype or (cons.component_subtype if cons else None),
        rank=_ic_rank(comp),
        nets=sorted(nets),
        satellites=satellites,
        layout_rules=rules,
        assemble_order=assemble,
    )


def _role_sort(role: RoleHint) -> int:
    order = [
        "decoupling", "bulk", "load_cap", "crystal", "filter",
        "pullup", "divider", "series", "bridge", "other",
    ]
    try:
        return order.index(role)
    except ValueError:
        return 99


def _role_hint(
    graph: DesignGraph,
    ic: Component,
    cons: ComponentConstraints | None,
    other: Component,
    via_net: str,
) -> RoleHint:
    if other.component_type == ComponentType.CRYSTAL:
        return "crystal"

    if other.component_type == ComponentType.CAPACITOR:
        if _looks_xtal_net(via_net) or _ic_pin_is_xtal(cons, via_net, ic):
            return "load_cap"
        others = {n for n in other.pins.values() if n != via_net}
        if any(_is_ground_net(graph, n) for n in others) and (
            _is_power_net(graph, via_net) or _net_is_ic_supply(graph, ic, cons, via_net)
        ):
            farads = _cap_farads(other)
            return "bulk" if farads is not None and farads >= _BULK_F else "decoupling"
        return "other"

    if other.component_type == ComponentType.INDUCTOR:
        return "filter"

    if other.component_type == ComponentType.RESISTOR:
        nets = list(dict.fromkeys(other.pins.values()))
        if len(nets) == 2:
            a, b = nets
            if _is_power_net(graph, a) or _is_power_net(graph, b):
                if _is_ground_net(graph, a) or _is_ground_net(graph, b):
                    return "divider"
                return "pullup"
            ic_nets = set(ic.pins.values())
            if a in ic_nets and b in ic_nets:
                return "bridge"
            if a in ic_nets or b in ic_nets:
                return "series"
        return "other"

    return "other"


def _looks_xtal_net(name: str) -> bool:
    return bool(_XTAL_RE.search(name or ""))


def _ic_pin_is_xtal(
    cons: ComponentConstraints | None,
    net_name: str,
    ic: Component,
) -> bool:
    for pin_num, n in ic.pins.items():
        if n != net_name:
            continue
        tokens = _pin_name_tokens(cons, pin_num)
        if any(_XTAL_RE.search(t) for t in tokens):
            return True
    return _looks_xtal_net(net_name)


def _net_is_ic_supply(
    graph: DesignGraph,
    ic: Component,
    cons: ComponentConstraints | None,
    net_name: str,
) -> bool:
    for pin_num, n in ic.pins.items():
        if n == net_name and _is_ic_supply_pin(graph, cons, pin_num, net_name):
            return True
    return _is_power_net(graph, net_name)


def _build_domains(graph: DesignGraph, ic_refs: list[str]) -> list[PlacementDomain]:
    parent = {r: r for r in ic_refs}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    power_by_ic: dict[str, set[str]] = {}
    for ref in ic_refs:
        nets = set()
        for n in graph.nets_of_component(ref):
            if _is_power_net(graph, n) and not _is_ground_net(graph, n):
                nets.add(n)
        power_by_ic[ref] = nets

    rail_owners: dict[str, list[str]] = {}
    for ref, nets in power_by_ic.items():
        for n in nets:
            rail_owners.setdefault(n, []).append(ref)
    for refs in rail_owners.values():
        for i in range(1, len(refs)):
            union(refs[0], refs[i])

    clusters: dict[str, list[str]] = {}
    for ref in ic_refs:
        clusters.setdefault(find(ref), []).append(ref)

    domains: list[PlacementDomain] = []
    for i, (_root, members) in enumerate(
        sorted(clusters.items(), key=lambda x: sorted(x[1])[0]),
    ):
        members_sorted = sorted(members)
        rails: set[str] = set()
        for m in members_sorted:
            rails |= power_by_ic.get(m, set())
        domains.append(PlacementDomain(
            domain_id=f"domain_{i + 1}",
            power_nets=sorted(rails),
            ic_refs=members_sorted,
        ))
    return domains


def _pin_name_tokens(cons: ComponentConstraints | None, pin_num: str) -> list[str]:
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
    if _looks_like_supply(net_name or ""):
        return True
    net = graph.nets.get(net_name)
    return bool(net and net.net_type == NetType.POWER)


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
    return bool(re.match(r"^\+?\d+V\d*", (name or "").upper()))


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
