"""Resolve passive component MPNs against stored manufacturer patterns."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from backend.pinscopex.models import (
    CapacitorSpecs,
    ComponentSpecs,
    ComponentType,
    InductorSpecs,
    PassivePattern,
    ResistorSpecs,
    ResolvedPassive,
    SimpleComponentSpecs,
    ValueDecoder,
)
from backend.pinscopex.parsers import parse_bom


# ---------------------------------------------------------------------------
# Value decoders
# ---------------------------------------------------------------------------


def _multiplier(digit: str, letter_multipliers: dict[str, int | str]) -> float:
    """Convert a multiplier character to its power-of-10 value.

    Raises ValueError for ``"decimal_point"`` entries — callers must handle
    R-notation before reaching here.
    """
    if digit in letter_multipliers:
        val = letter_multipliers[digit]
        if val == "decimal_point":
            raise ValueError(f"Letter '{digit}' is a decimal-point marker, not a multiplier")
        return 10.0 ** int(val)
    return 10.0 ** int(digit)


def _decode_eia3_pf(digits: str) -> float:
    """3-digit EIA code → picofarads.  e.g. '106' → 10×10^6 = 10_000_000 pF."""
    sig = int(digits[:2])
    mult = int(digits[2])
    return float(sig) * (10.0 ** mult)


def _decode_r_notation(digits: str, decimal_letters: set[str]) -> float | None:
    """Try to decode R-notation (e.g. '4R70' → 4.70, '47R0' → 47.0).

    Returns None if no decimal-point letter is found in *digits*.
    """
    for letter in decimal_letters:
        if letter in digits:
            return float(digits.replace(letter, "."))
    return None


def _decode_eia4_ohm(
    digits: str,
    tolerance_code: str,
    decoder: ValueDecoder,
) -> float:
    """4-digit resistance code → ohms, with tolerance-conditional layout."""
    if decoder.zero_code and digits == decoder.zero_code:
        return 0.0

    # Handle R-notation: letters marked as "decimal_point" in letter_multipliers
    decimal_letters = {
        k for k, v in decoder.letter_multipliers.items() if v == "decimal_point"
    }
    if decimal_letters:
        r_val = _decode_r_notation(digits, decimal_letters)
        if r_val is not None:
            return r_val

    cond = decoder.conditional_on or {}
    high_tol = cond.get("high_tolerance", [])

    if tolerance_code in high_tol:
        layout = cond.get("high_tolerance_layout", {})
    else:
        layout = cond.get("low_tolerance_layout", {})

    sig_start = layout.get("significant_start", 0)
    sig_count = layout.get("significant_count", 3)
    mult_idx = layout.get("multiplier_index", 3)

    sig = int(digits[sig_start : sig_start + sig_count])
    mult_char = digits[mult_idx]
    return float(sig) * _multiplier(mult_char, decoder.letter_multipliers)


def _decode_letter_decimal(digits: str, decoder: ValueDecoder) -> float:
    """Letter-decimal notation: letter serves as decimal point AND multiplier.

    Examples (resistor): 2K2→2200Ω, 97R6→97.6Ω, 10K→10000Ω, 1M→1MΩ
    """
    for letter, mult in decoder.letter_multipliers.items():
        if letter in digits:
            before, after = digits.split(letter, 1)
            if after:
                value = float(f"{before}.{after}")
            else:
                value = float(before)
            return value * float(mult)
    # No letter found — pure numeric
    return float(digits)


def decode_value(
    digits: str,
    decoder: ValueDecoder,
    tolerance_code: str | None = None,
) -> float:
    """Dispatch to the correct decoder and convert to output_unit."""
    if decoder.type == "eia3_pf":
        pf = _decode_eia3_pf(digits)
        if decoder.output_unit == "F":
            return pf * 1e-12
        return pf

    if decoder.type == "eia4_ohm_conditional":
        return _decode_eia4_ohm(digits, tolerance_code or "", decoder)

    if decoder.type == "letter_decimal_ohm":
        return _decode_letter_decimal(digits, decoder)

    raise ValueError(f"Unknown decoder type: {decoder.type}")


# ---------------------------------------------------------------------------
# Value formatting
# ---------------------------------------------------------------------------

_SI_PREFIXES_OHM = [
    (1e6, "Mohm"),
    (1e3, "kohm"),
    (1.0, "ohm"),
    (1e-3, "mohm"),
]

_SI_PREFIXES_F = [
    (1e-3, "mF"),
    (1e-6, "uF"),
    (1e-9, "nF"),
    (1e-12, "pF"),
    (1e-15, "fF"),
]


def _format_value(value: float, unit: str) -> str:
    """Format a value with appropriate SI prefix."""
    if value == 0.0:
        return f"0 {unit}"

    prefixes = _SI_PREFIXES_OHM if unit == "ohm" else _SI_PREFIXES_F

    for threshold, label in prefixes:
        if abs(value) >= threshold * 0.999:
            scaled = value / threshold
            # Prefer integer display when possible
            if scaled == int(scaled):
                return f"{int(scaled)} {label}"
            # Up to 2 decimal places, strip trailing zeros
            return f"{scaled:.2f}".rstrip("0").rstrip(".") + f" {label}"

    # Fallback
    return f"{value} {unit}"


def _parse_wattage(s: str) -> str:
    """Pass through wattage string as-is (e.g. '1/10W')."""
    return s


# ---------------------------------------------------------------------------
# ResolvedPassive → ComponentSpecs converter
# ---------------------------------------------------------------------------


def resolved_to_specs(resolved: ResolvedPassive) -> ComponentSpecs:
    """Convert a ResolvedPassive to its type-specific specs model."""
    if resolved.component_type == ComponentType.RESISTOR:
        return ResistorSpecs(
            value_ohms=resolved.value,
            value_formatted=resolved.value_formatted,
            tolerance=resolved.tolerance,
            package=resolved.package,
            power_rating_w=resolved.power_rating,
        )
    if resolved.component_type == ComponentType.CAPACITOR:
        return CapacitorSpecs(
            value_farads=resolved.value,
            value_formatted=resolved.value_formatted,
            tolerance=resolved.tolerance,
            package=resolved.package,
            voltage_rating_v=resolved.voltage_rating,
            dielectric=resolved.dielectric,
        )
    if resolved.component_type == ComponentType.INDUCTOR:
        return InductorSpecs(
            value_henries=resolved.value,
            value_formatted=resolved.value_formatted,
            tolerance=resolved.tolerance,
            package=resolved.package,
        )
    raise ValueError(f"Unsupported component type: {resolved.component_type}")


# ---------------------------------------------------------------------------
# SimpleComponentSpecs → typed passive specs (for DigiKey auto-resolve)
# ---------------------------------------------------------------------------

_SPICE_MULTIPLIERS: dict[str, float] = {
    "T": 1e12, "G": 1e9, "M": 1e6, "k": 1e3,
    "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12,
}

_UNIT_SUFFIXES = ("ohm", "F", "H", "V", "W", "A", "Hz")


def _parse_spice_value(s: str) -> float:
    """Parse a SPICE-prefixed value string to a float.

    Examples: "5.1kohm" → 5100.0, "470nF" → 4.7e-7, "30V" → 30.0,
              "120 at 100MHz" → 120.0
    """
    s = s.strip()

    # Strip conditional clauses like "at 100MHz" or "@ 100MHz"
    for sep in (" at ", " @ ", "@"):
        idx = s.find(sep)
        if idx > 0:
            s = s[:idx].strip()
            break

    # Strip unit suffix
    for suffix in _UNIT_SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break

    # Try direct float (no multiplier)
    try:
        return float(s)
    except ValueError:
        pass

    # Find multiplier character (last non-digit, non-dot char)
    for i in range(len(s) - 1, -1, -1):
        ch = s[i]
        if ch in _SPICE_MULTIPLIERS:
            numeric = s[:i] + s[i + 1 :]
            return float(numeric) * _SPICE_MULTIPLIERS[ch]

    raise ValueError(f"Cannot parse SPICE value: {s!r}")


def simple_to_typed_passive_specs(simple: SimpleComponentSpecs) -> ComponentSpecs:
    """Convert auto-resolved SimpleComponentSpecs to a typed passive model."""
    subtype = simple.component_subtype or ""
    vals = simple.values

    # Common optional fields
    value_formatted = str(vals.get("value_formatted") or "")
    tolerance = str(vals.get("tolerance")) if vals.get("tolerance") else None
    package = str(vals.get("package")) if vals.get("package") else None

    subtype_for_specs = subtype or None

    if subtype.startswith("passive.resistor") or subtype == "passive.resistor":
        raw = vals.get("value_ohms")
        if raw is None:
            raise ValueError(f"Missing value_ohms in auto-resolved resistor specs")
        value_ohms = _parse_spice_value(str(raw)) if isinstance(raw, str) else float(raw)
        power_rating_w = str(vals.get("power_rating_w")) if vals.get("power_rating_w") else None
        return ResistorSpecs(
            component_subtype=subtype_for_specs,
            value_ohms=value_ohms,
            value_formatted=value_formatted or _format_value(value_ohms, "ohm"),
            tolerance=tolerance,
            package=package,
            power_rating_w=power_rating_w,
        )

    if subtype.startswith("passive.capacitor"):
        raw = vals.get("value_farads")
        if raw is None:
            raise ValueError(f"Missing value_farads in auto-resolved capacitor specs")
        value_farads = _parse_spice_value(str(raw)) if isinstance(raw, str) else float(raw)
        voltage_rating_v = str(vals.get("voltage_rating_v")) if vals.get("voltage_rating_v") else None
        dielectric = str(vals.get("dielectric")) if vals.get("dielectric") else None
        return CapacitorSpecs(
            component_subtype=subtype_for_specs,
            value_farads=value_farads,
            value_formatted=value_formatted or _format_value(value_farads, "F"),
            tolerance=tolerance,
            package=package,
            voltage_rating_v=voltage_rating_v,
            dielectric=dielectric,
        )

    if subtype.startswith("passive.inductor") or subtype == "passive.ferrite_bead":
        raw = vals.get("value_henries")
        if raw is None:
            raise ValueError(f"Missing value_henries in auto-resolved inductor specs")
        value_henries = _parse_spice_value(str(raw)) if isinstance(raw, str) else float(raw)
        current_rating_a = str(vals.get("current_rating_a")) if vals.get("current_rating_a") else None
        dcr_raw = vals.get("dcr_ohms")
        dcr_ohms: float | None = None
        if dcr_raw is not None:
            dcr_ohms = _parse_spice_value(str(dcr_raw)) if isinstance(dcr_raw, str) else float(dcr_raw)
        return InductorSpecs(
            component_subtype=subtype_for_specs,
            value_henries=value_henries,
            value_formatted=value_formatted,
            tolerance=tolerance,
            package=package,
            current_rating_a=current_rating_a,
            dcr_ohms=dcr_ohms,
        )

    raise ValueError(f"Unsupported passive subtype for conversion: {subtype!r}")


# ---------------------------------------------------------------------------
# Pattern loading and matching
# ---------------------------------------------------------------------------


class SkippedItem:
    """A component or pattern that was skipped due to an error."""
    __slots__ = ("identifier", "stage", "error")

    def __init__(self, identifier: str, stage: str, error: str) -> None:
        self.identifier = identifier
        self.stage = stage
        self.error = error

    def to_dict(self) -> dict[str, str]:
        return {"identifier": self.identifier, "stage": self.stage, "error": self.error}


def load_patterns(
    patterns_dir: str | Path,
    skipped: list[SkippedItem] | None = None,
) -> list[PassivePattern]:
    """Load all pattern JSON files from a directory.

    Invalid pattern files are silently skipped (appended to *skipped* if provided).
    """
    patterns_dir = Path(patterns_dir)
    patterns: list[PassivePattern] = []
    for f in sorted(patterns_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            patterns.append(PassivePattern(**data))
        except Exception as e:
            if skipped is not None:
                skipped.append(SkippedItem(f.stem, "passive_pattern_load", str(e)))
    return patterns


def resolve_mpn(
    mpn: str,
    patterns: list[PassivePattern],
) -> tuple[PassivePattern, dict[str, str]] | None:
    """Match an MPN against loaded patterns. Returns (pattern, captured_groups) or None."""
    for pat in patterns:
        m = re.match(pat.regex, mpn)
        if m:
            return pat, m.groupdict()
    return None


# ---------------------------------------------------------------------------
# BOM resolution
# ---------------------------------------------------------------------------


def resolve_bom(
    bom_path: str | Path,
    patterns_dir: str | Path = "component-patterns",
    *,
    reference_col: str = "Reference",
    mpn_col: str = "Manufacturer Part Number",
    skipped: list[SkippedItem] | None = None,
) -> list[ResolvedPassive]:
    """Resolve all passive MPNs in a BOM against stored patterns.

    Individual MPNs that fail to decode are silently skipped (appended to
    *skipped* if provided).
    """
    patterns = load_patterns(patterns_dir, skipped=skipped)
    bom = parse_bom(bom_path, reference_col=reference_col, mpn_col=mpn_col)

    # Group references by MPN
    mpn_refs: dict[str, list[str]] = defaultdict(list)
    mpn_value: dict[str, str] = {}
    for ref, info in bom.items():
        mpn = info.get("mpn")
        if mpn:
            mpn_refs[mpn].append(ref)
            mpn_value[mpn] = info.get("value", "")

    resolved: list[ResolvedPassive] = []
    for mpn, refs in sorted(mpn_refs.items()):
        match = resolve_mpn(mpn, patterns)
        if match is None:
            continue

        try:
            pat, groups = match
            fields_by_name = {f.name: f for f in pat.fields}

            # Decode the primary value — find the value field by name
            value_digits = groups.get("resistance") or groups.get("capacitance") or ""
            tolerance_code = groups.get("tolerance", "")

            value = decode_value(value_digits, pat.value_decoder, tolerance_code)
            value_formatted = _format_value(value, pat.value_decoder.output_unit)

            # Decode tolerance
            tolerance_field = fields_by_name.get("tolerance")
            tolerance = (
                tolerance_field.lookup.get(tolerance_code) if tolerance_field else None
            )

            # Decode package size
            size_field = fields_by_name.get("size")
            size_code = groups.get("size", "")
            package = size_field.lookup.get(size_code, size_code) if size_field else None

            # Decode voltage rating (capacitors)
            voltage_field = fields_by_name.get("voltage")
            voltage_code = groups.get("voltage", "")
            voltage_rating = (
                voltage_field.lookup.get(voltage_code) if voltage_field else None
            )

            # Decode power rating (resistors)
            wattage_field = fields_by_name.get("wattage")
            wattage_code = groups.get("wattage", "")
            power_rating = (
                wattage_field.lookup.get(wattage_code) if wattage_field else None
            )

            # Decode dielectric (capacitors)
            dielectric_field = fields_by_name.get("dielectric")
            dielectric_code = groups.get("dielectric", "")
            dielectric = (
                dielectric_field.lookup.get(dielectric_code)
                if dielectric_field
                else None
            )

            # Build raw_fields: code → decoded value for all fields
            raw_fields: dict[str, str] = {}
            for fname, fval in groups.items():
                fd = fields_by_name.get(fname)
                if fd and fd.lookup:
                    raw_fields[fname] = fd.lookup.get(fval, fval)
                else:
                    raw_fields[fname] = fval

            resolved.append(
                ResolvedPassive(
                    mpn=mpn,
                    references=sorted(refs),
                    component_type=pat.component_type,
                    component_subtype=pat.component_subtype,
                    manufacturer=pat.manufacturer,
                    series=pat.series,
                    value=value,
                    value_formatted=value_formatted,
                    tolerance=tolerance,
                    package=package,
                    voltage_rating=voltage_rating,
                    power_rating=power_rating,
                    dielectric=dielectric,
                    raw_fields=raw_fields,
                )
            )
        except Exception as e:
            if skipped is not None:
                skipped.append(SkippedItem(mpn, "passive_resolve", str(e)))

    return resolved


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resolve passive component MPNs from a BOM against stored patterns",
    )
    parser.add_argument(
        "bom",
        nargs="?",
        default="simple_project/TI-MSP-KICAD9-TUTORIAL.csv",
        help="Path to BOM CSV file",
    )
    parser.add_argument(
        "--patterns",
        default="component-patterns",
        help="Directory containing pattern JSON files",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write resolved JSON to this path",
    )
    args = parser.parse_args()

    resolved = resolve_bom(args.bom, args.patterns)

    if not resolved:
        print("No passive components resolved.")
        return

    for r in resolved:
        extras = []
        if r.tolerance:
            extras.append(r.tolerance)
        if r.package:
            extras.append(r.package)
        if r.dielectric:
            extras.append(r.dielectric)
        if r.voltage_rating:
            extras.append(r.voltage_rating)
        if r.power_rating:
            extras.append(r.power_rating)
        extra_str = ", ".join(extras)
        print(f"  {r.mpn} → {r.value_formatted} ({extra_str})")
        print(f"    refs: {', '.join(r.references)}")

    print(f"\nResolved {len(resolved)} passive component(s).")

    if args.output:
        Path(args.output).write_text(
            json.dumps([r.model_dump() for r in resolved], indent=2) + "\n"
        )
        print(f"Written to {args.output}")


if __name__ == "__main__":
    main()
