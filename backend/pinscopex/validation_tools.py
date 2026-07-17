"""Graph-query tools for direct datasheet review.

Tools let the reviewer trace connections beyond the pre-built
component context.  The submit_review tool collects all findings.
"""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.pinscopex.models import (
    ComponentConstraints,
    DesignGraph,
)
from backend.pinscopex.utils import safe_mpn

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pin_sort_key(pin: str) -> tuple:
    m = re.match(r"^(\d+)", pin)
    if m:
        return (0, int(m.group(1)), pin)
    return (1, 0, pin)


_THERMAL_PAD_NAME_RE = re.compile(
    r"\b(e[\s\-]?pad|epad|ep|dap|thermal\s*pad|exposed\s*(?:pad|paddle)|die[\s\-]?(?:attach\s*)?pad)\b",
    re.IGNORECASE,
)


def _reviewer_voltage_str(net) -> str:
    """Format a net's voltage for reviewer tool output."""
    if net is None or net.voltage is None:
        return ""
    return f", {net.voltage}V"


def _is_thermal_pad_pin(pin) -> bool:
    """Heuristic: does a pintable entry describe the exposed/thermal pad?

    Users commonly assign the EP a custom pin number in their schematic
    symbol (often pin_count+1) that doesn't match the datasheet pintable's
    number for the same pad. Detecting EP pintable entries lets the
    reviewer match them to orphan schematic pins instead of reporting them
    as unconnected.
    """
    for field in (getattr(pin, "name", None), getattr(pin, "description", None)):
        if field and _THERMAL_PAD_NAME_RE.search(str(field)):
            return True
    number = str(getattr(pin, "number", "")).strip()
    if number and not number.isdigit() and _THERMAL_PAD_NAME_RE.search(number):
        return True
    return False


def _format_specs(specs) -> str:
    """Format component specs as a compact string."""
    if not specs:
        return ""
    d = specs.model_dump(exclude_none=True, exclude={"specs_type"})
    if not d:
        return ""
    parts = []
    for k, v in d.items():
        parts.append(f"{k}={v}")
    return ", ".join(parts)


# Type alias for constraints lookup
ConstraintsMap = dict[str, ComponentConstraints]  # MPN -> constraints


# ---------------------------------------------------------------------------
# Excerpt tool — per-review state, topic regexes, page selection
# ---------------------------------------------------------------------------

# Each topic maps to a narrow keyword regex used to pick relevant pages from
# a neighbor IC's datasheet. Narrower than _REVIEW_KEYWORDS so an excerpt
# fetch returns a focused slice (~5-10 pages) rather than 30+.
EXCERPT_TOPICS: dict[str, re.Pattern] = {
    "absolute_max": re.compile(
        r"absolute\s+maximum|maximum\s+ratings?|stress\s+rating",
        re.IGNORECASE,
    ),
    "recommended_operating": re.compile(
        r"recommended\s+operating|operating\s+conditions?|operating\s+range",
        re.IGNORECASE,
    ),
    "electrical_characteristics": re.compile(
        r"electrical\s+characteristics?|DC\s+characteristics?|AC\s+characteristics?"
        r"|V[IO][HL]\s*\(|input\s+(high|low)\s+voltage|output\s+(high|low)\s+voltage",
        re.IGNORECASE,
    ),
    "pin_voltage_levels": re.compile(
        r"5[\s\-]?V[\s\-]?tolerant|5V[\s\-]?tolerance|voltage\s+tolerance"
        r"|input\s+voltage\s+range|pin\s+voltage|I/O\s+voltage"
        r"|V[IO][HL]\b|VIO\b|VDDIO\b|tolerant\s+input",
        re.IGNORECASE,
    ),
    "power_supply": re.compile(
        r"power\s+supply|supply\s+voltage|VDD|VCC|VBAT|supply\s+current"
        r"|quiescent\s+current",
        re.IGNORECASE,
    ),
    "thermal": re.compile(
        r"thermal\s+(resistance|shutdown|pad|characteristics)|junction\s+temperature"
        r"|theta[\s\-]?J[AC]|θJ[AC]",
        re.IGNORECASE,
    ),
    "application_circuit": re.compile(
        r"application\s+(circuit|schematic|information|note)"
        r"|typical\s+application|reference\s+design|recommended\s+circuit",
        re.IGNORECASE,
    ),
}

_EXCERPT_MAX_PAGES_PER_FETCH = 10  # cap per single excerpt call


@dataclass
class ExcerptState:
    """Per-review state threaded through ``execute_tool`` so the excerpt tool
    can enforce neighbor-only access, run a fetch/page budget, and reuse
    pypdf trim work across ICs in the same validation run.

    Created in ``review_ic_async``; carries the cross-IC ``cache`` from the
    caller (``validate_design_async``).
    """

    current_ic: str
    connected_designators: set[str]
    graph: DesignGraph
    pdf_dir: Path
    storage: Any | None = None
    # Cross-IC trimmed-PDF cache keyed by (designator, topic, ds_md5)
    # -> (trimmed_pdf_path, [original_page_numbers]). Lives for the duration
    # of one validate_design_async.
    cache: dict[tuple[str, str, str], tuple[str, list[int]]] = field(
        default_factory=dict
    )
    # Per-review budget counters. ``page_budget`` is the global ceiling that
    # bounds total fan-out on a hub IC; ``per_neighbor_page_budget`` is a
    # sub-budget so that verifying ONE interface (which needs ~2-3 topic
    # fetches from a single neighbor — e.g. pin_voltage_levels + absolute_max)
    # is never blocked by pages already spent on a *different* neighbor. This
    # is the fix for the U2-001 / U3-001 false positives, where a single
    # 25-page global budget got exhausted before the abs-max table could be
    # read, forcing the reviewer to guess.
    fetch_count: int = 0
    page_count: int = 0
    fetch_budget: int = 8
    page_budget: int = 60
    per_neighbor_page_budget: int = 30
    pages_per_neighbor: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def find_connected_components(
    graph: DesignGraph,
    constraints_map: ConstraintsMap,
    designator: str,
    pin: str,
    designator_filter: str | None = None,
) -> str:
    """Find all components on the net at designator.pin, with full specs."""
    comp = graph.components.get(designator)
    if not comp:
        return f"Component '{designator}' not found."

    net_name = comp.pins.get(str(pin))
    if not net_name:
        return f"Pin {pin} on {designator} is not connected in the netlist."

    net = graph.nets[net_name]
    voltage_str = _reviewer_voltage_str(net)
    lines = [f"Net: {net_name} ({net.net_type.value}{voltage_str})"]

    count = 0
    for pc in net.pins:
        if pc.component_ref == designator:
            continue
        if designator_filter and not pc.component_ref.upper().startswith(designator_filter.upper()):
            continue

        neighbor = graph.components.get(pc.component_ref)
        if not neighbor:
            continue
        count += 1

        # Component header
        mpn_str = f", MPN={neighbor.mpn}" if neighbor.mpn else ""
        sub_str = f", {neighbor.component_subtype}" if neighbor.component_subtype else ""
        specs_str = _format_specs(neighbor.specs)
        if specs_str:
            specs_str = f" ({specs_str})"

        lines.append(
            f"  {neighbor.reference}: {neighbor.value}{mpn_str}, "
            f"{neighbor.component_type.value}{sub_str}{specs_str}"
        )

        # Pin map
        pin_strs = []
        for pn, pnet in sorted(neighbor.pins.items(), key=lambda x: _pin_sort_key(x[0])):
            pin_strs.append(f"{pn}->{pnet}")
        lines.append(f"    pins: {', '.join(pin_strs)}")

    if count == 0:
        filter_note = f" matching '{designator_filter}*'" if designator_filter else ""
        lines.append(f"  (no components{filter_note} on this net)")

    return "\n".join(lines)


def get_net_for_pin(
    graph: DesignGraph,
    constraints_map: ConstraintsMap,
    designator: str,
    pin: str,
) -> str:
    """Get net info for a specific pin — lightweight, no component listing."""
    comp = graph.components.get(designator)
    if not comp:
        return f"Component '{designator}' not found."

    net_name = comp.pins.get(str(pin))
    if not net_name:
        return f"Pin {pin} on {designator} is not connected in the netlist."

    net = graph.nets[net_name]
    voltage_str = _reviewer_voltage_str(net)

    # Get pin name from constraints
    pin_name = ""
    constraints = constraints_map.get(comp.mpn or "")
    if constraints:
        p = constraints.pin_by_number(pin)
        if p:
            pin_name = f" ({p.name})"

    return f"Pin {pin}{pin_name} on {designator} -> {net_name} [{net.net_type.value}{voltage_str}]"


def get_pintable(
    graph: DesignGraph,
    constraints_map: ConstraintsMap,
    designator: str,
) -> str:
    """Get full pintable with connection status."""
    comp = graph.components.get(designator)
    if not comp:
        return f"Component '{designator}' not found."

    constraints = constraints_map.get(comp.mpn or "")
    if not constraints:
        # Fall back to just showing netlist pins
        lines = [f"Pintable for {designator} ({comp.mpn or comp.value}) — no extracted pintable:"]
        for pn, pnet in sorted(comp.pins.items(), key=lambda x: _pin_sort_key(x[0])):
            net = graph.nets.get(pnet)
            ntype = f" [{net.net_type.value}]" if net else ""
            lines.append(f"  Pin {pn}: -> {pnet}{ntype} [connected]")
        return "\n".join(lines)

    lines = [f"Pintable for {designator} ({comp.mpn}):"]
    matched: set[str] = set()
    for p in sorted(constraints.pintable, key=lambda x: _pin_sort_key(str(x.number))):
        net_name = comp.pins.get(str(p.number))
        func_str = f"  [alt: {', '.join(p.functions)}]" if p.functions else ""
        if net_name:
            matched.add(str(p.number))
            net = graph.nets.get(net_name)
            voltage_str = _reviewer_voltage_str(net)
            ntype = net.net_type.value if net else "?"
            lines.append(f"  Pin {p.number} ({p.name}): -> {net_name} [{ntype}{voltage_str}]{func_str} [connected]")
        else:
            tp_note = " [likely exposed pad — check orphan schematic pins below]" if _is_thermal_pad_pin(p) else ""
            lines.append(f"  Pin {p.number} ({p.name}){func_str}: [unconnected]{tp_note}")

    orphans = [pn for pn in comp.pins if pn not in matched]
    if orphans:
        lines.append("")
        lines.append(
            "Additional schematic pins (not in datasheet pintable — "
            "commonly the EP/thermal pad under a user-chosen pin number):"
        )
        for pn in sorted(orphans, key=_pin_sort_key):
            net_name = comp.pins.get(pn) or ""
            net = graph.nets.get(net_name)
            voltage_str = _reviewer_voltage_str(net)
            ntype = net.net_type.value if net else "?"
            lines.append(f"  Pin {pn}: -> {net_name} [{ntype}{voltage_str}]")

    return "\n".join(lines)


def _resolve_neighbor_pdf(
    state: ExcerptState,
    mpn: str,
) -> Path | None:
    """Resolve a neighbor IC's MPN to a local PDF path.

    Mirrors validation._find_pdf's local-then-library lookup so neighbor
    datasheets follow the same resolution rules as the IC under review.
    """
    safe = safe_mpn(mpn)
    local = state.pdf_dir / f"{safe}.pdf"
    if local.is_file():
        return local
    if state.storage is not None:
        try:
            from backend.services import projects as proj_svc
            lib_key = proj_svc.library_has_datasheet(state.storage, mpn)
            if lib_key:
                state.storage.download_to_local(lib_key, local)
                if local.is_file():
                    return local
        except Exception:
            log.exception("excerpt: library lookup failed for %s", mpn)
    return None


def _trim_pdf_by_keywords(
    pdf_path: Path,
    keyword_re: re.Pattern,
    max_pages: int,
) -> tuple[str, list[int]]:
    """Pypdf-trim a PDF to pages matching a keyword regex (+/-1 neighbors).

    Returns ``(trimmed_pdf_path, kept_page_numbers_1indexed)``. The trimmed
    path is a temp file the caller is responsible for cleaning up *eventually*
    — in practice we keep these for the lifetime of the validation run so the
    same excerpt can be reused across ICs.

    Page numbers in the return list are 1-indexed and refer to the *original*
    PDF, so the model can cite them as ``source_page`` consistent with the
    no-remap convention used everywhere else in the reviewer.
    """
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(str(pdf_path))
    total = len(reader.pages)
    if total == 0:
        return str(pdf_path), []

    keep: set[int] = set()
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if keyword_re.search(text):
            for n in (i - 1, i, i + 1):
                if 0 <= n < total:
                    keep.add(n)
            if len(keep) >= max_pages:
                break

    if not keep:
        # Fall back: first few pages so the model gets *something* it can
        # decline to use, rather than an empty excerpt.
        keep = set(range(min(3, total)))

    selected = sorted(keep)[:max_pages]
    writer = PdfWriter()
    for i in selected:
        writer.add_page(reader.pages[i])
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    writer.write(tmp)
    tmp.close()
    return tmp.name, [i + 1 for i in selected]


def get_datasheet_excerpt(
    graph: DesignGraph,
    constraints_map: ConstraintsMap,
    designator: str,
    topic: str,
    state: ExcerptState | None,
):
    """Return pages from a *connected* neighbor IC's datasheet for a topic.

    Returns ``(text_summary, pdf_block_or_none)`` — the caller treats the text
    as the tool's ``content`` and attaches the PdfBlock (if present) to the
    same user message so the model can read the pages on the next turn.

    Restricted to neighbors of the IC under review (state.connected_designators).
    Subject to per-review fetch/page budget caps.
    """
    if state is None:
        return ("get_datasheet_excerpt called without per-review state — "
                "this is a bug, no excerpt returned.", None)

    # Lazy import to avoid backend↔pinscopex circular dependency at module load.
    from backend.services.llm import PdfBlock

    designator = (designator or "").strip()
    topic = (topic or "").strip().lower()

    if topic not in EXCERPT_TOPICS:
        valid = ", ".join(sorted(EXCERPT_TOPICS.keys()))
        return (f"Unknown topic '{topic}'. Valid topics: {valid}.", None)

    if designator == state.current_ic:
        return (
            f"You are already reviewing {designator}'s datasheet — its pages "
            f"are in your initial context. Use the existing PDF, no excerpt "
            f"fetch needed.",
            None,
        )

    if designator not in state.connected_designators:
        return (
            f"{designator} is not a signal neighbor of {state.current_ic} "
            f"in this design. The excerpt tool is restricted to ICs that "
            f"share a signal net with the IC under review. If you suspect "
            f"the issue still applies, submit WARNING with an explicit "
            f"Unverified: assumption.",
            None,
        )

    comp = graph.components.get(designator)
    if comp is None:
        return (f"Component '{designator}' not found in design graph.", None)

    mpn = comp.mpn or comp.value
    if not mpn:
        return (f"{designator} has no MPN — cannot resolve a datasheet.", None)

    # Budget checks before doing pypdf work. Three caps, in order:
    #  - fetch_count: total excerpt calls this review (bounds turn cost).
    #  - per_neighbor_page_budget: pages already pulled from THIS neighbor —
    #    once a neighbor is fully examined, more pages won't help.
    #  - page_budget: global ceiling across all neighbors (hub-IC fan-out).
    # The per-neighbor cap is checked before the global one so that pulling
    # the 2-3 topics needed to verify a single interface is never starved by
    # pages spent on other neighbors.
    neighbor_pages = state.pages_per_neighbor.get(designator, 0)
    if state.fetch_count >= state.fetch_budget:
        return (
            f"Excerpt budget exhausted ({state.fetch_count}/"
            f"{state.fetch_budget} fetches used). Submit WARNING with an "
            f"explicit Unverified: assumption rather than fetching more.",
            None,
        )
    if neighbor_pages >= state.per_neighbor_page_budget:
        return (
            f"Per-neighbor excerpt budget for {designator} exhausted "
            f"({neighbor_pages}/{state.per_neighbor_page_budget} pages). "
            f"You have read enough of {designator}'s datasheet; submit "
            f"WARNING with an explicit Unverified: assumption if the spec "
            f"still isn't resolved.",
            None,
        )
    if state.page_count >= state.page_budget:
        return (
            f"Excerpt page budget exhausted ({state.page_count}/"
            f"{state.page_budget} pages used). Submit WARNING with an "
            f"explicit Unverified: assumption rather than fetching more.",
            None,
        )

    pdf_path = _resolve_neighbor_pdf(state, mpn)
    if pdf_path is None:
        return (
            f"No datasheet PDF available for {designator} ({mpn}). Submit "
            f"WARNING with an explicit Unverified: assumption stating what "
            f"you needed to verify.",
            None,
        )

    # Stable cache key — md5 the source PDF once, reuse across ICs.
    import hashlib
    try:
        ds_md5 = hashlib.md5(pdf_path.read_bytes()).hexdigest()
    except Exception:
        log.exception("excerpt: md5 failed for %s", pdf_path)
        ds_md5 = pdf_path.name

    cache_key = (designator, topic, ds_md5)
    cache_val = state.cache.get(cache_key)
    pages: list[int]
    trimmed_path: str
    if (
        isinstance(cache_val, tuple)
        and len(cache_val) == 2
        and Path(cache_val[0]).is_file()
    ):
        trimmed_path, pages = cache_val  # type: ignore[assignment]
    else:
        keyword_re = EXCERPT_TOPICS[topic]
        remaining_budget = min(
            _EXCERPT_MAX_PAGES_PER_FETCH,
            max(1, state.page_budget - state.page_count),
            max(1, state.per_neighbor_page_budget - neighbor_pages),
        )
        trimmed_path, pages = _trim_pdf_by_keywords(
            pdf_path, keyword_re, remaining_budget,
        )
        state.cache[cache_key] = (trimmed_path, pages)

    # Update per-review budget counters
    state.fetch_count += 1
    state.page_count += len(pages)
    state.pages_per_neighbor[designator] = neighbor_pages + len(pages)

    block = PdfBlock(path=Path(trimmed_path), cacheable=True)
    summary = (
        f"Returned {len(pages)} pages from {designator} ({mpn}) matching "
        f"topic '{topic}': pages {pages}. The PDF excerpt is attached to "
        f"this message — read it and cite the printed page number from the "
        f"original datasheet in any resulting finding. These pages are from "
        f"{designator}'s datasheet (not the component under review), so set "
        f"that finding's source_designator to \"{designator}\" — otherwise the "
        f"page number would resolve against the wrong datasheet."
    )
    return summary, block


# ---------------------------------------------------------------------------
# Tool schemas (for Claude API)
# ---------------------------------------------------------------------------

FIND_CONNECTED_COMPONENTS_SCHEMA = {
    "name": "find_connected_components",
    "description": (
        "Find all components connected to the same net as a specific pin. "
        "Returns net info and each component with full specs and pin map. "
        "Use designator_filter to narrow results (e.g. 'C' for capacitors, 'R' for resistors)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "designator": {
                "type": "string",
                "description": "Component reference, e.g. 'U1', 'U2'",
            },
            "pin": {
                "type": "string",
                "description": "Pin number, e.g. '1', '7'",
            },
            "designator_filter": {
                "type": "string",
                "description": "Optional prefix filter: 'C' for caps, 'R' for resistors, 'U' for ICs, etc.",
            },
        },
        "required": ["designator", "pin"],
    },
}

GET_NET_FOR_PIN_SCHEMA = {
    "name": "get_net_for_pin",
    "description": (
        "Get the net name, type, and voltage for a specific pin. "
        "Lightweight — no component listing. Use for quick voltage checks."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "designator": {
                "type": "string",
                "description": "Component reference, e.g. 'U1'",
            },
            "pin": {
                "type": "string",
                "description": "Pin number, e.g. '1'",
            },
        },
        "required": ["designator", "pin"],
    },
}

GET_PINTABLE_SCHEMA = {
    "name": "get_pintable",
    "description": (
        "Get the full pin mapping for a component: pin numbers, names, "
        "net connections, and whether each pin is connected or unconnected. "
        "Use when pin naming is ambiguous or to check for floating pins."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "designator": {
                "type": "string",
                "description": "Component reference, e.g. 'U1'",
            },
        },
        "required": ["designator"],
    },
}

SUBMIT_REVIEW_SCHEMA = {
    "name": "submit_review",
    "description": (
        "Submit all findings from your review. Only include issues in findings — "
        "do not submit findings for things that are correct. "
        "List what you checked and found OK in checked_areas."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "description": "List of issues found. Empty array if no issues.",
                "items": {
                    "type": "object",
                    "properties": {
                        "finding": {
                            "type": "string",
                            "description": "What you observed in the actual circuit. 1-3 sentences.",
                        },
                        "why": {
                            "type": "string",
                            "description": "Why this matters — what the datasheet says and what could go wrong. 1-3 sentences.",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["ERROR", "WARNING", "INFO"],
                            "description": "ERROR: will cause malfunction. WARNING: may degrade reliability. INFO: worth noting.",
                        },
                        "source_page": {
                            "type": "integer",
                            "description": "Datasheet page number where the requirement is stated.",
                        },
                        "source_quote": {
                            "type": "string",
                            "description": (
                                "The exact verbatim text from the datasheet that "
                                "states this requirement — copy it "
                                "character-for-character (max ~200 chars). Omit "
                                "if the evidence is only in a figure or a "
                                "rasterized table with no selectable text."
                            ),
                        },
                        "source_designator": {
                            "type": "string",
                            "description": (
                                "Designator of the component whose datasheet "
                                "source_page and source_quote refer to. OMIT "
                                "this when the page/quote is from the component "
                                "you are reviewing (its own datasheet — the "
                                "common case). Set it ONLY when the evidence "
                                "came from a connected component's datasheet "
                                "that you fetched with get_datasheet_excerpt "
                                "(e.g. \"U3\"), so source_page resolves to the "
                                "correct datasheet."
                            ),
                        },
                        "recommendation": {
                            "type": "string",
                            "description": "What to change to fix the issue. Only for ERROR/WARNING.",
                        },
                    },
                    "required": ["finding", "why", "status", "source_page"],
                },
            },
            "checked_areas": {
                "type": "array",
                "description": (
                    "Areas you reviewed and found correct. Short labels, e.g. "
                    "'input decoupling', 'output capacitor', 'enable logic', "
                    "'crystal circuit', 'voltage margins', 'reset circuit'."
                ),
                "items": {"type": "string"},
            },
        },
        "required": ["findings", "checked_areas"],
    },
}

GET_DATASHEET_EXCERPT_SCHEMA = {
    "name": "get_datasheet_excerpt",
    "description": (
        "Fetch a focused excerpt of a *connected* IC's datasheet — the pages "
        "covering one topic (abs-max, electrical characteristics, 5V-tolerance, "
        "etc.). Use this BEFORE flagging any cross-IC interface issue that "
        "depends on the counterpart's spec. Restricted to ICs that share a "
        "signal net with the IC under review. Subject to a per-review fetch "
        "budget; if exhausted, submit WARNING with an explicit Unverified: "
        "assumption rather than guessing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "designator": {
                "type": "string",
                "description": (
                    "Reference of a connected IC (e.g. 'U3'). Must be a "
                    "signal neighbor of the IC under review."
                ),
            },
            "topic": {
                "type": "string",
                "enum": sorted(EXCERPT_TOPICS.keys()),
                "description": (
                    "Which datasheet section to pull. Pick the narrowest "
                    "topic that covers the spec you need — pin_voltage_levels "
                    "for 5V-tolerance / VIH / VIL, absolute_max for stress "
                    "ratings, electrical_characteristics for drive "
                    "strengths, application_circuit for reference designs."
                ),
            },
        },
        "required": ["designator", "topic"],
    },
}

GRAPH_TOOLS = [
    FIND_CONNECTED_COMPONENTS_SCHEMA,
    GET_NET_FOR_PIN_SCHEMA,
    GET_PINTABLE_SCHEMA,
    GET_DATASHEET_EXCERPT_SCHEMA,
]
ALL_TOOLS = GRAPH_TOOLS + [SUBMIT_REVIEW_SCHEMA]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def execute_tool(
    graph: DesignGraph,
    constraints_map: ConstraintsMap,
    tool_name: str,
    tool_input: dict,
    state: ExcerptState | None = None,
):
    """Execute a graph-query tool call.

    Returns ``(text, attachment)`` where ``attachment`` is an optional
    PdfBlock the caller should append to the next user message alongside the
    tool_result. All tools except ``get_datasheet_excerpt`` return
    ``(text, None)``.
    """
    if tool_name == "find_connected_components":
        return (
            find_connected_components(
                graph, constraints_map,
                tool_input["designator"],
                tool_input["pin"],
                tool_input.get("designator_filter"),
            ),
            None,
        )
    if tool_name == "get_net_for_pin":
        return (
            get_net_for_pin(
                graph, constraints_map,
                tool_input["designator"],
                tool_input["pin"],
            ),
            None,
        )
    if tool_name == "get_pintable":
        return (
            get_pintable(
                graph, constraints_map,
                tool_input["designator"],
            ),
            None,
        )
    if tool_name == "get_datasheet_excerpt":
        return get_datasheet_excerpt(
            graph, constraints_map,
            tool_input.get("designator", ""),
            tool_input.get("topic", ""),
            state,
        )
    return (f"Unknown tool: {tool_name}", None)
