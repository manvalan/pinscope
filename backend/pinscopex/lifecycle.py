"""Distributor lifecycle / RoHS — cached records only, never a guessed equivalent."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel

from backend.pinscopex.models import ComponentType, DesignGraph, Finding
from backend.pinscopex.utils import safe_mpn

_EOL = re.compile(
    r"\b(obsolete|eol|end\s*of\s*life|discontinued|last\s*time\s*buy|ltb)\b",
    re.I,
)
_NRND = re.compile(
    r"\b(nrnd|not\s+for\s+new\s+designs|not\s+recommended)\b",
    re.I,
)
_ACTIVE = re.compile(r"\b(active|production|recommended)\b", re.I)
_ROHS_NO = re.compile(r"\b(non[-\s]?compliant|not\s+compliant|no)\b", re.I)
_ROHS_YES = re.compile(r"\b(rohs\s*\d*\s*compliant|compliant|yes|true)\b", re.I)
_ROHS_NA = re.compile(r"\b(not\s+applicable|n/?a|exempt)\b", re.I)


class LifecycleRecord(BaseModel):
    mpn: str
    source: str = ""
    lifecycle: str | None = None  # active | nrnd | eol | unknown
    rohs_compliant: bool | None = None
    stock: int | None = None
    lead_time: str | None = None
    replacement: str | None = None
    product_status_raw: str = ""


def _status_lifecycle(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    if _EOL.search(s):
        return "eol"
    if _NRND.search(s):
        return "nrnd"
    if _ACTIVE.search(s):
        return "active"
    return "unknown"


def _rohs(raw: str) -> bool | None:
    s = (raw or "").strip()
    if not s:
        return None
    if _ROHS_NA.search(s):
        return None
    if _ROHS_NO.search(s):
        return False
    if _ROHS_YES.search(s):
        return True
    return None


def _replacement(product: dict) -> str | None:
    for key in ("ProductSubstitutions", "Substitutes", "replacement", "Replacement"):
        val = product.get(key)
        if not val:
            continue
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, list) and val:
            first = val[0]
            if isinstance(first, str) and first.strip():
                return first.strip()
            if isinstance(first, dict):
                for k in ("ManufacturerProductNumber", "ManufacturerPartNumber", "mpn"):
                    if first.get(k):
                        return str(first[k]).strip()
    return None


def parse_distributor_product(mpn: str, product: dict, *, source: str = "digikey") -> LifecycleRecord:
    """Map a DigiKey/Mouser/LCSC product dict. Unknown fields stay None."""
    status = (
        product.get("ProductStatus")
        or product.get("productStatus")
        or product.get("partLifeCycle")
        or product.get("LifecycleStatus")
        or ""
    )
    rohs_raw = (
        product.get("RoHSStatus")
        or product.get("rohsStatus")
        or product.get("rohs")
        or ""
    )
    if isinstance(rohs_raw, bool):
        rohs = rohs_raw
        rohs_raw = "true" if rohs_raw else "false"
    else:
        rohs = _rohs(str(rohs_raw))
    stock = product.get("QuantityAvailable")
    if stock is None:
        stock = product.get("stock")
    try:
        stock_i = int(stock) if stock is not None else None
    except (TypeError, ValueError):
        stock_i = None
    lead = product.get("ManufacturerLeadWeeks") or product.get("lead_time") or product.get("LeadTime")
    return LifecycleRecord(
        mpn=mpn,
        source=source,
        lifecycle=_status_lifecycle(str(status)),
        rohs_compliant=rohs,
        stock=stock_i,
        lead_time=str(lead) if lead not in (None, "") else None,
        replacement=_replacement(product),
        product_status_raw=str(status),
    )


def load_lifecycle_dir(directory: str | Path) -> dict[str, LifecycleRecord]:
    out: dict[str, LifecycleRecord] = {}
    path = Path(directory)
    if not path.is_dir():
        return out
    for f in path.glob("*.json"):
        raw = json.loads(f.read_text())
        rec = LifecycleRecord.model_validate(raw)
        out[rec.mpn] = rec
    return out


def write_lifecycle_record(directory: str | Path, rec: LifecycleRecord) -> Path:
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    dest = path / f"{safe_mpn(rec.mpn)}.json"
    dest.write_text(rec.model_dump_json(indent=2) + "\n")
    return dest


def _match_record(mpn: str | None, records: dict[str, LifecycleRecord]) -> LifecycleRecord | None:
    if not mpn:
        return None
    if mpn in records:
        return records[mpn]
    norm = re.sub(r"[/_\-\s]", "", mpn).upper()
    for key, rec in records.items():
        if re.sub(r"[/_\-\s]", "", key).upper() == norm:
            return rec
    return None


def check_lifecycle(
    graph: DesignGraph,
    records: dict[str, LifecycleRecord] | None,
) -> list[Finding]:
    recs = records or {}
    findings: list[Finding] = []
    seen: set[str] = set()
    for ref, comp in sorted(graph.components.items()):
        if comp.component_type in (
            ComponentType.MECHANICAL, ComponentType.FIDUCIAL, ComponentType.TEST_POINT,
        ):
            continue
        mpn = (comp.mpn or "").strip()
        rec = _match_record(mpn, recs)
        if rec is None:
            continue
        if mpn in seen:
            continue
        seen.add(mpn)
        if rec.lifecycle == "eol":
            rec_txt = (
                f"Distributor replacement: {rec.replacement}."
                if rec.replacement else
                "No distributor replacement was listed."
            )
            findings.append(Finding(
                designator=ref,
                mpn=mpn,
                aspect="lifecycle",
                source="lifecycle_check",
                status="WARNING",
                finding=f"{mpn} is EOL/obsolete ({rec.product_status_raw or 'eol'}).",
                why="Distributor ProductStatus, not an LLM equivalent search.",
                recommendation=rec_txt,
                reference=rec.source or "distributor",
                pins=[ref],
                rule_id="PS-LF-001",
            ))
        elif rec.lifecycle == "nrnd":
            findings.append(Finding(
                designator=ref,
                mpn=mpn,
                aspect="lifecycle",
                source="lifecycle_check",
                status="INFO",
                finding=f"{mpn} is NRND ({rec.product_status_raw or 'nrnd'}).",
                why="Distributor ProductStatus.",
                recommendation="Prefer an Active orderable if the design is new.",
                reference=rec.source or "distributor",
                pins=[ref],
                rule_id="PS-LF-002",
            ))
        if rec.rohs_compliant is False:
            findings.append(Finding(
                designator=ref,
                mpn=mpn,
                aspect="lifecycle",
                source="lifecycle_check",
                status="WARNING",
                finding=f"{mpn} is marked RoHS non-compliant.",
                why="RoHS fail only when the distributor flag is explicit.",
                recommendation="Choose a RoHS-compliant orderable of the same MPN family.",
                reference=rec.source or "distributor",
                pins=[ref],
                rule_id="PS-LF-003",
            ))
    return findings
