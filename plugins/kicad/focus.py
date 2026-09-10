"""Locate pinscope-findings.json and decide schematic vs PCB focus.

Imported by the KiCad action plugin. No pcbnew/wx at import time so tests
can run in the repo venv.
"""

from __future__ import annotations

import json
from pathlib import Path


def find_bridge_file(start: str | Path, *, max_up: int = 6) -> Path | None:
    cur = Path(start).resolve()
    if cur.is_file():
        cur = cur.parent
    for _ in range(max_up + 1):
        cand = cur / "pinscope-findings.json"
        if cand.is_file():
            return cand
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def load_bridge(path: str | Path) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("pinscope-findings.json is not an object")
    return raw


def focus_target(finding: dict) -> dict:
    """Map one E2 finding to a CAD focus request."""
    ref = str(finding.get("ref") or "")
    uuid = str(finding.get("uuid") or "")
    sheet = str(finding.get("sheet") or "")
    kind = finding.get("target")
    if kind not in ("sch", "pcb"):
        rid = str(finding.get("rule_id") or "")
        kind = "pcb" if rid.startswith(("PS-PLC", "PS-SI", "PS-LAY", "PS-3W", "PS-CLR")) else "sch"
    return {
        "kind": kind,
        "ref": ref,
        "uuid": uuid,
        "sheet": sheet,
        "pins": list(finding.get("pins") or []),
    }
