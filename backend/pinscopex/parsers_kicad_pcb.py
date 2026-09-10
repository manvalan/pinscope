"""KiCad `.kicad_pcb` ingest — footprints, pads, nets, segments, vias.

No SI/DRC. Schema validation stays complete without this file.
"""

from __future__ import annotations

from pathlib import Path

from backend.pinscopex.models import (
    LayoutFootprint,
    LayoutGraph,
    LayoutPad,
    LayoutSegment,
    LayoutVia,
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


def parse_kicad_pcb(path: str | Path) -> LayoutGraph:
    p = Path(path)
    tree = _parse_sexp(p.read_text(encoding="utf-8", errors="replace"))
    if _tag(tree) != "kicad_pcb":
        raise ValueError(f"Expected kicad_pcb, got {_tag(tree)!r}")

    nets: dict[str, int] = {}
    footprints: dict[str, LayoutFootprint] = {}
    segments: list[LayoutSegment] = []
    vias: list[LayoutVia] = []

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
            )
            continue
        if tag == "segment":
            net_el = _kid(node, "net")
            net_name = ""
            if net_el and len(net_el) >= 2:
                code = int(_fnum(net_el[1]))
                net_name = next((n for n, c in nets.items() if c == code), str(code))
            segments.append(LayoutSegment(
                start=_xy(node, "start"),
                end=_xy(node, "end"),
                width=_fnum(_val(node, "width") or 0),
                layer=_val(node, "layer"),
                net=net_name,
            ))
            continue
        if tag == "via":
            net_el = _kid(node, "net")
            net_name = ""
            if net_el and len(net_el) >= 2:
                code = int(_fnum(net_el[1]))
                net_name = next((n for n, c in nets.items() if c == code), str(code))
            vx, vy, _ = _at(node)
            vias.append(LayoutVia(x=vx, y=vy, net=net_name))

    return LayoutGraph(
        nets=nets,
        footprints=footprints,
        segments=segments,
        vias=vias,
    )
