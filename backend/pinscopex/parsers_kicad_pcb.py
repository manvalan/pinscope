"""KiCad `.kicad_pcb` ingest — footprints, pads, nets, segments, vias.

No SI/DRC. Schema validation stays complete without this file.
"""

from __future__ import annotations

from pathlib import Path

from backend.pinscopex.models import (
    LayoutDielectric,
    LayoutFootprint,
    LayoutGraph,
    LayoutPad,
    LayoutSegment,
    LayoutStackup,
    LayoutVia,
    LayoutZone,
)
from backend.pinscopex.parsers_kicad import (
    _at,
    _fnum,
    _kid,
    _kids,
    _parse_sexp,
    _rotate,
    _tag,
    _val,
)


def _xy(node: object, name: str) -> tuple[float, float]:
    k = _kid(node, name)
    if not k or len(k) < 3:
        return 0.0, 0.0
    return _fnum(k[1]), _fnum(k[2])


def _prop(node: object, key: str) -> str:
    for p in _kids(node, "property"):
        if len(p) >= 3 and str(p[1]) == key:
            return str(p[2])
    return ""


def _pad_net(pad: object) -> str:
    n = _kid(pad, "net")
    if n and len(n) >= 3:
        return str(n[2])
    return ""


def _net_name(node: object, nets: dict[str, int]) -> str:
    n = _kid(node, "net")
    if not n or len(n) < 2:
        named = _val(node, "net_name")
        return named
    if len(n) >= 3:
        return str(n[2])
    try:
        code = int(_fnum(n[1]))
    except (TypeError, ValueError):
        return ""
    return next((name for name, c in nets.items() if c == code), str(code))


def _layer_type(node: object) -> str:
    return str(_val(node, "type") or "").lower()


def _parse_stackup(tree: object) -> LayoutStackup | None:
    setup = _kid(tree, "setup")
    if not setup:
        return None
    stack = _kid(setup, "stackup")
    if not stack:
        return None
    copper: list[str] = []
    dielectrics: list[LayoutDielectric] = []
    thicknesses: list[float] = []
    for layer in _kids(stack, "layer"):
        name = str(layer[1]) if len(layer) > 1 and not isinstance(layer[1], list) else ""
        kind = _layer_type(layer)
        thick = _kid(layer, "thickness")
        height = _fnum(thick[1]) if thick and len(thick) > 1 else None
        if kind == "copper" or name.endswith(".Cu"):
            if name:
                copper.append(name)
            if height is not None and height > 0:
                thicknesses.append(height)
            continue
        if kind in {"core", "prepreg", "dielectric"} or name.lower().startswith("dielectric"):
            er_el = _kid(layer, "epsilon_r")
            if er_el is None:
                er_el = _kid(layer, "epsilonr")
            er = _fnum(er_el[1]) if er_el and len(er_el) > 1 else None
            if er is None or height is None or er <= 0 or height <= 0:
                continue
            dielectrics.append(LayoutDielectric(
                name=name or f"dielectric_{len(dielectrics)}",
                er=er,
                height_mm=height,
            ))
    if len(copper) < 2 or len(dielectrics) != len(copper) - 1:
        return None
    t = thicknesses[0] if thicknesses else None
    return LayoutStackup(
        copper_layers=copper,
        dielectrics=dielectrics,
        copper_thickness_mm=t,
    )


def _pts_xy(node: object) -> list[tuple[float, float]]:
    pts_el = _kid(node, "pts")
    if not pts_el:
        return []
    out: list[tuple[float, float]] = []
    for xy in pts_el[1:]:
        if isinstance(xy, list) and xy and xy[0] == "xy" and len(xy) >= 3:
            out.append((_fnum(xy[1]), _fnum(xy[2])))
    return out


def _parse_zone(node: object, nets: dict[str, int]) -> list[LayoutZone]:
    net = str(_val(node, "net_name") or "") or _net_name(node, nets)
    zones: list[LayoutZone] = []
    for poly in _kids(node, "filled_polygon"):
        layer = _val(poly, "layer")
        pts = _pts_xy(poly)
        if layer and len(pts) >= 3:
            zones.append(LayoutZone(net=net, layer=layer, outlines=[pts]))
    return zones


def _is_crtyd(layer: str) -> bool:
    return str(layer).endswith("CrtYd")


def _abs(fx: float, fy: float, frot: float, lx: float, ly: float) -> tuple[float, float]:
    rx, ry = _rotate(lx, ly, frot)
    return fx + rx, fy + ry


def _courtyard_pts(node: object, fx: float, fy: float, frot: float) -> list[tuple[float, float]]:
    """Courtyard vertices from the PCB file. Empty if KiCad has no CrtYd."""
    pts: list[tuple[float, float]] = []
    for poly in _kids(node, "fp_poly"):
        if not _is_crtyd(_val(poly, "layer")):
            continue
        pts_el = _kid(poly, "pts")
        if not pts_el:
            continue
        for xy in pts_el[1:]:
            if isinstance(xy, list) and xy and xy[0] == "xy" and len(xy) >= 3:
                pts.append(_abs(fx, fy, frot, _fnum(xy[1]), _fnum(xy[2])))
        if pts:
            return pts
    for rect in _kids(node, "fp_rect"):
        if not _is_crtyd(_val(rect, "layer")):
            continue
        sx, sy = _xy(rect, "start")
        ex, ey = _xy(rect, "end")
        return [
            _abs(fx, fy, frot, sx, sy),
            _abs(fx, fy, frot, ex, sy),
            _abs(fx, fy, frot, ex, ey),
            _abs(fx, fy, frot, sx, ey),
        ]
    for line in _kids(node, "fp_line"):
        if not _is_crtyd(_val(line, "layer")):
            continue
        sx, sy = _xy(line, "start")
        ex, ey = _xy(line, "end")
        a = _abs(fx, fy, frot, sx, sy)
        b = _abs(fx, fy, frot, ex, ey)
        if not pts or pts[-1] != a:
            pts.append(a)
        if pts[-1] != b:
            pts.append(b)
    return pts


def parse_kicad_pcb(path: str | Path) -> LayoutGraph:
    p = Path(path)
    tree = _parse_sexp(p.read_text(encoding="utf-8", errors="replace"))
    if _tag(tree) != "kicad_pcb":
        raise ValueError(f"Expected kicad_pcb, got {_tag(tree)!r}")

    nets: dict[str, int] = {}
    footprints: dict[str, LayoutFootprint] = {}
    segments: list[LayoutSegment] = []
    vias: list[LayoutVia] = []
    zones: list[LayoutZone] = []

    for node in tree[1:]:
        if not isinstance(node, list) or not node:
            continue
        tag = _tag(node)
        if tag == "net" and len(node) >= 3 and not any(isinstance(x, list) and x and x[0] == "node" for x in node[1:]):
            try:
                code = int(_fnum(node[1]))
            except (TypeError, ValueError):
                continue
            name = str(node[2])
            if name:
                nets[name] = code
            continue
        if tag in {"footprint", "module"}:
            fp_name = str(node[1]) if len(node) > 1 and not isinstance(node[1], list) else ""
            fx, fy, frot = _at(node)
            layer = _val(node, "layer")
            ref = _prop(node, "Reference")
            if not ref or ref.startswith("#"):
                continue
            pads: list[LayoutPad] = []
            for pad in _kids(node, "pad"):
                num = str(pad[1]) if len(pad) > 1 else ""
                if not num:
                    continue
                px, py, _ = _at(pad)
                rx, ry = _rotate(px, py, frot)
                pads.append(LayoutPad(
                    number=num,
                    x=fx + rx,
                    y=fy + ry,
                    net=_pad_net(pad),
                ))
            footprints[ref] = LayoutFootprint(
                reference=ref,
                footprint=fp_name,
                x=fx,
                y=fy,
                layer=layer,
                pads=pads,
                courtyard=_courtyard_pts(node, fx, fy, frot),
            )
            continue
        if tag == "segment":
            segments.append(LayoutSegment(
                start=_xy(node, "start"),
                end=_xy(node, "end"),
                width=_fnum(_val(node, "width") or 0),
                layer=_val(node, "layer"),
                net=_net_name(node, nets),
            ))
            continue
        if tag == "via":
            drill_el = _kid(node, "drill")
            drill = _fnum(drill_el[1]) if drill_el and len(drill_el) > 1 else None
            vx, vy, _ = _at(node)
            vias.append(LayoutVia(x=vx, y=vy, net=_net_name(node, nets), drill=drill))
            continue
        if tag == "zone":
            zones.extend(_parse_zone(node, nets))
            continue

    return LayoutGraph(
        nets=nets,
        footprints=footprints,
        segments=segments,
        vias=vias,
        stackup=_parse_stackup(tree),
        zones=zones,
    )
