"""Signal-filter topology: RC, LC, ferrite+C, π (C-L-C), T (L-C-L).

fc is reported only when R/L/C values are known. Sample-rate comparison
and ferrite DCR limits fire only when the neighboring IC specs list them.
Power-rail decoupling is not a signal filter.
"""

from __future__ import annotations

import math
import re

from backend.pinscopex.models import (
    Component,
    ComponentConstraints,
    ComponentType,
    DesignGraph,
    Finding,
    InductorSpecs,
)
from backend.pinscopex.passive_rail_check import (
    _cap_farads,
    _is_ground_net,
    _is_power_net,
    _pin_name_tokens,
    _resistor_ohms,
)
from backend.pinscopex.validate import _match_constraints

_ADC_RATE_KEYS = ("adc_sample_rate", "adc_sample_rate_hz", "data_rate", "data_rate_hz")
_DCR_MAX_KEYS = ("max_ferrite_dcr_ohms", "ferrite_dcr_max_ohms", "max_bead_dcr_ohms")
_ANALOG_RE = re.compile(
    r"(?:^|[_/])(ADC|AIN|VDDA|AVDD|VREF)(?:$|[_/\d])",
    re.IGNORECASE,
)


def _inductor_henries(comp: Component) -> float | None:
    specs = comp.specs
    if isinstance(specs, InductorSpecs) and specs.value_henries:
        return float(specs.value_henries)
    return None


def _dcr_ohms(comp: Component) -> float | None:
    specs = comp.specs
    if isinstance(specs, InductorSpecs) and specs.dcr_ohms is not None:
        return float(specs.dcr_ohms)
    return None


def _is_ferrite(comp: Component) -> bool:
    sub = (comp.component_subtype or "").lower()
    if "ferrite" in sub:
        return True
    specs = comp.specs
    if isinstance(specs, InductorSpecs) and specs.component_subtype:
        return "ferrite" in specs.component_subtype
    return comp.reference.upper().startswith("FB")


def _two_nets(comp: Component) -> tuple[str, str] | None:
    nets = list(dict.fromkeys(comp.pins.values()))
    if len(nets) != 2:
        return None
    return nets[0], nets[1]


def _gnd_caps(graph: DesignGraph, net: str) -> list[tuple[str, float | None]]:
    out: list[tuple[str, float | None]] = []
    for ref in graph.capacitors_on_net(net):
        cap = graph.components[ref]
        others = {n for n in cap.pins.values() if n != net}
        if any(_is_ground_net(graph, n) for n in others):
            out.append((ref, _cap_farads(cap)))
    return out


def _sum_known_c(caps: list[tuple[str, float | None]]) -> float | None:
    vals = [c for _, c in caps if c is not None]
    if not vals or len(vals) != len(caps):
        return None
    return sum(vals)


def _fc_rc(r: float, c: float) -> float:
    return 1.0 / (2.0 * math.pi * r * c)


def _fc_lc(l: float, c: float) -> float:
    return 1.0 / (2.0 * math.pi * math.sqrt(l * c))


def _ic_specs_values(comp: Component) -> dict:
    specs = comp.specs
    values = getattr(specs, "values", None) if specs else None
    return values if isinstance(values, dict) else {}


def _adc_rate_hz(graph: DesignGraph, ic_refs: list[str]) -> float | None:
    for ref in ic_refs:
        values = _ic_specs_values(graph.components[ref])
        for key in _ADC_RATE_KEYS:
            raw = values.get(key)
            if raw is None:
                continue
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
    return None


def _dcr_limit_ohms(graph: DesignGraph, ic_refs: list[str]) -> float | None:
    for ref in ic_refs:
        values = _ic_specs_values(graph.components[ref])
        for key in _DCR_MAX_KEYS:
            raw = values.get(key)
            if raw is None:
                continue
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
    return None


def _ic_refs_on(graph: DesignGraph, *nets: str) -> list[str]:
    refs: list[str] = []
    for net in nets:
        for r in graph.components_on_net(net):
            c = graph.components.get(r)
            if c and c.component_type == ComponentType.IC and r not in refs:
                refs.append(r)
    return refs


def _analog_net(
    graph: DesignGraph,
    constraints_map: dict[str, ComponentConstraints],
    *nets: str,
) -> str | None:
    for net in nets:
        if _ANALOG_RE.search(net or ""):
            return net
        for ref in _ic_refs_on(graph, net):
            cons = _match_constraints(graph.components[ref].mpn or "", constraints_map)
            for pin_num, pin_net in graph.components[ref].pins.items():
                if pin_net != net:
                    continue
                if _ANALOG_RE.search(net):
                    return net
                for tok in _pin_name_tokens(cons, pin_num):
                    if _ANALOG_RE.search(tok):
                        return net
    return None


def _filter_finding(
    *,
    kind: str,
    fc: float | None,
    designator: str,
    mpn: str,
    net: str,
    extra_why: str,
    adc_hz: float | None,
) -> Finding:
    if fc is None:
        return Finding(
            designator=designator,
            mpn=mpn,
            aspect="filter",
            source="filter_check",
            status="INFO",
            finding=f"{kind} filter on '{net}' ({designator}); fc unknown (missing L/C/R value).",
            why=extra_why,
            recommendation="Populate passive values to compute cutoff.",
            reference="netlist topology",
            net=net,
            pins=[designator],
            rule_id="PS-FLT-001",
        )
    if adc_hz is not None and not (0.1 * adc_hz <= fc <= 20 * adc_hz):
        return Finding(
            designator=designator,
            mpn=mpn,
            aspect="filter",
            source="filter_check",
            status="WARNING",
            finding=(
                f"{kind} filter on '{net}' has fc ≈ {fc:.3g} Hz vs ADC/data rate "
                f"{adc_hz:.3g} Hz."
            ),
            why=extra_why + " Compared only because the IC specs list a sample/data rate.",
            recommendation="Adjust R/C (or L) so fc sits nearer the sample rate, or confirm anti-alias intent.",
            reference="netlist topology",
            net=net,
            pins=[designator],
            rule_id="PS-FLT-002",
        )
    rec = (
        "fc is within a wide band of the IC sample/data rate."
        if adc_hz is not None
        else "Verify fc against the analog bandwidth; no datasheet rate was present."
    )
    return Finding(
        designator=designator,
        mpn=mpn,
        aspect="filter",
        source="filter_check",
        status="INFO",
        finding=f"{kind} filter on '{net}' ({designator}), fc ≈ {fc:.3g} Hz.",
        why=extra_why,
        recommendation=rec,
        reference="netlist topology",
        net=net,
        pins=[designator],
        rule_id="PS-FLT-001",
    )


def _emit(
    findings: list[Finding],
    seen: set[tuple[str, str]],
    *,
    kind: str,
    ref: str,
    net: str,
    fc: float | None,
    mpn: str,
    extra_why: str,
    adc_hz: float | None,
) -> None:
    key = (kind, ref)
    if key in seen:
        return
    seen.add(key)
    findings.append(_filter_finding(
        kind=kind, fc=fc, designator=ref, mpn=mpn, net=net,
        extra_why=extra_why, adc_hz=adc_hz,
    ))


def check_filters(
    graph: DesignGraph,
    constraints_map: dict[str, ComponentConstraints] | None = None,
) -> list[Finding]:
    cmap = constraints_map or {}
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    used_l: set[str] = set()

    # T: two series L sharing a middle net that has C to GND.
    for mid in sorted(graph.nets):
        if _is_ground_net(graph, mid):
            continue
        caps = _gnd_caps(graph, mid)
        if not caps:
            continue
        inds = [
            r for r in graph.components_on_net(mid)
            if (c := graph.components.get(r)) is not None
            and c.component_type == ComponentType.INDUCTOR
        ]
        if len(inds) != 2:
            continue
        ends: list[str] = []
        ok = True
        for r in inds:
            pair = _two_nets(graph.components[r])
            if not pair:
                ok = False
                break
            other = pair[1] if pair[0] == mid else pair[0]
            if _is_ground_net(graph, other):
                ok = False
                break
            ends.append(other)
        if not ok:
            continue
        lvals = [_inductor_henries(graph.components[r]) for r in inds]
        c_f = _sum_known_c(caps)
        l_eq = sum(lvals) if all(lvals) else None  # type: ignore[arg-type]
        fc = _fc_lc(l_eq, c_f) if l_eq and c_f else None
        ics = _ic_refs_on(graph, mid, *ends)
        _emit(
            findings, seen, kind="T", ref="+".join(sorted(inds)), net=mid,
            fc=fc, mpn=graph.components[inds[0]].mpn or "",
            extra_why="T network (L-C-L).",
            adc_hz=_adc_rate_hz(graph, ics),
        )
        used_l.update(inds)

    for ref, comp in sorted(graph.components.items()):
        if comp.component_type != ComponentType.INDUCTOR or ref in used_l:
            continue
        pair = _two_nets(comp)
        if not pair:
            continue
        n1, n2 = pair
        if _is_ground_net(graph, n1) or _is_ground_net(graph, n2):
            continue
        c1, c2 = _gnd_caps(graph, n1), _gnd_caps(graph, n2)
        ferrite = _is_ferrite(comp)
        lval = _inductor_henries(comp)
        ics = _ic_refs_on(graph, n1, n2)
        adc = _adc_rate_hz(graph, ics)
        if c1 and c2:
            if _is_power_net(graph, n1) and _is_power_net(graph, n2) and not ferrite:
                continue
            s1, s2 = _sum_known_c(c1), _sum_known_c(c2)
            c_eq = None
            if s1 and s2:
                c_eq = 1.0 / (1.0 / s1 + 1.0 / s2)
            fc = _fc_lc(lval, c_eq) if lval and c_eq else None
            _emit(
                findings, seen, kind="π", ref=ref, net=n1, fc=fc,
                mpn=comp.mpn or "", extra_why="π network (C-L-C).", adc_hz=adc,
            )
        elif c1 or c2:
            filt_net = n1 if c1 else n2
            if _is_power_net(graph, filt_net) and not ferrite:
                continue
            caps = c1 or c2
            c_f = _sum_known_c(caps)
            fc = _fc_lc(lval, c_f) if lval and c_f else None
            kind = "ferrite+C" if ferrite else "LC"
            _emit(
                findings, seen, kind=kind, ref=ref, net=filt_net, fc=fc,
                mpn=comp.mpn or "",
                extra_why="Series L/ferrite with shunt C to ground.",
                adc_hz=adc,
            )
        analog = _analog_net(graph, cmap, n1, n2)
        limit = _dcr_limit_ohms(graph, ics)
        dcr = _dcr_ohms(comp)
        if ferrite and analog and limit is not None and dcr is not None and dcr > limit:
            findings.append(Finding(
                designator=ref,
                mpn=comp.mpn or "",
                aspect="filter",
                source="filter_check",
                status="WARNING",
                finding=(
                    f"{ref} ferrite DCR {dcr:.3g} Ω on analog net '{analog}' "
                    f"exceeds {limit:.3g} Ω."
                ),
                why="Bead DCR vs the IC spec limit on an analog/ADC rail.",
                recommendation="Use a lower-DCR bead specified for analog, or 0 Ω.",
                reference="IC specs",
                net=analog,
                pins=[ref],
                rule_id="PS-FLT-003",
            ))

    for ref, comp in sorted(graph.components.items()):
        if comp.component_type != ComponentType.RESISTOR:
            continue
        pair = _two_nets(comp)
        if not pair:
            continue
        n1, n2 = pair
        if _is_power_net(graph, n1) or _is_power_net(graph, n2):
            continue
        if _is_ground_net(graph, n1) or _is_ground_net(graph, n2):
            continue
        c1, c2 = _gnd_caps(graph, n1), _gnd_caps(graph, n2)
        if bool(c1) == bool(c2):
            continue
        filt_net, src_net, caps = (n1, n2, c1) if c1 else (n2, n1, c2)
        if _is_power_net(graph, filt_net):
            continue
        r_ohm = _resistor_ohms(comp)
        c_f = _sum_known_c(caps)
        fc = _fc_rc(r_ohm, c_f) if r_ohm and c_f else None
        ics = _ic_refs_on(graph, src_net, filt_net)
        _emit(
            findings, seen, kind="RC", ref=ref, net=filt_net, fc=fc,
            mpn=comp.mpn or "", extra_why="Series R, shunt C to ground (low-pass).",
            adc_hz=_adc_rate_hz(graph, ics),
        )

    return findings
