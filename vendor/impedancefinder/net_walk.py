"""Samples a net's routed segments into evenly-spaced points along a
continuous distance axis.

Segments are chained by endpoint coincidence: two segments that share an
exact (x, y) point are treated as connected, regardless of layer. This
means a via is handled for free -- the segment ending on one layer and the
segment starting on the other share the via's exact position, so the
distance axis carries straight through without any via-specific code.

A net with a single point-to-point route becomes one NetBranch. A
T-topology net (any point where 3+ segments meet) is split into one branch
per spoke leaving that point, each restarting its distance axis at zero
there -- callers that want a single unified axis across the whole net will
need to stitch branches together themselves; this module only guarantees
that each individual branch's axis is correct and continuous.
"""
from __future__ import annotations

from impedancefinder.model import NetBranch, Point2D, SamplePoint, TraceSegment

_COORDINATE_PRECISION_MM = 6  # matches pcbnew's nm-to-mm conversion exactly


def sample_net(segments: tuple[TraceSegment, ...], pitch_mm: float) -> tuple[NetBranch, ...]:
    """Sample every branch of a net at pitch_mm, plus each segment's exact
    endpoint. All segments are assumed to belong to the same net; callers
    should pre-filter board_model.BoardData.segments by net name first.
    """
    if pitch_mm <= 0:
        raise ValueError(f"pitch_mm must be positive, got {pitch_mm}")
    branches = _group_into_branches(segments)
    return tuple(_sample_branch(branch, pitch_mm) for branch in branches)


def _endpoint_key(point: Point2D) -> tuple[float, float]:
    return (round(point.x_mm, _COORDINATE_PRECISION_MM), round(point.y_mm, _COORDINATE_PRECISION_MM))


def _build_adjacency(
    segments: tuple[TraceSegment, ...]
) -> dict[tuple[float, float], list[TraceSegment]]:
    adjacency: dict[tuple[float, float], list[TraceSegment]] = {}
    for segment in segments:
        for endpoint in (segment.start, segment.end):
            adjacency.setdefault(_endpoint_key(endpoint), []).append(segment)
    return adjacency


def _orient_from(segment: TraceSegment, from_key: tuple[float, float]) -> TraceSegment:
    if _endpoint_key(segment.start) == from_key:
        return segment
    return TraceSegment(
        net=segment.net, layer=segment.layer, start=segment.end, end=segment.start, width_mm=segment.width_mm
    )


def _walk_branch(
    entry_key: tuple[float, float],
    entry_segment: TraceSegment,
    adjacency: dict[tuple[float, float], list[TraceSegment]],
    visited: set,
) -> tuple[TraceSegment, ...]:
    ordered: list[TraceSegment] = []
    current_key, current_segment = entry_key, entry_segment
    while True:
        visited.add(id(current_segment))
        oriented = _orient_from(current_segment, current_key)
        ordered.append(oriented)
        next_key = _endpoint_key(oriented.end)
        neighbors = [s for s in adjacency[next_key] if id(s) not in visited]
        if len(neighbors) != 1 or len(adjacency[next_key]) != 2:
            break
        current_key, current_segment = next_key, neighbors[0]
    return tuple(ordered)


def _group_into_branches(segments: tuple[TraceSegment, ...]) -> tuple[tuple[TraceSegment, ...], ...]:
    # Junctions (degree >= 3) are walked in a full first pass, before any
    # leaf is considered -- otherwise a leaf reached first in dict-iteration
    # order would claim a spoke and the branch would start at the leaf
    # instead of the junction, leaving sibling spokes of the same junction
    # inconsistently zeroed (one from the leaf, the rest from the junction).
    adjacency = _build_adjacency(segments)
    visited: set = set()
    branches = []
    for key, segments_at_node in adjacency.items():
        if len(segments_at_node) >= 3:
            branches.extend(_walk_unvisited(key, segments_at_node, adjacency, visited))
    for key, segments_at_node in adjacency.items():
        if len(segments_at_node) == 1:
            branches.extend(_walk_unvisited(key, segments_at_node, adjacency, visited))
    branches.extend(_group_remaining_loops(segments, adjacency, visited))
    return tuple(branches)


def _walk_unvisited(
    key: tuple[float, float],
    segments_at_node: list[TraceSegment],
    adjacency: dict[tuple[float, float], list[TraceSegment]],
    visited: set,
) -> list[tuple[TraceSegment, ...]]:
    return [
        _walk_branch(key, segment, adjacency, visited)
        for segment in segments_at_node
        if id(segment) not in visited
    ]


def _group_remaining_loops(
    segments: tuple[TraceSegment, ...],
    adjacency: dict[tuple[float, float], list[TraceSegment]],
    visited: set,
) -> tuple[tuple[TraceSegment, ...], ...]:
    # Anything left unvisited lies entirely on degree-2 nodes -- a pure loop
    # with no leaf or junction to start from. Walk each remaining loop once,
    # starting arbitrarily from one of its segments.
    loops = []
    for segment in segments:
        if id(segment) not in visited:
            loops.append(_walk_branch(_endpoint_key(segment.start), segment, adjacency, visited))
    return tuple(loops)


def _sample_branch(branch_segments: tuple[TraceSegment, ...], pitch_mm: float) -> NetBranch:
    samples: list[SamplePoint] = []
    cumulative_mm = 0.0
    for segment in branch_segments:
        samples.extend(_sample_segment(segment, pitch_mm, cumulative_mm))
        cumulative_mm += segment.length_mm
    return NetBranch(samples=tuple(samples))


def _sample_segment(
    segment: TraceSegment, pitch_mm: float, offset_mm: float
) -> tuple[SamplePoint, ...]:
    length_mm = segment.length_mm
    if length_mm == 0:
        return (_sample_at(segment, 0.0, offset_mm),)
    step_count = max(1, int(length_mm // pitch_mm))
    local_distances = [i * pitch_mm for i in range(step_count + 1) if i * pitch_mm < length_mm]
    local_distances.append(length_mm)
    return tuple(_sample_at(segment, distance, offset_mm + distance) for distance in local_distances)


def _sample_at(segment: TraceSegment, local_distance_mm: float, cumulative_distance_mm: float) -> SamplePoint:
    fraction = 0.0 if segment.length_mm == 0 else local_distance_mm / segment.length_mm
    position = Point2D(
        x_mm=segment.start.x_mm + fraction * (segment.end.x_mm - segment.start.x_mm),
        y_mm=segment.start.y_mm + fraction * (segment.end.y_mm - segment.start.y_mm),
    )
    return SamplePoint(
        net=segment.net,
        layer=segment.layer,
        distance_along_net_mm=cumulative_distance_mm,
        position=position,
        width_mm=segment.width_mm,
    )
