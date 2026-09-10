"""KiCad netlist (XML / s-expression) and single-sheet ``.kicad_sch`` parser.

Yields the same ``(parts, nets)`` shape as PADS/EDIF so graph build is format-agnostic.
``.kicad_sch`` uses embedded ``lib_symbols`` plus wires/labels; hierarchical
sheets in other files are not followed (export a netlist for those).
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterator

_MPN_FIELD_NAMES = {
    "mpn", "manufacturer part number", "manufacturer_part_number",
    "manf#", "part number", "partnumber", "p/n",
}

# ---------------------------------------------------------------------------
# S-expression
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> Iterator[str]:
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == "(" or c == ")":
            yield c
            i += 1
            continue
        if c == '"':
            j = i + 1
            buf: list[str] = []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                else:
                    buf.append(text[j])
                    j += 1
            yield '"' + "".join(buf)
            i = j + 1
            continue
        j = i
        while j < n and not text[j].isspace() and text[j] not in "()":
            j += 1
        yield text[i:j]
        i = j


def _parse_sexp(text: str) -> Any:
    tokens = list(_tokenize(text))
    it = iter(tokens)

    def form() -> Any:
        out: list[Any] = []
        for tok in it:
            if tok == "(":
                out.append(form())
            elif tok == ")":
                return out
            elif tok.startswith('"'):
                out.append(tok[1:])
            else:
                out.append(tok)
        return out

    first = next(it, None)
    if first != "(":
        raise ValueError("KiCad file is not an s-expression")
    return form()


def _tag(node: Any) -> str:
    if isinstance(node, list) and node:
        return str(node[0])
    return ""


def _kids(node: Any, name: str) -> list[list]:
    if not isinstance(node, list):
        return []
    return [x for x in node[1:] if isinstance(x, list) and x and x[0] == name]


def _kid(node: Any, name: str) -> list | None:
    found = _kids(node, name)
    return found[0] if found else None


def _val(node: Any, name: str) -> str:
    k = _kid(node, name)
    if not k or len(k) < 2:
        return ""
    return str(k[1])


def _unquote_attr(node: ET.Element, key: str) -> str:
    return (node.get(key) or "").strip()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# ---------------------------------------------------------------------------
# XML netlist (File → Export → Netlist)
# ---------------------------------------------------------------------------


def _iter_xml(root: ET.Element, name: str) -> Iterator[ET.Element]:
    for el in root.iter():
        if _local(el.tag) == name:
            yield el


def parse_kicad_xml_netlist(path: str | Path) -> tuple[dict[str, str], dict[str, list[tuple[str, str]]], dict[str, dict]]:
    tree = ET.parse(path)
    root = tree.getroot()
    parts: dict[str, str] = {}
    fields: dict[str, dict] = {}
    for comp in _iter_xml(root, "comp"):
        ref = _unquote_attr(comp, "ref")
        if not ref:
            continue
        value = ""
        footprint = ""
        mpn = None
        lcsc = None
        for child in list(comp):
            loc = _local(child.tag)
            if loc == "value":
                value = (child.text or "").strip()
            elif loc == "footprint":
                footprint = (child.text or "").strip()
            elif loc == "fields":
                for field in child:
                    if _local(field.tag) != "field":
                        continue
                    fname = (field.get("name") or "").strip().lower()
                    fval = (field.text or "").strip()
                    if fname in _MPN_FIELD_NAMES and fval:
                        mpn = fval
                    elif fname == "lcsc" and fval:
                        lcsc = fval
            elif loc == "property":
                pname = (child.get("name") or "").strip().lower()
                pval = (child.get("value") or child.text or "").strip()
                if pname in _MPN_FIELD_NAMES and pval:
                    mpn = pval
                elif pname == "lcsc" and pval:
                    lcsc = pval
        parts[ref] = footprint
        fields[ref] = {"value": value, "footprint": footprint, "mpn": mpn, "lcsc": lcsc}
    nets: dict[str, list[tuple[str, str]]] = {}
    for net in _iter_xml(root, "net"):
        name = _unquote_attr(net, "name") or f"Net-{_unquote_attr(net, 'code')}"
        pins: list[tuple[str, str]] = []
        for node in net:
            if _local(node.tag) != "node":
                continue
            ref = _unquote_attr(node, "ref")
            pin = _unquote_attr(node, "pin")
            if ref and pin:
                pins.append((ref, pin))
        if name:
            nets[name] = pins
    return parts, nets, fields


# ---------------------------------------------------------------------------
# S-expression netlist (kicad-cli sch export netlist)
# ---------------------------------------------------------------------------


def parse_kicad_sexp_netlist(tree: Any) -> tuple[dict[str, str], dict[str, list[tuple[str, str]]], dict[str, dict]]:
    parts: dict[str, str] = {}
    fields: dict[str, dict] = {}
    comps = _kid(tree, "components") or []
    for comp in comps[1:]:
        if _tag(comp) != "comp":
            continue
        ref = _val(comp, "ref")
        if not ref:
            continue
        value = _val(comp, "value")
        footprint = _val(comp, "footprint")
        mpn = None
        lcsc = None
        for field in _kids(_kid(comp, "fields") or [], "field"):
            fname = ""
            fval = ""
            name_el = _kid(field, "name")
            if name_el and len(name_el) >= 2:
                fname = str(name_el[1]).lower()
            strs = [str(x) for x in field[1:] if not isinstance(x, list)]
            if strs:
                fval = strs[-1]
            if fname in _MPN_FIELD_NAMES and fval:
                mpn = fval
            elif fname == "lcsc" and fval:
                lcsc = fval
        parts[ref] = footprint
        fields[ref] = {"value": value, "footprint": footprint, "mpn": mpn, "lcsc": lcsc}

    nets: dict[str, list[tuple[str, str]]] = {}
    nets_el = _kid(tree, "nets") or []
    for net in nets_el[1:]:
        if _tag(net) != "net":
            continue
        name = _val(net, "name") or f"Net-{_val(net, 'code')}"
        pins: list[tuple[str, str]] = []
        for node in _kids(net, "node"):
            ref = _val(node, "ref")
            pin = _val(node, "pin")
            if ref and pin:
                pins.append((ref, pin))
        if name:
            nets[name] = pins
    return parts, nets, fields


# ---------------------------------------------------------------------------
# Single-sheet .kicad_sch (embedded lib_symbols + wires)
# ---------------------------------------------------------------------------


def _fnum(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _at(node: Any) -> tuple[float, float, float]:
    k = _kid(node, "at")
    if not k or len(k) < 3:
        return 0.0, 0.0, 0.0
    rot = _fnum(k[3]) if len(k) > 3 else 0.0
    return _fnum(k[1]), _fnum(k[2]), rot


def _snap(x: float, y: float) -> tuple[int, int]:
    return round(x * 1000), round(y * 1000)


def _rotate(px: float, py: float, deg: float) -> tuple[float, float]:
    r = deg % 360.0
    rad = math.radians(r)
    c, s = math.cos(rad), math.sin(rad)
    return px * c + py * s, -px * s + py * c


def _lib_pins(sym: Any) -> dict[tuple[int, str], tuple[float, float]]:
    """(unit, pin_number) -> (x, y) in symbol space. unit 0 = common."""
    out: dict[tuple[int, str], tuple[float, float]] = {}

    def walk(node: Any, unit: int) -> None:
        if not isinstance(node, list) or not node:
            return
        if node[0] == "symbol" and len(node) > 1 and isinstance(node[1], str):
            # nested unit symbol Device:R_1_1 → unit 1
            m = re.search(r"_(\d+)_(\d+)$", str(node[1]))
            u = int(m.group(1)) if m else unit
            for ch in node[1:]:
                walk(ch, u)
            return
        if node[0] == "pin":
            ax, ay, _ = _at(node)
            num = _val(node, "number") or ""
            if not num and len(node) > 1:
                num = str(node[1])
            if num:
                out[(unit, num)] = (ax, ay)
                out[(0, num)] = (ax, ay)
            return
        for ch in node[1:]:
            if isinstance(ch, list):
                walk(ch, unit)

    walk(sym, 0)
    return out


class _DSU:
    def __init__(self) -> None:
        self.p: dict[tuple[int, int], tuple[int, int]] = {}

    def add(self, pt: tuple[int, int]) -> None:
        self.p.setdefault(pt, pt)

    def find(self, a: tuple[int, int]) -> tuple[int, int]:
        self.add(a)
        if self.p[a] != a:
            self.p[a] = self.find(self.p[a])
        return self.p[a]

    def union(self, a: tuple[int, int], b: tuple[int, int]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def parse_kicad_sch(tree: Any) -> tuple[dict[str, str], dict[str, list[tuple[str, str]]], dict[str, dict]]:
    lib_pins: dict[str, dict[tuple[int, str], tuple[float, float]]] = {}
    for sym in _kids(_kid(tree, "lib_symbols") or [], "symbol"):
        lid = str(sym[1]) if len(sym) > 1 else ""
        if lid:
            lib_pins[lid] = _lib_pins(sym)

    parts: dict[str, str] = {}
    fields: dict[str, dict] = {}
    pin_at: dict[tuple[str, str], tuple[int, int]] = {}
    dsu = _DSU()
    labels: dict[tuple[int, int], str] = {}
    power_pts: list[tuple[tuple[int, int], str]] = []

    def prop(sym: Any, key: str) -> str:
        for p in _kids(sym, "property"):
            if len(p) >= 3 and str(p[1]) == key:
                return str(p[2])
        return ""

    for sym in _kids(tree, "symbol"):
        lib_id = _val(sym, "lib_id")
        ix, iy, rot = _at(sym)
        unit = int(_fnum(_val(sym, "unit") or "1") or 1)
        mirror = bool(_kid(sym, "mirror"))
        ref = prop(sym, "Reference")
        if ref.startswith("#"):
            # power flag / graphic
            val = prop(sym, "Value") or lib_id.rsplit(":", 1)[-1]
            lp = lib_pins.get(lib_id, {})
            xy = lp.get((unit, "1")) or lp.get((0, "1")) or (0.0, 0.0)
            px, py = _rotate(xy[0], xy[1], rot)
            if mirror:
                px = -px
            pt = _snap(ix + px, iy + py)
            dsu.add(pt)
            if val:
                power_pts.append((pt, val))
            continue
        if not ref:
            continue
        value = prop(sym, "Value")
        footprint = prop(sym, "Footprint")
        mpn = None
        lcsc = None
        for p in _kids(sym, "property"):
            if len(p) < 3:
                continue
            n = str(p[1]).strip().lower()
            v = str(p[2]).strip()
            if n in _MPN_FIELD_NAMES and v:
                mpn = v
            elif n == "lcsc" and v:
                lcsc = v
        parts[ref] = footprint
        fields[ref] = {"value": value, "footprint": footprint, "mpn": mpn, "lcsc": lcsc}
        lp = lib_pins.get(lib_id, {})
        for pin_el in _kids(sym, "pin"):
            num = str(pin_el[1]) if len(pin_el) > 1 else ""
            if not num:
                continue
            xy = lp.get((unit, num)) or lp.get((0, num)) or (0.0, 0.0)
            px, py = _rotate(xy[0], xy[1], rot)
            if mirror:
                px = -px
            pt = _snap(ix + px, iy + py)
            pin_at[(ref, num)] = pt
            dsu.add(pt)

    def collect_pts(node: Any) -> None:
        if not isinstance(node, list) or not node:
            return
        tag = node[0]
        if tag == "wire":
            pts = _kid(node, "pts")
            coords: list[tuple[int, int]] = []
            if pts:
                for xy in _kids(pts, "xy"):
                    if len(xy) >= 3:
                        pt = _snap(_fnum(xy[1]), _fnum(xy[2]))
                        dsu.add(pt)
                        coords.append(pt)
            for a, b in zip(coords, coords[1:]):
                dsu.union(a, b)
            return
        if tag in {"label", "global_label", "hierarchical_label"}:
            name = str(node[1]) if len(node) > 1 else ""
            x, y, _ = _at(node)
            pt = _snap(x, y)
            dsu.add(pt)
            if name:
                labels[pt] = name
            return
        if tag == "junction":
            x, y, _ = _at(node)
            dsu.add(_snap(x, y))
            return
        for ch in node[1:]:
            if isinstance(ch, list):
                collect_pts(ch)

    collect_pts(tree)

    for pt, name in labels.items():
        dsu.add(pt)
    for pt, _name in power_pts:
        dsu.add(pt)

    # Merge labels/power onto coinciding pin/wire points (already same snap keys).
    nets: dict[str, list[tuple[str, str]]] = {}
    root_name: dict[tuple[int, int], str] = {}
    for pt, name in labels.items():
        root_name[dsu.find(pt)] = name
    for pt, name in power_pts:
        root_name.setdefault(dsu.find(pt), name)

    grouped: dict[tuple[int, int], list[tuple[str, str]]] = {}
    for (ref, pin), pt in pin_at.items():
        grouped.setdefault(dsu.find(pt), []).append((ref, pin))

    used_names: set[str] = set()
    for root, pins in grouped.items():
        name = root_name.get(root)
        if not name:
            ref0, pin0 = pins[0]
            name = f"Net-({ref0}-Pad{pin0})"
        while name in used_names:
            name = name + "_"
        used_names.add(name)
        nets[name] = pins

    return parts, nets, fields


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------

_fields_cache: dict[str, dict[str, dict]] = {}


def parse_kicad(
    path: str | Path,
) -> tuple[dict[str, str], dict[str, list[tuple[str, str]]], dict[str, dict]]:
    p = Path(path)
    raw = p.read_bytes()
    head = raw[:256].decode("utf-8", errors="replace").lstrip("\ufeff").lstrip()
    if head.startswith("<") or head.startswith("<?xml"):
        parts, nets, fields = parse_kicad_xml_netlist(p)
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
        tree = _parse_sexp(text)
        tag = _tag(tree)
        if tag == "kicad_sch":
            parts, nets, fields = parse_kicad_sch(tree)
        elif tag == "export":
            parts, nets, fields = parse_kicad_sexp_netlist(tree)
        else:
            raise ValueError(f"Unsupported KiCad s-expression root {tag!r}")
    if not parts:
        raise ValueError("No components found in KiCad file")
    if not nets:
        raise ValueError(
            "No nets found. For a multi-sheet schematic, export a netlist "
            "(File → Export → Netlist) instead of uploading .kicad_sch."
        )
    _fields_cache[str(p.resolve())] = fields
    return parts, nets, fields


def kicad_part_fields(path: str | Path) -> dict[str, dict]:
    key = str(Path(path).resolve())
    if key not in _fields_cache:
        parse_kicad(path)
    return _fields_cache.get(key, {})
