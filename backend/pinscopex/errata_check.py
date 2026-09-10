"""Errata workarounds from a known-URL catalog. No HTML scrape."""

from __future__ import annotations

import logging
import re

from backend.pinscopex.models import ComponentConstraints, DesignGraph, Finding
from backend.pinscopex.passive_rail_check import (
    _pin_name_tokens,
    _resistor_to_power,
)
from backend.pinscopex.validate import _match_constraints

log = logging.getLogger(__name__)

# Exact MPN → URL + structured workarounds. Empty by default so eval is quiet.
DEFAULT_ERRATA_CATALOG: dict[str, dict] = {}


def check_errata(
    graph: DesignGraph,
    constraints_map: dict[str, ComponentConstraints] | None,
    catalog: dict[str, dict] | None = None,
) -> list[Finding]:
    cmap = constraints_map or {}
    cat = DEFAULT_ERRATA_CATALOG if catalog is None else catalog
    findings: list[Finding] = []
    for ref, comp in sorted(graph.components.items()):
        mpn = (comp.mpn or "").strip()
        if not mpn:
            continue
        entry = cat.get(mpn)
        if entry is None:
            continue
        url = (entry.get("url") or "").strip()
        if not url:
            log.info("errata skip %s: catalog row has no url", mpn)
            continue
        cons = _match_constraints(mpn, cmap)
        for wa in entry.get("workarounds") or []:
            kind = (wa.get("kind") or "").lower()
            pin_name = (wa.get("pin_name") or "").strip()
            if kind != "pullup" or not pin_name:
                continue
            net = _net_for_pin_name(graph, ref, cons, pin_name)
            if not net:
                continue
            if _resistor_to_power(graph, net):
                continue
            findings.append(Finding(
                designator=ref,
                mpn=mpn,
                aspect="errata",
                source="errata_check",
                status="WARNING",
                finding=(
                    f"{ref} {pin_name} is missing the errata pull-up on '{net}'."
                ),
                why=wa.get("note") or "Vendor errata workaround is not on the schematic.",
                recommendation="Add the pull-up described in the errata, or confirm the die revision.",
                reference=url,
                net=net,
                pins=[f"{ref}.{pin_name}"],
                rule_id="PS-ERRATA-001",
            ))
    return findings


def _net_for_pin_name(
    graph: DesignGraph,
    ref: str,
    cons: ComponentConstraints | None,
    pin_name: str,
) -> str | None:
    comp = graph.components.get(ref)
    if not comp:
        return None
    want = pin_name.upper()
    for pin_num, net in comp.pins.items():
        if (net or "").upper() == want:
            return net
        tokens = _pin_name_tokens(cons, pin_num)
        if any(t.upper() == want or _token_match(t, pin_name) for t in tokens):
            return net
    return None


def _token_match(token: str, pin_name: str) -> bool:
    return bool(re.search(rf"(?:^|[_/]){re.escape(pin_name)}(?:$|[_/\d])", token, re.I))
