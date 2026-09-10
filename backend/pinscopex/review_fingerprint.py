"""Stable per-IC neighborhood hash so a second review can skip unchanged chips."""

from __future__ import annotations

import hashlib
import json

from backend.pinscopex.models import ComponentType, DesignGraph
from backend.pinscopex.validate import _match_constraints


def ic_neighborhood_fingerprint(
    graph: DesignGraph,
    ref: str,
    constraints_map: dict | None = None,
) -> str | None:
    """Hash MPN, pin→net, 1-hop neighbors, and extraction model_version.

    Returns None if *ref* is not an IC. Neighbor changes (pull-up added on
    SDA, etc.) invalidate every IC on that net.
    """
    comp = graph.components.get(ref)
    if not comp or comp.component_type != ComponentType.IC:
        return None
    pins = tuple(sorted((str(p), n) for p, n in comp.pins.items()))
    neighbors: list[tuple[str, str, str, str]] = []
    for _pin, net_name in pins:
        for other in graph.components_on_net(net_name):
            if other == ref:
                continue
            o = graph.components[other]
            o_pins_on_net = tuple(
                sorted(str(p) for p, n in o.pins.items() if n == net_name)
            )
            neighbors.append(
                (other, o.mpn or "", o.component_type.value, ",".join(o_pins_on_net))
            )
    model_version = ""
    cons = _match_constraints(comp.mpn or comp.value, constraints_map or {})
    if cons is not None:
        model_version = getattr(cons, "model_version", "") or ""
    payload = {
        "mpn": comp.mpn or "",
        "pins": pins,
        "neighbors": tuple(sorted(neighbors)),
        "model_version": model_version,
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()


def graph_ic_fingerprints(
    graph: DesignGraph,
    constraints_map: dict | None = None,
) -> dict[str, str]:
    out: dict[str, str] = {}
    for ref, comp in graph.components.items():
        if comp.component_type != ComponentType.IC:
            continue
        fp = ic_neighborhood_fingerprint(graph, ref, constraints_map)
        if fp:
            out[ref] = fp
    return out


def skip_unchanged_ics(
    completed_refs: set[str],
    previous: dict[str, str],
    current: dict[str, str],
) -> set[str]:
    """Keep skip only for completed ICs whose neighborhood hash is unchanged."""
    skip: set[str] = set()
    for ref in completed_refs:
        if ref in current and previous.get(ref) == current[ref]:
            skip.add(ref)
    return skip
