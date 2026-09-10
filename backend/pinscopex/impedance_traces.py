"""ImpedenceFinder net analysis on specified PCB traces.

Walks sampled points on named nets (net_walk + planes + zsolver).
Stackup and widths come from the board (or explicit LayoutStackup).
No invented εr/h; missing stackup or empty net list skips.
"""

from __future__ import annotations

from dataclasses import asdict

from backend.vendor_path import ensure_impedancefinder

ensure_impedancefinder()
from impedancefinder import net_analysis, report
from impedancefinder.model import (
    BoardData,
    DielectricLayer,
    Point2D,
    Stackup,
    TraceSegment,
    ViaSpan,
    ZonePolygon,
)

from backend.pinscopex.impedance import GeometryError
from backend.pinscopex.models import DesignGraph, LayoutGraph, NetType


def _stackup(layout: LayoutGraph) -> Stackup:
    raw = layout.stackup
    if raw is None:
        raise GeometryError("PCB has no stackup (copper + dielectric εr/h)")
    t = raw.copper_thickness_mm
    if t is None or t <= 0:
        raise GeometryError("PCB stackup has no copper thickness")
    return Stackup(
        copper_layer_names=tuple(raw.copper_layers),
        dielectrics=tuple(
            DielectricLayer(name=d.name, er=d.er, height_mm=d.height_mm)
            for d in raw.dielectrics
        ),
        copper_thickness_mm=t,
    )


def layout_to_board_data(layout: LayoutGraph) -> BoardData:
    stackup = _stackup(layout)
    segments: list[TraceSegment] = []
    for s in layout.segments:
        if not s.net or s.width <= 0:
            continue
        segments.append(TraceSegment(
            net=s.net,
            layer=s.layer,
            start=Point2D(s.start[0], s.start[1]),
            end=Point2D(s.end[0], s.end[1]),
            width_mm=s.width,
        ))
    vias: list[ViaSpan] = []
    layers = stackup.copper_layer_names
    if len(layers) >= 2:
        top, bot = layers[0], layers[-1]
        for v in layout.vias:
            if not v.net or v.drill is None or v.drill <= 0:
                continue
            vias.append(ViaSpan(
                net=v.net,
                position=Point2D(v.x, v.y),
                top_layer=top,
                bottom_layer=bot,
                drill_mm=v.drill,
            ))
    zones: list[ZonePolygon] = []
    for z in layout.zones:
        rings = tuple(
            tuple(Point2D(x, y) for x, y in ring)
            for ring in z.outlines
            if len(ring) >= 3
        )
        if rings:
            zones.append(ZonePolygon(net=z.net, layer=z.layer, outlines_mm=rings))
    return BoardData(
        segments=tuple(segments),
        vias=tuple(vias),
        zone_polygons=tuple(zones),
        copper_layer_names=stackup.copper_layer_names,
        stackup=stackup,
        outline=None,
    )


def analyze_specified_nets(
    layout: LayoutGraph,
    net_names: list[str],
    pitch_mm: float,
) -> list[dict]:
    """Analyze only the named nets. Empty names → []. Missing net → error row."""
    if pitch_mm <= 0:
        raise GeometryError("pitch_mm must be > 0")
    wanted = [n.strip() for n in net_names if n and n.strip()]
    if not wanted:
        return []
    board = layout_to_board_data(layout)
    stackup = board.stackup
    assert stackup is not None
    rows: list[dict] = []
    for name in wanted:
        segs = net_analysis.segments_for(board, name)
        if not segs:
            rows.append({"net_name": name, "error": "no segments on this net"})
            continue
        result = net_analysis.analyze_net(board, stackup, name, pitch_mm)
        summary = report.summarize_net(name, board, result)
        row = asdict(summary)
        row["sample_count"] = len(result.samples)
        rows.append(row)
    return rows


# ImpedenceFinder net_walk sample interval (mm). Same as vendor
# tests/test_net_walk.py pitch_mm=1.0 — not a Z0 target.
NET_WALK_PITCH_MM = 1.0


def nets_needed(layout: LayoutGraph, graph: DesignGraph | None) -> list[str]:
    """Routed copper that is not a power/ground net in the schematic."""
    routed = {s.net for s in layout.segments if s.net and s.width > 0}
    needed: list[str] = []
    for name in sorted(routed):
        if graph is not None:
            net = graph.nets.get(name)
            if net is not None and net.net_type in (NetType.POWER, NetType.GROUND):
                continue
        needed.append(name)
    return needed


def analyze_where_needed(
    layout: LayoutGraph,
    graph: DesignGraph | None = None,
    pitch_mm: float = NET_WALK_PITCH_MM,
) -> dict:
    """Pipeline entry: skip without stackup or without routed signal nets."""
    if pitch_mm <= 0:
        raise GeometryError("pitch_mm must be > 0")
    if layout.stackup is None:
        return {"pitch_mm": pitch_mm, "nets": [], "skipped": "no stackup"}
    names = nets_needed(layout, graph)
    if not names:
        return {"pitch_mm": pitch_mm, "nets": [], "skipped": "no routed signal nets"}
    return {
        "pitch_mm": pitch_mm,
        "nets": analyze_specified_nets(layout, names, pitch_mm),
        "skipped": None,
    }
