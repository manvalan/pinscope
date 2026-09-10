"""BOM vs schematic property matching.

Compares per-reference MPN/value from the schematic property table against
the uploaded BOM. Silent when the schematic map is empty (PADS/EDIF) so
we never invent orphans from a format that has no schematic properties.
"""

from __future__ import annotations

from backend.pinscopex.models import Finding


def _norm_mpn(value: object) -> str:
    return " ".join(str(value or "").split()).upper()


def check_bom_schematic_match(
    schematic: dict[str, dict],
    bom: dict[str, dict],
) -> list[Finding]:
    if not schematic:
        return []

    findings: list[Finding] = []
    refs = sorted(set(schematic) | set(bom))
    for ref in refs:
        if ref.startswith("#"):
            continue
        sch = schematic.get(ref) or {}
        bom_row = bom.get(ref) or {}
        if ref not in schematic:
            findings.append(Finding(
                designator=ref,
                mpn=str(bom_row.get("mpn") or ""),
                aspect="bom_match",
                source="bom_match",
                status="WARNING",
                finding=(
                    f"BOM lists {ref} but the schematic has no such reference."
                ),
                why=(
                    "An extra BOM line that is not in the netlist will not be "
                    "validated against a datasheet and may indicate a stale BOM."
                ),
                recommendation=f"Remove {ref} from the BOM or add it to the schematic.",
                rule_id="PS-BOM-002",
                pins=[],
            ))
            continue
        sch_mpn = _norm_mpn(sch.get("mpn"))
        bom_mpn = _norm_mpn(bom_row.get("mpn"))
        if sch_mpn and bom_mpn and sch_mpn != bom_mpn:
            findings.append(Finding(
                designator=ref,
                mpn=str(sch.get("mpn") or ""),
                aspect="bom_match",
                source="bom_match",
                status="ERROR",
                finding=(
                    f"{ref} schematic MPN '{sch.get('mpn')}' does not match "
                    f"BOM MPN '{bom_row.get('mpn')}'."
                ),
                why=(
                    "Datasheet review and library lookup follow one MPN. "
                    "A mismatch means the wrong die or a stale BOM row."
                ),
                recommendation=(
                    f"Make {ref}'s BOM and schematic MPN identical, then re-run."
                ),
                rule_id="PS-BOM-001",
                pins=[ref],
            ))
    return findings
