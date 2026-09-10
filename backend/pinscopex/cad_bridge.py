"""pinscope-cad-bridge JSON (E2) for the KiCad action plugin."""

from __future__ import annotations

import json
from pathlib import Path

from backend.pinscopex.models import CadIndexEntry, DesignGraph, Finding, ValidationReport

CAD_BRIDGE_VERSION = 1
_PCB_RULE_PREFIXES = ("PS-PLC", "PS-SI", "PS-LAY", "PS-3W", "PS-CLR")


def annotate_findings_cad(
    findings: list[Finding],
    cad_index: dict[str, CadIndexEntry] | None,
) -> None:
    """Fill cad_sheet/cad_uuid from the graph index when the finding omitted them."""
    if not cad_index:
        return
    for f in findings:
        entry = cad_index.get(f.designator)
        if not entry:
            continue
        if not f.cad_uuid and entry.uuid:
            f.cad_uuid = entry.uuid
        if not f.cad_sheet and entry.sheet:
            f.cad_sheet = entry.sheet


def _pin_numbers(designator: str, pins: list[str]) -> list[str]:
    out: list[str] = []
    prefix = designator + "."
    for raw in pins:
        s = str(raw).strip()
        if not s:
            continue
        if s.upper().startswith(prefix.upper()):
            s = s[len(prefix):]
        out.append(s)
    return out


def _target_kind(rule_id: str | None) -> str:
    rid = rule_id or ""
    if any(rid.startswith(p) for p in _PCB_RULE_PREFIXES):
        return "pcb"
    return "sch"


def build_cad_bridge(
    report: ValidationReport,
    project_id: str,
    *,
    url_base: str = "",
) -> dict:
    """E2 `pinscope-cad-bridge` payload. Missing uuid/sheet stay empty strings."""
    findings: list[dict] = []
    for f in report.findings:
        fid = f.finding_id or ""
        url = ""
        if url_base and fid:
            sep = "&" if "?" in url_base else "?"
            url = f"{url_base}{sep}finding={fid}"
        findings.append({
            "rule_id": f.rule_id or f.source or "review",
            "ref": f.designator,
            "pins": _pin_numbers(f.designator, f.pins or []),
            "sheet": f.cad_sheet or "",
            "uuid": f.cad_uuid or "",
            "severity": (f.status or "WARNING").lower(),
            "message": f.finding,
            "url": url,
            "finding_id": fid,
            "net": f.net or "",
            "target": _target_kind(f.rule_id),
        })
    return {
        "version": CAD_BRIDGE_VERSION,
        "project_id": project_id,
        "findings": findings,
    }


def write_cad_bridge(path: str | Path, payload: dict) -> None:
    Path(path).write_text(json.dumps(payload, indent=2) + "\n")


def cad_index_from_graph(graph: DesignGraph) -> dict[str, CadIndexEntry]:
    return dict(graph.cad_index or {})
