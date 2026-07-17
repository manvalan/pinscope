"""Deterministic pin-mux feasibility check.

For each IC pin whose net name asserts a peripheral function (e.g. a net named
``MCU-UART5-TX`` asserts ``UART5_TX``), verify that the pin can actually be
configured for that function per the datasheet alternate-function table.  A pin
that exposes peripheral P but *not* the asserted signal S (e.g. PD2 exposes
UART5 only as ``UART5_RX``) cannot be muxed to S — a hard, context-free defect.

This is a FEASIBILITY check, never a DIRECTION check.  It makes no claim about
whether a TX should connect to a peer's RX (direct-UART crossover) or TX
(transceiver/isolator straight-through) — that is context-dependent and left to
the agentic reviewer.  To stay sound it SKIPS any net that also lands on another
IC exposing the same peripheral (an inter-device link, where the net name's
perspective is ambiguous).
"""

from __future__ import annotations

from backend.pinscopex.models import (
    ComponentConstraints,
    ComponentType,
    DesignGraph,
    Finding,
)
from backend.pinscopex.pin_function_tokens import (
    complement,
    normalize_functions,
    parse_net_token,
    signals_for_peripheral,
)
from backend.pinscopex.validate import _match_constraints


def check_pin_mux_feasibility(
    graph: DesignGraph,
    constraints_map: dict[str, ComponentConstraints],
) -> list[Finding]:
    """Flag IC pins assigned a peripheral function their silicon can't route."""
    findings: list[Finding] = []

    for ref, comp in sorted(graph.components.items()):
        if comp.component_type != ComponentType.IC:
            continue
        cons = _match_constraints(comp.mpn or comp.value, constraints_map)
        if not cons:
            continue

        for pin_num, net_name in comp.pins.items():
            token = parse_net_token(net_name)
            if token is None:
                continue
            peripheral, signal = token

            pin = cons.pin_by_number(pin_num)
            if pin is None or not pin.functions:
                continue
            exposed = signals_for_peripheral(
                normalize_functions(pin.functions), peripheral
            )
            if not exposed:
                continue  # pin doesn't expose this peripheral at all — not our case
            if signal in exposed:
                continue  # feasible; any direction question is the reviewer's call

            # Pin exposes the peripheral but NOT the asserted signal -> infeasible.
            # Gate: skip if another IC pin on this net also exposes the peripheral
            # (inter-device same-peripheral link — could be a legitimate crossover
            # or transceiver straight-through; leave it to the agentic reviewer).
            if _peer_exposes_peripheral(
                graph, constraints_map, net_name, ref, peripheral
            ):
                continue

            findings.append(
                _feasibility_finding(
                    ref, comp.mpn or "", pin_num, pin.name,
                    net_name, peripheral, signal, exposed, pin.functions,
                )
            )

    return findings


def _peer_exposes_peripheral(
    graph: DesignGraph,
    constraints_map: dict[str, ComponentConstraints],
    net_name: str,
    self_ref: str,
    peripheral: str,
) -> bool:
    """True if any *other* IC pin on this net exposes the given peripheral."""
    net = graph.nets.get(net_name)
    if not net:
        return False
    for pc in net.pins:
        if pc.component_ref == self_ref:
            continue
        other = graph.components.get(pc.component_ref)
        if not other or other.component_type != ComponentType.IC:
            continue
        ocons = _match_constraints(other.mpn or other.value, constraints_map)
        if not ocons:
            continue
        opin = ocons.pin_by_number(pc.pin_number)
        if opin is None or not opin.functions:
            continue
        if signals_for_peripheral(normalize_functions(opin.functions), peripheral):
            return True
    return False


def _feasibility_finding(
    ref: str,
    mpn: str,
    pin_num: str,
    pin_name: str,
    net_name: str,
    peripheral: str,
    signal: str,
    exposed: set[str],
    functions: list[str],
) -> Finding:
    # Full alternate-function list, verbatim from the datasheet and in datasheet
    # order — NOT our canonicalized tokens.  Printing the raw strings keeps the
    # finding self-auditing: a reader (or a future us) can spot a naming synonym
    # we haven't taught the tokenizer yet (this is how the SPI PICO/POCI==MOSI/MISO
    # false positive slipped through — the finding only showed the derived subset).
    functions_str = ", ".join(functions) if functions else "(none listed)"
    comp_sig = complement(signal)
    is_swap = bool(comp_sig and comp_sig in exposed)

    swap_hint = ""
    rec = (
        f"Move '{net_name}' to a pin whose alternate functions include "
        f"{peripheral}_{signal}."
    )
    if is_swap:
        swap_hint = (
            f" This pin's {peripheral} role is {peripheral}_{comp_sig} — the "
            f"complement of {peripheral}_{signal} — so the {signal}/{comp_sig} "
            f"nets are most likely swapped."
        )
        rec = (
            f"Move '{net_name}' to a {peripheral}_{signal}-capable pin, or swap "
            f"it with the paired {peripheral}_{comp_sig} net if that resolves both."
        )

    return Finding(
        designator=ref,
        mpn=mpn,
        aspect="pin_mux",
        source="pin_mux_check",
        source_page=None,
        status="ERROR",
        finding=(
            f"Net '{net_name}' assigns {ref} pin {pin_num} ({pin_name}) the "
            f"{peripheral}_{signal} function, but this pin cannot be muxed as "
            f"{peripheral}_{signal}."
        ),
        why=(
            f"The intended function {peripheral}_{signal} was inferred from the "
            f"net name '{net_name}'. Per the datasheet alternate-function table, "
            f"pin {pin_num} ({pin_name}) can be muxed as: {functions_str}. "
            f"{peripheral}_{signal} is not in that list, so the silicon cannot "
            f"route it here regardless of downstream wiring." + swap_hint +
            f" If '{net_name}' is not actually configured for {peripheral} in "
            f"firmware (e.g. bit-banged GPIO, or a label carried over from the "
            f"connected part), disregard this finding."
        ),
        recommendation=rec,
        reference=f"{mpn or ref} alternate-function table",
    )
