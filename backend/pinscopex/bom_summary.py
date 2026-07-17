"""Build a BOM summary table from the design graph. No AI — pure collation."""

from __future__ import annotations

from backend.pinscopex.models import ComponentType, DesignGraph
from backend.pinscopex.utils import natural_sort_key


def build_bom_summary(
    graph: DesignGraph,
    datasheet_mpns: set[str] | None = None,
    descriptions: dict[str, str] | None = None,
) -> list[dict]:
    """Group components by MPN and collate BOM summary rows.

    ``descriptions`` is an optional ``{mpn: description}`` map (e.g. from
    extracted ``package_info.description``). When supplied, IC rows get a
    ``description`` field — used by the frontend to show what the chip does
    in place of the empty Specs cell.

    Returns a list of dicts, each with:
      mpn, designators, value, category, specs, description
    """
    # Group components by MPN (or by value+type if no MPN)
    by_key: dict[str, list] = {}
    for comp in graph.components.values():
        key = comp.mpn if comp.mpn else f"__no_mpn__{comp.value}__{comp.component_type}"
        by_key.setdefault(key, []).append(comp)

    rows = []
    for comps in by_key.values():
        first = comps[0]
        designators = sorted(
            [c.reference for c in comps], key=natural_sort_key
        )

        # Extract display-friendly specs
        specs_dict = None
        if first.specs:
            if hasattr(first.specs, "values"):
                # SimpleComponentSpecs — flatten the values dict
                raw = {k: v for k, v in first.specs.values.items() if v is not None}
            else:
                raw = first.specs.model_dump(exclude={"specs_type"})
                # Drop None values and internal numeric fields
                raw = {
                    k: v for k, v in raw.items()
                    if v is not None and k not in ("value_ohms", "value_farads", "value_henries")
                }
            specs_dict = raw if raw else None

        has_ds = bool(
            first.mpn
            and datasheet_mpns is not None
            and first.mpn in datasheet_mpns
        )

        description = None
        if (
            descriptions is not None
            and first.mpn
            and first.component_type == ComponentType.IC
        ):
            description = descriptions.get(first.mpn)

        rows.append({
            "mpn": first.mpn,
            "designators": designators,
            "value": first.value,
            "category": first.component_subtype,
            "specs": specs_dict,
            "description": description,
            "has_datasheet": has_ds,
        })

    # Sort: ICs first, then passives, then others; within each by category then MPN
    def sort_key(row: dict) -> tuple:
        cat = row["category"] or ""
        if cat.startswith("ic"):
            group = 0
        elif cat.startswith("passive"):
            group = 1
        else:
            group = 2
        return (group, cat, row["mpn"] or "")

    rows.sort(key=sort_key)
    return rows
