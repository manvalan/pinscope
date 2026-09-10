"""Enable pins on the fitted variant: no pull and no driver is ERROR.

Runs only when the BOM actually marks DNP/fitted. Enable tied to a rail
is a driver. DNP resistors are removed from the variant graph.
"""

from __future__ import annotations

import re

from backend.pinscopex.models import (
    ComponentConstraints,
    ComponentType,
    DesignGraph,
    Finding,
)
from backend.pinscopex.passive_rail_check import (
    _is_ground_net,
    _is_power_net,
    _pin_name_tokens,
)
from backend.pinscopex.validate import _match_constraints

_EN_RE = re.compile(
    r"(?:^|[_/])(EN|ENA|ENABLE|n?SHDN|nEN|EN_N|CHIP_EN)(?:$|[_/\d])",
    re.I,
)


def _dnp_map(graph: DesignGraph) -> dict[str, bool] | None:
    """Return {ref: is_dnp} if any BOM row carries DNP/fitted, else None."""
    fields = graph.bom_fields or {}
    if not fields:
        return None
    if not any("dnp" in (v or {}) or "fitted" in (v or {}) for v in fields.values()):
        return None
    out: dict[str, bool] = {}
    for ref in graph.components:
        row = fields.get(ref) or {}
        if "dnp" in row:
            out[ref] = bool(row.get("dnp"))
        elif "fitted" in row:
            out[ref] = not bool(row.get("fitted"))
        else:
            out[ref] = False
    return out


def _is_fitted(dnp: dict[str, bool], ref: str) -> bool:
    return not dnp.get(ref, False)


def check_dnp_enables(
    graph: DesignGraph,
    constraints_map: dict[str, ComponentConstraints] | None = None,
) -> list[Finding]:
    dnp = _dnp_map(graph)
    if dnp is None:
        return []
    cmap = constraints_map or {}
    findings: list[Finding] = []
    for ref, comp in sorted(graph.components.items()):
        if not _is_fitted(dnp, ref):
            continue
        if comp.component_type != ComponentType.IC:
            continue
        cons = _match_constraints(comp.mpn or comp.value, cmap)
        for pin_num, net in sorted(comp.pins.items(), key=lambda x: str(x[0])):
            tokens = _pin_name_tokens(cons, pin_num)
            names = tokens or [net or "", pin_num]
            if not any(_EN_RE.search(t) for t in names):
                continue
            if _is_power_net(graph, net) or _is_ground_net(graph, net):
                continue
            has_pull = False
            has_driver = False
            for r in graph.components_on_net(net):
                if r == ref or not _is_fitted(dnp, r):
                    continue
                other = graph.components.get(r)
                if not other:
                    continue
                if other.component_type == ComponentType.IC:
                    has_driver = True
                    continue
                if other.component_type != ComponentType.RESISTOR:
                    continue
                others = {n for n in other.pins.values() if n != net}
                if any(_is_power_net(graph, n) or _is_ground_net(graph, n) for n in others):
                    has_pull = True
            if has_pull or has_driver:
                continue
            variant = (graph.bom_fields.get(ref) or {}).get("variant")
            findings.append(Finding(
                designator=ref,
                mpn=comp.mpn or "",
                aspect="dnp",
                source="dnp_check",
                status="ERROR",
                finding=(
                    f"{ref} enable '{net}' has no fitted pull or driver "
                    f"(DNP parts ignored)."
                ),
                why="On the fitted variant the enable net is floating.",
                recommendation="Fit a pull, tie EN to a rail, or drive it from a PG/GPIO.",
                reference="BOM DNP/fitted",
                net=net,
                pins=[f"{ref}.{pin_num}"],
                rule_id="PS-DNP-001",
                variant=str(variant) if variant else None,
            ))
    return findings
