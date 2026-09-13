"""RF antenna verify + design recipe (schema + optional PCB).

Verify: matching topology from IC ANT/RF pin toward ANT footprint / ANT_FEED.
Design: KiCad marker (ANT* footprint or ANT_FEED/RF_ANT net) → microstrip w
for target Z0 from stackup; optional λ/4 length if f0_mhz is given;
parametric IFA / meander / stub geometry (segments + SVG + .kicad_mod).

No EM/VSWR. No CPWG clearance. Geometry is a documented routing template only.
"""

from __future__ import annotations

import math
import re
from typing import Any, Literal

from pydantic import BaseModel

from backend.periscopex.antenna_geometry import (
    AntennaGeometry,
    AntennaTemplate,
    build_geometry,
)
from backend.periscopex.impedance import GeometryError, solve_width
from backend.periscopex.models import (
    ComponentType,
    DesignGraph,
    LayoutGraph,
)

_C_MPS = 299_792_458.0

_ANT_PIN_RE = re.compile(
    r"(?:^|[_/\-])(ANT|ANTENNA|RF(?:IO|OUT|IN)?|RF_OUT|RF_IN|LNA|TX|RX)(?:$|[_/\-\d])",
    re.IGNORECASE,
)
_FEED_NET_RE = re.compile(
    r"^(?:ANT_FEED|ANTENNA_FEED)$",
    re.IGNORECASE,
)
_ZONE_NET_RE = re.compile(
    r"(?:^|[_/\-])(antenna|ant_zone|rf_antenna)(?:$|[_/\-])",
    re.IGNORECASE,
)

Topology = Literal[
    "direct", "series_L", "LC", "pi", "T", "unknown", "missing",
]
Status = Literal["ok", "warning", "info"]
DesignStatus = Literal["ready", "need_pcb", "need_stackup", "need_marker"]


class AntennaVerifyRow(BaseModel):
    ic_ref: str
    pin: str
    net: str
    topology: Topology
    parts: list[str] = []
    target_z_ohm: float = 50.0
    status: Status = "info"
    detail: str = ""
    feed_z0: float | None = None
    feed_length_mm: float | None = None
    marker_ref: str | None = None


class AntennaFeedLine(BaseModel):
    kind: str = "microstrip"
    target_z_ohm: float = 50.0
    w_mm: float | None = None
    h_mm: float | None = None
    er: float | None = None
    t_mm: float | None = None


class AntennaRadiator(BaseModel):
    length_mm_suggest: float | None = None
    f0_mhz: float | None = None
    note: str = (
        "λ/4 estimate using εeff≈(εr+1)/2 — routing-first only, not an EM result."
    )


class AntennaZoneInfo(BaseModel):
    net: str
    layer: str
    bbox_mm: tuple[float, float, float, float] | None = None  # xmin,ymin,xmax,ymax
    area_mm2: float | None = None


class AntennaDesignRecipe(BaseModel):
    status: DesignStatus
    feed_point: dict[str, Any] | None = None
    feed_line: AntennaFeedLine | None = None
    radiator: AntennaRadiator | None = None
    geometry: AntennaGeometry | None = None
    zone: AntennaZoneInfo | None = None
    keepout_checklist: list[str] = []
    detail: str = ""


class AntennaReport(BaseModel):
    verify: list[AntennaVerifyRow] = []
    design: AntennaDesignRecipe | None = None
    marker_help: str = (
        "Mark the feed join in KiCad: footprint Ref starting with ANT, "
        "or net named ANT_FEED / RF_ANT. Optional zone net 'antenna' for the canvas."
    )


def build_antenna_report(
    graph: DesignGraph,
    layout: LayoutGraph | None = None,
    *,
    impedance_nets: dict | None = None,
    f0_mhz: float | None = None,
    target_z_ohm: float = 50.0,
    h_mm: float | None = None,
    er: float | None = None,
    t_mm: float | None = None,
    template: AntennaTemplate = "ifa",
) -> AntennaReport:
    verify = _verify(graph, layout, impedance_nets, target_z_ohm)
    design = build_design_recipe(
        graph, layout,
        f0_mhz=f0_mhz,
        target_z_ohm=target_z_ohm,
        h_mm=h_mm,
        template=template,
        er=er,
        t_mm=t_mm,
    )
    return AntennaReport(verify=verify, design=design)


def build_design_recipe(
    graph: DesignGraph,
    layout: LayoutGraph | None = None,
    *,
    f0_mhz: float | None = None,
    target_z_ohm: float = 50.0,
    h_mm: float | None = None,
    er: float | None = None,
    t_mm: float | None = None,
    template: AntennaTemplate = "ifa",
) -> AntennaDesignRecipe:
    marker = _find_marker(graph, layout)
    zone = _find_antenna_zone(layout)
    checklist = [
        "Keep copper / pours out of the antenna keepout unless the antenna datasheet allows it.",
        "Short GND return from the matching network to the RF reference.",
        "Avoid long stubs and right angles on the 50 Ω feed.",
        "Place matching parts close to the RF pin / feed point.",
        "IFA pad 2 (shorting tip) must connect to RF ground / pour edge.",
        "Place the footprint with feed (pad 1) on the ANT* / ANT_FEED join.",
    ]

    stack = _resolve_stackup(layout, h_mm=h_mm, er=er, t_mm=t_mm)
    if marker is None and layout is None:
        return AntennaDesignRecipe(
            status="need_pcb",
            zone=zone,
            keepout_checklist=checklist,
            detail="Upload a .kicad_pcb (and mark ANT* / ANT_FEED) to compute feed width.",
        )
    if marker is None:
        return AntennaDesignRecipe(
            status="need_marker",
            zone=zone,
            keepout_checklist=checklist,
            detail="No ANT* footprint or ANT_FEED/RF_ANT net found.",
        )
    if stack is None:
        return AntennaDesignRecipe(
            status="need_stackup",
            feed_point=marker,
            zone=zone,
            keepout_checklist=checklist,
            detail="PCB stackup missing εr/h — set stackup in KiCad or pass h/er in the request.",
        )

    h, er_v, t = stack
    try:
        w = solve_width("microstrip", target_z_ohm, h, er_v, t, s=None)
    except GeometryError as exc:
        return AntennaDesignRecipe(
            status="need_stackup",
            feed_point=marker,
            zone=zone,
            keepout_checklist=checklist,
            detail=str(exc),
        )

    feed = AntennaFeedLine(
        kind="microstrip",
        target_z_ohm=target_z_ohm,
        w_mm=round(w, 4),
        h_mm=h,
        er=er_v,
        t_mm=t,
    )
    radiator = None
    if f0_mhz is not None and f0_mhz > 0:
        eeff = (er_v + 1.0) / 2.0
        f_hz = f0_mhz * 1e6
        length_m = _C_MPS / (4.0 * f_hz * math.sqrt(eeff))
        radiator = AntennaRadiator(
            length_mm_suggest=round(length_m * 1e3, 2),
            f0_mhz=f0_mhz,
        )

    feed_xy = None
    if marker.get("x") is not None and marker.get("y") is not None:
        feed_xy = (float(marker["x"]), float(marker["y"]))
    zone_bbox = zone.bbox_mm if zone else None
    geometry = build_geometry(
        template,
        f0_mhz=f0_mhz,
        w_mm=float(feed.w_mm or 0),
        er=er_v,
        zone_bbox_mm=zone_bbox,
        feed_xy=feed_xy,
    )

    detail = "Recipe ready — feed at w_mm; geometry is a parametric template (not EM)."
    if geometry.fit == "need_f0":
        detail = "Feed w ready — set f0 to generate IFA / meander / stub geometry."
    elif geometry.fit == "scaled":
        detail = geometry.detail
    elif geometry.fit == "overflow":
        detail = geometry.detail

    return AntennaDesignRecipe(
        status="ready",
        feed_point=marker,
        feed_line=feed,
        radiator=radiator,
        geometry=geometry,
        zone=zone,
        keepout_checklist=checklist,
        detail=detail,
    )


def _verify(
    graph: DesignGraph,
    layout: LayoutGraph | None,
    impedance_nets: dict | None,
    target_z: float,
) -> list[AntennaVerifyRow]:
    z_by_net = _z0_index(impedance_nets)
    rows: list[AntennaVerifyRow] = []
    for ref, comp in sorted(graph.components.items()):
        if comp.component_type != ComponentType.IC:
            continue
        for pin_num, net in comp.pins.items():
            if not net or not _looks_rf_pin(graph, ref, pin_num, net, comp.component_subtype):
                continue
            topo, parts, marker, detail, status = _classify_path(graph, ref, net)
            z0, length = None, None
            if net in z_by_net:
                z0 = z_by_net[net].get("z0_avg_ohms")
                length = z_by_net[net].get("length_mm")
            elif marker and marker.get("net") and marker["net"] in z_by_net:
                info = z_by_net[marker["net"]]
                z0 = info.get("z0_avg_ohms")
                length = info.get("length_mm")
            rows.append(AntennaVerifyRow(
                ic_ref=ref,
                pin=str(pin_num),
                net=net,
                topology=topo,
                parts=parts,
                target_z_ohm=target_z,
                status=status,
                detail=detail,
                feed_z0=z0,
                feed_length_mm=length,
                marker_ref=marker.get("ref") if marker else None,
            ))
    return rows


def _looks_rf_pin(
    graph: DesignGraph,
    ref: str,
    pin_num: str,
    net: str,
    subtype: str | None,
) -> bool:
    if _FEED_NET_RE.match(net or ""):
        return True
    if _ANT_PIN_RE.search(net or ""):
        return True
    # Pin name from netlist is often just the net; subtype helps for modules.
    sub = (subtype or "").lower()
    if sub.startswith("ic.rf") and _ANT_PIN_RE.search(net or ""):
        return True
    if sub.startswith("ic.rf"):
        # Common module pad names appear as nets
        u = (net or "").upper()
        if any(k in u for k in ("ANT", "RF", "LNA", "WIFI")):
            return True
    return bool(_ANT_PIN_RE.search(str(pin_num)))


def _classify_path(
    graph: DesignGraph,
    ic_ref: str,
    start_net: str,
) -> tuple[Topology, list[str], dict | None, str, Status]:
    """BFS a few hops of passives toward ANT marker / connector."""
    marker = _marker_on_net(graph, start_net)
    if marker:
        return "direct", [], marker, "Feed net is the antenna marker.", "ok"

    parts: list[str] = []
    kinds: list[str] = []
    visited_nets = {start_net}
    frontier = [start_net]
    found_marker: dict | None = None
    found_connector = False

    for _ in range(4):
        next_frontier: list[str] = []
        for net in frontier:
            for cref in _passives_on_net(graph, net):
                if cref in parts:
                    continue
                other = graph.components[cref]
                ctype = other.component_type
                if ctype == ComponentType.CONNECTOR:
                    found_connector = True
                    parts.append(cref)
                    continue
                if cref.upper().startswith("ANT"):
                    found_marker = {"ref": cref, "net": net, "kind": "footprint"}
                    parts.append(cref)
                    continue
                if ctype not in (
                    ComponentType.RESISTOR,
                    ComponentType.CAPACITOR,
                    ComponentType.INDUCTOR,
                ):
                    continue
                parts.append(cref)
                if ctype == ComponentType.INDUCTOR:
                    kinds.append("L")
                elif ctype == ComponentType.CAPACITOR:
                    kinds.append("C")
                elif ctype == ComponentType.RESISTOR:
                    kinds.append("R")
                for n2 in other.pins.values():
                    if not n2 or n2 in visited_nets:
                        continue
                    visited_nets.add(n2)
                    next_frontier.append(n2)
                    m = _marker_on_net(graph, n2)
                    if m:
                        found_marker = m
        frontier = next_frontier
        if found_marker or (found_connector and not frontier):
            break

    if found_marker or found_connector:
        topo = _topo_from_kinds(kinds)
        who = found_marker.get("ref") if found_marker else "connector"
        return topo, parts, found_marker, f"Path to {who}: {topo}.", "ok"

    if parts:
        return (
            "unknown",
            parts,
            None,
            "Passives on RF net but no ANT* / ANT_FEED / connector reached.",
            "warning",
        )
    return (
        "missing",
        [],
        None,
        "No matching network found between RF pin and antenna marker.",
        "warning",
    )


def _topo_from_kinds(kinds: list[str]) -> Topology:
    s = "".join(kinds)
    if not s:
        return "direct"
    if s in ("L",):
        return "series_L"
    if s in ("LC", "CL"):
        return "LC"
    if s.count("C") >= 2 and "L" in s:
        return "pi"
    if s.count("L") >= 2 and "C" in s:
        return "T"
    if "L" in s and "C" in s:
        return "LC"
    if "L" in s:
        return "series_L"
    return "unknown"


def _passives_on_net(graph: DesignGraph, net: str) -> list[str]:
    out: list[str] = []
    net_obj = graph.nets.get(net)
    if not net_obj:
        return out
    for pc in net_obj.pins:
        cref = pc.component_ref
        comp = graph.components.get(cref)
        if not comp or comp.component_type == ComponentType.IC:
            continue
        out.append(cref)
    return sorted(set(out))


def _marker_on_net(graph: DesignGraph, net: str) -> dict | None:
    if _FEED_NET_RE.match(net or ""):
        return {"ref": None, "net": net, "kind": "net"}
    # Dedicated join alias only when an ANT* part sits on the net.
    for cref in _passives_on_net(graph, net):
        if cref.upper().startswith("ANT"):
            return {"ref": cref, "net": net, "kind": "footprint"}
        comp = graph.components[cref]
        if comp.component_type == ComponentType.CONNECTOR and (
            cref.upper().startswith("ANT")
            or _ANT_PIN_RE.search((comp.value or "") + cref)
        ):
            return {"ref": cref, "net": net, "kind": "connector"}
    return None


def _find_marker(graph: DesignGraph, layout: LayoutGraph | None) -> dict | None:
    # Prefer layout footprints ANT*
    if layout:
        for ref, fp in sorted(layout.footprints.items()):
            if ref.upper().startswith("ANT"):
                net = next((p.net for p in fp.pads if p.net), None)
                return {
                    "ref": ref,
                    "net": net,
                    "kind": "footprint",
                    "x": fp.x,
                    "y": fp.y,
                    "layer": fp.layer,
                }
        for net_name in layout.nets:
            if _FEED_NET_RE.match(net_name) or net_name.upper() == "RF_ANT":
                # RF_ANT as board join only if ANT* footprint uses it
                if net_name.upper() == "RF_ANT":
                    if not any(
                        r.upper().startswith("ANT")
                        for r, fp in layout.footprints.items()
                        if any(p.net == net_name for p in fp.pads)
                    ):
                        continue
                return {"ref": None, "net": net_name, "kind": "net"}

    for ref, comp in sorted(graph.components.items()):
        if ref.upper().startswith("ANT"):
            nets = [n for n in comp.pins.values() if n]
            return {
                "ref": ref,
                "net": nets[0] if nets else None,
                "kind": "footprint",
            }
        for net in comp.pins.values():
            if net and _FEED_NET_RE.match(net):
                return {"ref": ref if comp.component_type != ComponentType.IC else None,
                        "net": net, "kind": "net"}
    return None


def _find_antenna_zone(layout: LayoutGraph | None) -> AntennaZoneInfo | None:
    if not layout:
        return None
    for z in layout.zones:
        if not _ZONE_NET_RE.search(z.net or ""):
            continue
        bbox, area = _outline_metrics(z.outlines)
        return AntennaZoneInfo(
            net=z.net,
            layer=z.layer,
            bbox_mm=bbox,
            area_mm2=area,
        )
    return None


def _outline_metrics(
    outlines: list[list[tuple[float, float]]],
) -> tuple[tuple[float, float, float, float] | None, float | None]:
    pts: list[tuple[float, float]] = []
    for ring in outlines:
        pts.extend(ring)
    if len(pts) < 3:
        return None, None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    bbox = (min(xs), min(ys), max(xs), max(ys))
    # Shoelace on first ring only
    ring = outlines[0]
    area = 0.0
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % len(ring)]
        area += x1 * y2 - x2 * y1
    return bbox, abs(area) / 2.0


def _resolve_stackup(
    layout: LayoutGraph | None,
    *,
    h_mm: float | None,
    er: float | None,
    t_mm: float | None,
) -> tuple[float, float, float] | None:
    if h_mm and er and h_mm > 0 and er > 0:
        return float(h_mm), float(er), float(t_mm or 0.035)
    if not layout or not layout.stackup or not layout.stackup.dielectrics:
        return None
    d = layout.stackup.dielectrics[0]
    if d.height_mm <= 0 or d.er <= 0:
        return None
    t = layout.stackup.copper_thickness_mm
    return float(d.height_mm), float(d.er), float(t if t and t > 0 else 0.035)


def _z0_index(impedance_nets: dict | None) -> dict[str, dict]:
    if not impedance_nets:
        return {}
    rows = impedance_nets.get("nets") or []
    out: dict[str, dict] = {}
    for row in rows:
        name = row.get("net_name") or row.get("net")
        if name:
            out[str(name)] = row
    return out
