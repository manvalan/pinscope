"""Parser for EDIF 2.0.0 netlists (Siemens xDX Designer flavor).

Yields the same ``(parts, nets)`` shape as :func:`parsers.parse_netlist` so
downstream graph building doesn't care which netlist format the user uploaded.

Tested against xDX Designer's exporter. Other EDIF 2.0.0 exporters (OrCAD,
Altium, KiCad, Eagle) will *probably* parse — the s-expression handling is
generic and the EDIF instance/cell/net structure is standardised — but they
have not been verified against real files.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator


# ---------------------------------------------------------------------------
# Tokenizer + s-expression parser
# ---------------------------------------------------------------------------


class _Str(str):
    """Marker subclass so quoted-string tokens are distinguishable from atoms.

    Both atoms (e.g. ``viewRef``, ``&0441I3151``) and string values
    (e.g. ``"U3"``, ``"GROUND"``) end up as Python ``str`` in the parsed
    tree. EDIF rarely needs that distinction — string equality compares the
    same way — but the marker is here in case future logic does.
    """


def _tokenize(text: str) -> Iterator[object]:
    """Yield tokens: ``'('``, ``')'``, atom :class:`str`, or quoted :class:`_Str`."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        if c == ";":
            # EDIF doesn't really use comments, but tolerate them just in case
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c in "()":
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
            yield _Str("".join(buf))
            i = j + 1
            continue
        j = i
        while j < n and not text[j].isspace() and text[j] not in '()"':
            j += 1
        yield text[i:j]
        i = j


def _parse_sexp(tokens: list[object]) -> list:
    """Build a nested list tree. Atoms / strings remain as ``str`` / ``_Str``."""
    it = iter(tokens)

    def parse_form() -> list:
        result: list = []
        for tok in it:
            if tok == "(":
                result.append(parse_form())
            elif tok == ")":
                return result
            else:
                result.append(tok)
        return result  # unterminated at EOF — return what we have

    top: list = []
    for tok in it:
        if tok == "(":
            top.append(parse_form())
        elif tok == ")":
            raise ValueError("EDIF: unexpected ')' at top level")
        else:
            top.append(tok)
    return top


# ---------------------------------------------------------------------------
# Tree walkers
# ---------------------------------------------------------------------------


def _walk(node: object, head: str) -> Iterator[list]:
    """Yield every nested list whose first element equals ``head``."""
    if not isinstance(node, list):
        return
    if node and isinstance(node[0], str) and node[0] == head:
        yield node
    for child in node:
        if isinstance(child, list):
            yield from _walk(child, head)


def _node_id(node: list) -> str | None:
    """Return the identifying atom of ``(<head> <id> ...)``.

    Handles ``(<head> (rename &INTERNAL "display") ...)`` by returning
    ``&INTERNAL`` — the form used elsewhere by ``cellRef`` / ``instanceRef``.
    """
    if len(node) < 2:
        return None
    second = node[1]
    if isinstance(second, list) and len(second) >= 2 and second[0] == "rename":
        return str(second[1])
    if isinstance(second, str):
        return str(second)
    return None


def _direct_property(node: list, prop_name: str) -> str | None:
    """Return the string value of a ``(property NAME (string "X") ...)`` child.

    Only looks at direct children of ``node`` — does not recurse into nested
    forms — so it can be called on an ``instance`` without picking up
    properties tucked inside ``portInstance`` blocks.
    """
    for child in node:
        if not (isinstance(child, list) and len(child) >= 2 and child[0] == "property"):
            continue
        name_node = child[1]
        if isinstance(name_node, list) and name_node and name_node[0] == "rename":
            actual = str(name_node[1]) if len(name_node) >= 2 else ""
        elif isinstance(name_node, str):
            actual = str(name_node)
        else:
            continue
        if actual != prop_name:
            continue
        for elem in child[2:]:
            if isinstance(elem, list) and len(elem) >= 2 and elem[0] == "string":
                return str(elem[1])
    return None


# ---------------------------------------------------------------------------
# Stage extractors
# ---------------------------------------------------------------------------


def _build_cell_library(tree: list) -> dict[tuple[str, str], dict[str, str | None]]:
    """Build ``(library_name, cell_id) -> {port_name: pin_type}``.

    ``pin_type`` is ``"GROUND"`` (or any other ``Pin_Type`` property value) when
    the cell tagged the port; ``None`` when no Pin_Type property is present.
    Used to detect which nets are ground.
    """
    cells: dict[tuple[str, str], dict[str, str | None]] = {}
    for lib in _walk(tree, "library"):
        if len(lib) < 2:
            continue
        lib_name = str(lib[1])
        for cell in _walk(lib, "cell"):
            cell_id = _node_id(cell)
            if not cell_id:
                continue
            port_map: dict[str, str | None] = {}
            for port in _walk(cell, "port"):
                if len(port) < 2:
                    continue
                port_name = str(port[1])
                port_map[port_name] = _direct_property(port, "Pin_Type")
            cells[(lib_name, cell_id)] = port_map
    return cells


def _find_cell_ref(node: list) -> tuple[str, str] | None:
    """From an ``(instance ...)`` form, return ``(library_name, cell_id)`` from
    its ``(viewRef VIEW (cellRef CELL (libraryRef LIB)))`` triple."""
    for child in node:
        if not (isinstance(child, list) and child and child[0] == "viewRef"):
            continue
        for sub in child[1:]:
            if isinstance(sub, list) and len(sub) >= 2 and sub[0] == "cellRef":
                cell_id = str(sub[1])
                lib_name = ""
                for sub2 in sub[2:]:
                    if isinstance(sub2, list) and len(sub2) >= 2 and sub2[0] == "libraryRef":
                        lib_name = str(sub2[1])
                        break
                return (lib_name, cell_id)
    return None


_SUBDESIGN_PREFIX = re.compile(r"^(&\d+)[IN]\d+")


def _subdesign_id(internal_id: str | None) -> str | None:
    """Extract the sub-design prefix from an EDIF instance or net ID.

    Siemens xDX Designer emits internal IDs like ``&0441I2234`` (instance) or
    ``&0441N2250`` (net), where ``&0441`` identifies the sub-design /
    schematic view the symbol belongs to. Different sub-designs in one file
    get different numeric prefixes; back-annotation, contents, and viewMap
    all reuse the same prefix per design.

    Returns ``None`` when the ID doesn't match the prefix scheme (bare-named
    cells, named nets like ``+5V``, or exports from non-xDX tools). The
    parser treats ``None`` as "shared / no sub-design" and includes those
    forms in every selection.
    """
    if not internal_id:
        return None
    m = _SUBDESIGN_PREFIX.match(internal_id)
    return m.group(1) if m else None


def _build_instance_map(tree: list) -> dict[str, dict]:
    """Walk every ``(instance ...)`` form. Skip back-annotation refs in viewMap.

    Each entry: ``{cell_ref, port_pins, inline_designator, footprint, subdesign_id}``.
    """
    instances: dict[str, dict] = {}
    for inst in _walk(tree, "instance"):
        inst_id = _node_id(inst)
        if not inst_id:
            continue

        cell_ref = _find_cell_ref(inst)

        port_pins: dict[str, str] = {}
        inline_des: str | None = None
        for child in inst:
            if not isinstance(child, list) or not child:
                continue
            if child[0] == "portInstance" and len(child) >= 2:
                port_name = str(child[1])
                for sub in child[2:]:
                    if isinstance(sub, list) and len(sub) >= 2 and sub[0] == "designator":
                        port_pins[port_name] = str(sub[1])
                        break
            elif child[0] == "designator" and len(child) >= 2 and inline_des is None:
                inline_des = str(child[1])

        instances[inst_id] = {
            "cell_ref": cell_ref,
            "port_pins": port_pins,
            "inline_designator": inline_des,
            "footprint": _direct_property(inst, "Cell_Name") or "",
            "subdesign_id": _subdesign_id(inst_id),
        }
    return instances


def _build_back_annotation(tree: list) -> dict[str, str]:
    """``instance_id -> real_designator`` from ``viewMap.instanceBackAnnotate``."""
    annotations: dict[str, str] = {}
    for ann in _walk(tree, "instanceBackAnnotate"):
        inst_id: str | None = None
        des: str | None = None
        for child in ann[1:]:
            if not isinstance(child, list) or len(child) < 2:
                continue
            if child[0] == "instanceRef":
                inst_id = str(child[1])
            elif child[0] == "designator":
                des = str(child[1])
        if inst_id and des:
            annotations[inst_id] = des
    return annotations


def _is_template_designator(des: str) -> bool:
    """xDX exports unconfigured instances with templates like ``R?`` / ``U?``."""
    return des.endswith("?")


def _resolve_designators(
    instances: dict[str, dict], back_anno: dict[str, str]
) -> dict[str, str]:
    """For each instance, pick the real designator. Drop template-only ones."""
    resolved: dict[str, str] = {}
    for inst_id, inst in instances.items():
        inline = inst["inline_designator"]
        annotated = back_anno.get(inst_id)
        if inline and not _is_template_designator(inline):
            resolved[inst_id] = inline
        elif annotated and not _is_template_designator(annotated):
            resolved[inst_id] = annotated
        # else: unconfigured library symbol — skip
    return resolved


def _extract_nets(
    tree: list,
    instances: dict[str, dict],
    designators: dict[str, str],
    cell_lib: dict[tuple[str, str], dict[str, str | None]],
    include_subdesigns: set[str] | None = None,
) -> dict[str, list[tuple[str, str]]]:
    """Walk every ``(net ...)`` form. Rename ground-touching nets to ``GND``.

    When ``include_subdesigns`` is supplied, endpoints belonging to
    excluded sub-designs are dropped. A net is kept iff it has at least one
    surviving endpoint — bare-named nets (no sub-design prefix) survive as
    long as any of their referenced instances does.
    """
    nets: dict[str, list[tuple[str, str]]] = {}
    for net in _walk(tree, "net"):
        if len(net) < 2:
            continue
        name_node = net[1]
        if isinstance(name_node, list) and len(name_node) >= 3 and name_node[0] == "rename":
            net_name = str(name_node[2])
        elif isinstance(name_node, str):
            net_name = str(name_node)
        else:
            continue

        connections: list[tuple[str, str]] = []
        touches_ground = False
        for child in net[1:]:
            if not (isinstance(child, list) and child and child[0] == "joined"):
                continue
            for ref in child[1:]:
                if not (isinstance(ref, list) and len(ref) >= 2 and ref[0] == "portRef"):
                    continue
                port_name = str(ref[1])
                inst_id: str | None = None
                for sub in ref[2:]:
                    if isinstance(sub, list) and len(sub) >= 2 and sub[0] == "instanceRef":
                        inst_id = str(sub[1])
                        break
                if not inst_id or inst_id not in instances:
                    continue
                inst = instances[inst_id]
                if include_subdesigns is not None:
                    if inst["subdesign_id"] not in include_subdesigns:
                        continue
                pin = inst["port_pins"].get(port_name)
                des = designators.get(inst_id)
                if not pin or not des:
                    continue
                if inst["cell_ref"]:
                    port_map = cell_lib.get(inst["cell_ref"], {})
                    if port_map.get(port_name) == "GROUND":
                        touches_ground = True
                connections.append((des, pin))

        if not connections:
            continue
        final_name = "GND" if touches_ground else net_name
        nets.setdefault(final_name, []).extend(connections)
    return nets


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _parse_tree(path: str | Path) -> list:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return _parse_sexp(list(_tokenize(text)))


def parse_edif_netlist(
    path: str | Path,
    *,
    include_subdesigns: set[str] | None = None,
) -> tuple[dict[str, str], dict[str, list[tuple[str, str]]]]:
    """Parse a Siemens xDX Designer EDIF 2.0.0 netlist (``.edn``).

    Args:
        path: file to parse.
        include_subdesigns: when supplied, restrict the output to instances
            whose ``&NNNN`` sub-design prefix is in this set. Instances with
            no prefix (bare-named cells) are always kept. ``None`` (default)
            includes every sub-design — same behavior as before this flag
            existed.

    Returns:
        parts: ``{reference: footprint}`` (footprint from the instance's
            ``Cell_Name`` property — typically a package size like ``"0402"``)
        nets:  ``{net_name: [(component_ref, pin_number), ...]}``

    Ground nets are renamed to ``"GND"`` based on ``Pin_Type=GROUND`` port
    tags in the cell library; if no port tags ground (rare), net names stay
    as the EDIF-generated ``$NN…`` strings and downstream validation will
    surface the missing ground.
    """
    tree = _parse_tree(path)

    cell_lib = _build_cell_library(tree)
    instances = _build_instance_map(tree)
    back_anno = _build_back_annotation(tree)
    designators = _resolve_designators(instances, back_anno)

    if include_subdesigns is not None:
        # Drop excluded instances before nets are walked. Instances with
        # subdesign_id=None (bare-named, no prefix) are always kept — they're
        # shared between sub-designs in the xDX export and dropping them
        # would orphan otherwise-included nets.
        designators = {
            iid: des
            for iid, des in designators.items()
            if instances[iid]["subdesign_id"] is None
            or instances[iid]["subdesign_id"] in include_subdesigns
        }

    nets = _extract_nets(
        tree, instances, designators, cell_lib,
        include_subdesigns=include_subdesigns,
    )

    parts: dict[str, str] = {}
    for inst_id, des in designators.items():
        parts[des] = instances[inst_id]["footprint"]

    return parts, nets


def list_edif_subdesigns(path: str | Path) -> list[dict]:
    """Return one entry per sub-design found in the file.

    Each entry: ``{"id": "&0441", "instance_count": 21,
    "designators": ["C1", "C2", ...]}``. Sub-designs are identified by the
    ``&NNNN`` prefix on EDIF instance IDs; instances with no prefix (bare
    cells, rare in xDX exports) are bundled under ``"id": None`` and are
    always included regardless of the user's selection.

    Designators are sorted naturally (R1 before R10) within each sub-design;
    sub-designs themselves are sorted by their first BOM-style designator so
    output is deterministic across runs.
    """
    tree = _parse_tree(path)
    instances = _build_instance_map(tree)
    back_anno = _build_back_annotation(tree)
    designators = _resolve_designators(instances, back_anno)

    by_sub: dict[str | None, list[str]] = {}
    for iid, des in designators.items():
        sub = instances[iid]["subdesign_id"]
        by_sub.setdefault(sub, []).append(des)

    def _key(des: str) -> tuple:
        # Sort R1 before R10 — split on the first digit run.
        head = des.rstrip("0123456789")
        tail = des[len(head):]
        return (head, int(tail) if tail.isdigit() else 0)

    out: list[dict] = []
    for sub, dlist in by_sub.items():
        dlist.sort(key=_key)
        out.append({
            "id": sub,
            "instance_count": len(dlist),
            "designators": dlist,
        })

    out.sort(key=lambda e: (e["designators"][0] if e["designators"] else "", e["id"] or ""))
    return out
