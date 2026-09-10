"""Pure parsers for PADS-PCB netlists and KiCad BOM CSV files."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Literal

NetlistFormat = Literal["pads", "edif", "kicad_xml", "kicad_sexp", "kicad_sch"]


def parse_netlist(
    path: str | Path,
    known_refs: set[str] | None = None,
) -> tuple[dict[str, str], dict[str, list[tuple[str, str]]]]:
    """Parse a PADS-PCB ASCII netlist (.asc).

    PADS-PCB allows reference designators containing spaces (e.g. ``CV GND``,
    ``CAN BUS IN``, ``3.3V ACTIVE``). When ``known_refs`` is supplied (typically
    from the BOM), tokens are greedily matched to the longest known designator
    so multi-word refs parse correctly. Without ``known_refs`` the parser falls
    back to single-word tokenisation.

    Returns:
        parts: {reference: footprint}
        nets:  {net_name: [(component_ref, pin_number), ...]}
    """
    text = Path(path).read_text()
    lines = text.splitlines()

    parts: dict[str, str] = {}
    nets: dict[str, list[tuple[str, str]]] = {}

    section = None
    current_net: str | None = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        # Section markers. PADS-PCB headers may carry trailing labels
        # (e.g. "*PART*       ITEMS" or "*MISC*      MISCELLANEOUS PARAMETERS"
        # from EasyEDA Pro), so match the marker prefix rather than the whole
        # line. Unknown markers (anything starred that we don't recognise) are
        # treated as section terminators — without this, EasyEDA Pro's *MISC*
        # ATTRIBUTE VALUES block leaks into the net section and "Datasheet"
        # URLs / footprint strings get misparsed as pin connections.
        if line.startswith("*"):
            if line.startswith("*SIGNAL*"):
                pass  # sub-marker within *NET*; handled in the net branch
            elif line.startswith("*PART*"):
                section = "part"
                current_net = None
                continue
            elif line.startswith("*NET*"):
                section = "net"
                current_net = None
                continue
            elif line.startswith("*END*"):
                break
            else:
                # *PADS-PCB*, *REMARK*, *MISC*, or any unrecognised marker
                section = None
                current_net = None
                continue

        if section == "part":
            tokens = line.split()
            ref, footprint = _parse_part_tokens(tokens, known_refs)
            if ref:
                parts[ref] = footprint

        elif section == "net":
            if line.startswith("*SIGNAL*"):
                current_net = line.split("*SIGNAL*", 1)[1].strip()
                if current_net not in nets:
                    nets[current_net] = []
            elif current_net is not None:
                # Pin entries: "REF.PIN REF.PIN ..."  (REF may contain spaces)
                nets[current_net].extend(_parse_pin_tokens(line.split(), known_refs))

    # Some PADS-PCB exports omit the *PART* section entirely and ship only
    # connectivity. Synthesize parts from refs seen in *SIGNAL* blocks so
    # downstream validation and graph-building still work; footprints stay
    # empty (the BOM is the source of truth for footprints anyway).
    if not parts and nets:
        for pins in nets.values():
            for ref, _pin in pins:
                parts.setdefault(ref, "")

    return parts, nets


def _parse_part_tokens(
    tokens: list[str],
    known_refs: set[str] | None,
) -> tuple[str | None, str]:
    """Split a *PART* line into (ref, footprint), respecting multi-word refs."""
    if not tokens:
        return None, ""

    if known_refs:
        # Greedy longest-prefix match against known refs
        for n in range(min(len(tokens), 8), 0, -1):
            candidate = " ".join(tokens[:n])
            if candidate in known_refs:
                return candidate, " ".join(tokens[n:])

    # Fallback: single-word ref, rest is footprint
    if len(tokens) >= 2:
        return tokens[0], " ".join(tokens[1:])
    return tokens[0], ""


def _parse_pin_tokens(
    tokens: list[str],
    known_refs: set[str] | None,
) -> list[tuple[str, str]]:
    """Parse a *SIGNAL* pin line into (ref, pin) pairs.

    Tokens terminate on a ``.`` — everything before (back to the previous
    consumed position) is the ref, possibly with internal spaces.
    """
    pins: list[tuple[str, str]] = []
    consumed = -1

    for j, token in enumerate(tokens):
        if j <= consumed or "." not in token:
            continue

        last_word, pin = token.rsplit(".", 1)

        # Greedy longest match when known_refs is available
        if known_refs:
            matched_start: int | None = None
            for start in range(consumed + 1, j + 1):
                parts = tokens[start:j] + ([last_word] if last_word else [])
                candidate = " ".join(parts)
                if candidate and candidate in known_refs:
                    matched_start = start
                    break
            if matched_start is not None:
                ref = " ".join(
                    tokens[matched_start:j] + ([last_word] if last_word else [])
                )
                pins.append((ref, pin))
                consumed = j
                continue

        # Fallback: single-word ref (original behaviour)
        ref = last_word
        pins.append((ref, pin))
        consumed = j

    return pins


def detect_netlist_format(content: bytes | str) -> NetlistFormat:
    """Sniff the first chunk of a netlist to decide the format.

    EDIF starts with ``(edif``; KiCad XML with ``<export`` / ``<?xml``;
    KiCad s-expr netlist with ``(export``; schematic with ``(kicad_sch``.
    PADS-PCB ASCII (``*PADS-PCB*``) is the default when no marker is found.
    """
    if isinstance(content, bytes):
        text = content[:2048].decode("utf-8", errors="replace")
    else:
        text = content[:2048]
    head = text.lstrip("\ufeff").lstrip()
    low = head[:40].lower()
    if low.startswith("(edif"):
        return "edif"
    if low.startswith("(kicad_sch"):
        return "kicad_sch"
    if low.startswith("(export"):
        return "kicad_sexp"
    if low.startswith("<?xml") or low.startswith("<export"):
        return "kicad_xml"
    return "pads"


def parse_netlist_any(
    path: str | Path,
    known_refs: set[str] | None = None,
    *,
    include_subdesigns: set[str] | None = None,
) -> tuple[dict[str, str], dict[str, list[tuple[str, str]]], NetlistFormat]:
    """Auto-detect the netlist format and parse.

    Returns ``(parts, nets, format)``. The ``parts`` and ``nets`` shapes match
    :func:`parse_netlist`; downstream code (graph build, validation) doesn't
    need to know which parser ran. ``known_refs`` is only relevant for PADS —
    EDIF designators are unambiguous tokens. ``include_subdesigns`` is only
    relevant for EDIF — it filters which ``&NNNN``-prefixed instances and
    their nets land in the output (PADS netlists have no sub-design concept).
    """
    p = Path(path)
    sample = p.read_bytes()[:2048]
    fmt = detect_netlist_format(sample)
    if fmt == "edif":
        from backend.pinscopex.parsers_edif import parse_edif_netlist
        parts, nets = parse_edif_netlist(p, include_subdesigns=include_subdesigns)
    elif fmt.startswith("kicad"):
        from backend.pinscopex.parsers_kicad import parse_kicad
        parts, nets, _ = parse_kicad(p)
    else:
        parts, nets = parse_netlist(p, known_refs=known_refs)
    return parts, nets, fmt


def validate_netlist(parts: dict, nets: dict) -> list[str]:
    """Sanity-check parsed netlist data. Returns a list of error strings (empty = valid)."""
    errors: list[str] = []

    if not parts:
        errors.append(
            "No components found — is this a PADS-PCB (.asc), EDIF (.edn), "
            "or KiCad netlist / .kicad_sch?"
        )
        return errors  # further checks are meaningless without parts

    if not nets:
        errors.append("No nets found — the connectivity section (*NET*) is missing or empty")
        return errors

    # At least some parts must appear in the net connections
    refs_in_nets = {ref for pins in nets.values() for ref, _ in pins}
    if not (set(parts) & refs_in_nets):
        errors.append(
            "No components are wired to any net — the connectivity section may be missing or malformed"
        )

    # Every real schematic has a ground net
    gnd_names = {"GND", "AGND", "DGND", "PGND", "VSS", "0V"}
    has_gnd = any(
        n.upper() in gnd_names or n.upper().endswith("GND") or n.upper().startswith("GND")
        for n in nets
    )
    if not has_gnd:
        errors.append(
            "No ground net found (expected GND, AGND, DGND, VSS, etc.) — "
            "this may not be a complete schematic netlist"
        )

    return errors


def parse_bom(
    path: str | Path,
    *,
    reference_col: str = "Reference",
    mpn_col: str = "Manufacturer Part Number",
) -> dict[str, dict]:
    """Parse a KiCad BOM CSV with grouped references.

    Args:
        path: Path to the BOM CSV file.
        reference_col: Column name for reference designators.
        mpn_col: Column name for manufacturer part numbers.

    Returns:
        {reference: {"value": str, "footprint": str, "mpn": str|None, "lcsc": str|None}}
        One entry per individual reference (groups are expanded).
    """
    result: dict[str, dict] = {}
    text = Path(path).read_text()
    reader = csv.DictReader(text.splitlines())
    colnames = {n.lower() for n in (reader.fieldnames or []) if n}
    has_dnp_col = bool(colnames & {"dnp", "dni", "fitted", "populate"})
    has_variant_col = bool(colnames & {"variant"})

    for row in reader:
        refs_raw = row.get(reference_col, "")
        value = row.get("Value", "") or row.get("Comment", "")
        footprint = row.get("Footprint", "")
        mpn = (row.get(mpn_col, "") or "").strip() or None
        lcsc = row.get("LCSC", "") or None
        datasheet_url = (row.get("Datasheet", "") or "").strip() or None

        # Expand grouped references: "C1,C2,C5" -> ["C1", "C2", "C5"]
        refs = [r.strip() for r in refs_raw.split(",") if r.strip()]
        # KiCad exports often leave Manufacturer Part Number empty and put
        # the orderable code in Value (or PNM). Without this, U* never
        # enter ic_mpns and review reports "no datasheet PDF".
        if not mpn:
            mpn = (row.get("PNM", "") or "").strip() or None
        if not mpn and any(re.match(r"^U\d", r, re.I) for r in refs):
            mpn = (value or "").strip() or None

        dnp_raw = (row.get("DNP") or row.get("DNI") or "").strip().lower()
        fitted_raw = (row.get("Fitted") or row.get("Populate") or "").strip().lower()
        variant = (row.get("Variant") or row.get("variant") or "").strip() or None
        is_dnp = dnp_raw in {"1", "y", "yes", "true", "dnp", "dni", "x"}
        if not is_dnp and fitted_raw in {"0", "n", "no", "false"}:
            is_dnp = True

        for ref in refs:
            entry = {
                "value": value,
                "footprint": footprint,
                "mpn": mpn,
                "lcsc": lcsc,
                "datasheet_url": datasheet_url,
            }
            if has_dnp_col:
                entry["dnp"] = is_dnp
            if has_variant_col:
                entry["variant"] = variant
            result[ref] = entry

    return result
