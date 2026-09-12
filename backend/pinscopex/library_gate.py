"""Shared-library promotion gates for extracted IC JSON."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def pintable_checksum(pintable: list[Any]) -> str:
    """Stable hash of pin number+name pairs (order-independent)."""
    rows: list[tuple[str, str]] = []
    for pin in pintable or []:
        if isinstance(pin, dict):
            num = str(pin.get("number") or "").strip()
            name = str(pin.get("name") or "").strip()
        else:
            num = str(getattr(pin, "number", "") or "").strip()
            name = str(getattr(pin, "name", "") or "").strip()
        if num:
            rows.append((num, name))
    payload = json.dumps(sorted(rows), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def should_promote_extraction(data: dict) -> tuple[bool, str]:
    """Return (ok, reason). Reject empty / tiny pintables from shared library."""
    pins = data.get("pintable") or []
    if not isinstance(pins, list) or len(pins) == 0:
        return False, "empty pintable"
    if len(pins) < 2:
        return False, "pintable has fewer than 2 pins"
    # Require at least one named pin so a number-only stub cannot poison the library.
    named = 0
    for pin in pins:
        name = pin.get("name") if isinstance(pin, dict) else getattr(pin, "name", None)
        if name and str(name).strip() and str(name).strip() != "~":
            named += 1
    if named == 0:
        return False, "pintable has no named pins"
    return True, pintable_checksum(pins)
