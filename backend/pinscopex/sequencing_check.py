"""Power-good → enable sequencing when the IC specs declare a sequence.

No RC time constants are invented. Missing power_sequence means skip.
"""

from __future__ import annotations

import re

from backend.pinscopex.models import (
    ComponentConstraints,
    ComponentType,
    DesignGraph,
    Finding,
)
from backend.pinscopex.thermal_check import (
    _VIN_PIN,
    _VOUT_PIN,
    _is_ldo,
    _pin_net_by_role,
    _specs_values,
)
from backend.pinscopex.validate import _match_constraints

_PG_RE = re.compile(r"(?:^|[_/])(PG|PGOOD|PWRGD|POWER_GOOD|POK)(?:$|[_/\d])", re.I)
_EN_RE = re.compile(
    r"(?:^|[_/])(EN|ENA|ENABLE|n?SHDN|nEN|EN_N)(?:$|[_/\d])",
    re.I,
)


def _has_sequence(comp) -> bool:
    values = _specs_values(comp)
    raw = values.get("power_sequence")
    if raw is None or raw == "" or raw is False:
        return False
    if isinstance(raw, (int, float)) and raw == 0:
        return False
    return True


def check_power_sequencing(
    graph: DesignGraph,
    constraints_map: dict[str, ComponentConstraints] | None = None,
) -> list[Finding]:
    cmap = constraints_map or {}
    regs: list[tuple] = []
    for ref, comp in sorted(graph.components.items()):
        if comp.component_type != ComponentType.IC:
            continue
        cons = _match_constraints(comp.mpn or comp.value, cmap)
        vin = _pin_net_by_role(graph, comp, cons, _VIN_PIN)
        vout = _pin_net_by_role(graph, comp, cons, _VOUT_PIN)
        if not (vin and vout) and not _is_ldo(comp, cons):
            continue
        pg = _pin_net_by_role(graph, comp, cons, _PG_RE, exclude_re=None)
        en = _pin_net_by_role(graph, comp, cons, _EN_RE, exclude_re=None)
        regs.append((ref, comp, cons, vin, vout, pg, en))

    findings: list[Finding] = []
    for dref, dcomp, dcons, dvin, _dvout, _dpg, den in regs:
        if not _has_sequence(dcomp):
            continue
        if not den or not dvin:
            continue
        upstream = [
            row for row in regs
            if row[0] != dref and row[4] and row[4] == dvin
        ]
        if not upstream:
            continue
        uref, ucomp, _ucons, _uvin, _uvout, upg, _uen = upstream[0]
        if not upg:
            findings.append(Finding(
                designator=dref,
                mpn=dcomp.mpn or "",
                aspect="sequencing",
                source="sequencing_check",
                status="WARNING",
                finding=(
                    f"{dref} specs declare power_sequence but upstream {uref} "
                    f"has no PG pin feeding {dref} EN '{den}'."
                ),
                why="Sequence was listed in IC specs; delay milliseconds were not estimated.",
                recommendation="Tie the upstream power-good to this enable, or remove the sequence spec if unused.",
                reference="power_sequence",
                net=den,
                pins=[dref, uref],
                rule_id="PS-SEQ-001",
            ))
            continue
        if upg != den:
            findings.append(Finding(
                designator=dref,
                mpn=dcomp.mpn or "",
                aspect="sequencing",
                source="sequencing_check",
                status="WARNING",
                finding=(
                    f"{uref} PG '{upg}' does not connect to {dref} EN '{den}'."
                ),
                why="Declared power_sequence expects PG to enable the next rail.",
                recommendation="Net the upstream PG to the downstream EN.",
                reference="power_sequence",
                net=den,
                pins=[f"{uref}", f"{dref}"],
                rule_id="PS-SEQ-001",
            ))
    return findings
