"""Open-drain / on-die pull-up pins from extracted internal_features."""

from __future__ import annotations

from backend.pinscopex.models import ComponentConstraints, DesignGraph, Finding
from backend.pinscopex.passive_rail_check import (
    _pin_name_tokens,
    _resistor_to_power,
)
from backend.pinscopex.validate import _match_constraints


def check_internal_features(
    graph: DesignGraph,
    constraints_map: dict[str, ComponentConstraints] | None = None,
) -> list[Finding]:
    cmap = constraints_map or {}
    findings: list[Finding] = []
    for ref, comp in sorted(graph.components.items()):
        cons = _match_constraints(comp.mpn or comp.value, cmap)
        feats = cons.internal_features if cons else None
        if not feats or not feats.pullup_pins:
            continue
        for pin_name in feats.pullup_pins:
            net = None
            for pin_num, n in comp.pins.items():
                tokens = _pin_name_tokens(cons, pin_num)
                names = tokens or [n or "", str(pin_num)]
                if any(
                    t.upper() == pin_name.upper() or (n or "").upper() == pin_name.upper()
                    for t in names
                ):
                    net = n
                    break
            if not net:
                continue
            if _resistor_to_power(graph, net):
                continue
            findings.append(Finding(
                designator=ref,
                mpn=comp.mpn or "",
                aspect="internal_features",
                source="internal_features_check",
                status="WARNING",
                finding=(
                    f"{ref} {pin_name} is listed as needing an external pull-up "
                    f"and net '{net}' has none."
                ),
                why="internal_features.pullup_pins from the datasheet block diagram.",
                recommendation="Add a pull-up to the I/O rail, or confirm an on-die pull is enabled.",
                reference="internal_features",
                net=net,
                pins=[f"{ref}.{pin_name}"],
                rule_id="PS-INT-001",
            ))
    return findings
