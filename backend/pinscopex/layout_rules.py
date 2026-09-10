"""Validate datasheet layout_rules. Distances stay null unless numeric."""

from __future__ import annotations

from typing import Any

KNOWN_KINDS = frozenset({"decoupling_proximity", "thermal_via", "keepout"})


def _num(v: Any) -> float | None:
    if v is None or v is False:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def validate_layout_rules(raw: list | None) -> tuple[list[dict], list[str]]:
    """Return (normalized rows, errors). Empty list is a valid skip."""
    if not raw:
        return [], []
    if not isinstance(raw, list):
        return [], ["layout_rules must be an array"]
    ok: list[dict] = []
    errors: list[str] = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            errors.append(f"layout_rules[{i}] must be an object")
            continue
        kind = str(row.get("kind") or "").strip()
        if kind not in KNOWN_KINDS:
            errors.append(f"layout_rules[{i}] unknown kind {kind!r}")
            continue
        dist = _num(row.get("max_distance_mm"))
        via = row.get("min_via_count")
        via_i = None
        if isinstance(via, int) and not isinstance(via, bool):
            via_i = via
        elif via is not None:
            n = _num(via)
            via_i = int(n) if n is not None else None
        page = row.get("source_page")
        page_i = int(page) if isinstance(page, int) else None
        ok.append({
            "kind": kind,
            "pin": row.get("pin"),
            "cap_value_hint": row.get("cap_value_hint"),
            "max_distance_mm": dist,
            "same_layer": row.get("same_layer") if isinstance(row.get("same_layer"), bool) else None,
            "min_via_count": via_i,
            "net_class": row.get("net_class"),
            "note": row.get("note"),
            "source_page": page_i,
        })
    return ok, errors
