"""Topology classification and dispatch to the closed-form solvers.

Classifies each sample as microstrip, stripline, or grounded-coplanar
(CPWG) from the vertical plane structure (planes.py) and, for CPWG, a
same-layer copper-proximity heuristic — then calls the matching zsolver
function.

Differential pairs are supported via analyze_differential_sample: pairing is
name-based (NET_P/NET_N or NET+/NET-), and the edge-to-edge spacing is
measured geometrically against the nearest point on the partner net's
segments — there's no assumption that the two nets share a common,
continuous distance axis (net_walk.py's per-segment sampling doesn't
guarantee that yet; see its module docstring).
"""
from __future__ import annotations

from typing import Optional

from impedancefinder import planes, zsolver
from impedancefinder.model import (
    ImpedanceSample,
    Point2D,
    SamplePoint,
    Stackup,
    Topology,
    TraceSegment,
    ZonePolygon,
)
from impedancefinder.planes import PlaneContext

# Same-layer copper (a different net) closer than this many trace-widths is
# treated as a coplanar ground gap, i.e. CPWG rather than plain microstrip.
_CPWG_GAP_WIDTH_MULTIPLE = 5.0

# NET<positive> pairs with NET<negative>, tried in order; the first suffix
# match wins (checked longest-first isn't needed since "_P"/"_N" and "+"/"-"
# can't collide on the same net name).
_DIFF_PAIR_SUFFIX_PAIRS = (("_P", "_N"), ("+", "-"))

# A same-named-pair net whose nearest routed point is farther than this many
# trace-widths away isn't genuinely coupled here (e.g. before the pair
# converges near a connector) -- treat the sample as single-ended instead.
_DIFF_PAIR_MAX_GAP_WIDTH_MULTIPLE = 10.0


def classify_topology(
    sample: SamplePoint,
    stackup: Stackup,
    zone_polygons: tuple[ZonePolygon, ...],
    context: PlaneContext,
) -> Topology:
    if context.reference_plane_count == 0:
        return Topology.UNKNOWN
    if not stackup.is_outer_layer(sample.layer):
        return Topology.STRIPLINE
    if _has_coplanar_ground(sample, zone_polygons):
        return Topology.COPLANAR_GROUNDED
    return Topology.MICROSTRIP


def _has_coplanar_ground(sample: SamplePoint, zone_polygons: tuple[ZonePolygon, ...]) -> bool:
    gap = planes.coverage_at(
        sample, zone_polygons, sample.layer, exclude_net=sample.net
    ).distance_to_void_mm
    return gap is not None and gap < _CPWG_GAP_WIDTH_MULTIPLE * sample.width_mm


def compute_sample_impedance(
    sample: SamplePoint,
    stackup: Stackup,
    context: PlaneContext,
    topology: Topology,
    spacing_mm: Optional[float] = None,
) -> ImpedanceSample:
    """spacing_mm is the edge-to-edge gap to a differential partner trace;
    leave it None for single-ended analysis."""
    z0_ohms, solver_flags = _solve_z0(sample, stackup, context, topology, spacing_mm)
    flags = planes.flags_for_context(context, sample.width_mm) + solver_flags
    return ImpedanceSample(
        distance_along_net_mm=sample.distance_along_net_mm,
        position=sample.position,
        layer=sample.layer,
        width_mm=sample.width_mm,
        topology=topology,
        z0_ohms=z0_ohms,
        flags=flags,
    )


def analyze_sample(
    sample: SamplePoint, stackup: Stackup, zone_polygons: tuple[ZonePolygon, ...]
) -> ImpedanceSample:
    """Convenience wrapper: resolve planes, classify, and solve in one call
    — what cli.py and the plugin use per single-ended sample."""
    context = planes.resolve_reference_planes(stackup, zone_polygons, sample)
    topology = classify_topology(sample, stackup, zone_polygons, context)
    return compute_sample_impedance(sample, stackup, context, topology)


def find_pair_net_name(net_name: str) -> Optional[str]:
    """Guess a differential partner's net name from common KiCad naming
    conventions (NET_P/NET_N, NET+/NET-). Returns None if net_name matches
    neither — callers should then treat it as single-ended."""
    for positive_suffix, negative_suffix in _DIFF_PAIR_SUFFIX_PAIRS:
        if net_name.endswith(positive_suffix):
            return net_name[: -len(positive_suffix)] + negative_suffix
        if net_name.endswith(negative_suffix):
            return net_name[: -len(negative_suffix)] + positive_suffix
    return None


def analyze_differential_sample(
    sample: SamplePoint,
    partner_segments: tuple[TraceSegment, ...],
    stackup: Stackup,
    zone_polygons: tuple[ZonePolygon, ...],
) -> ImpedanceSample:
    """Like analyze_sample, but measures the edge-to-edge gap to the nearest
    point on partner_segments (the paired net's routed segments) and
    dispatches to zsolver's diff_* solvers instead of the single-ended
    ones. Falls back to single-ended analysis if partner_segments is empty
    or too far away to plausibly be a coupled pair."""
    context = planes.resolve_reference_planes(stackup, zone_polygons, sample)
    topology = classify_topology(sample, stackup, zone_polygons, context)
    spacing_mm = _nearest_partner_gap_mm(sample, partner_segments)
    return compute_sample_impedance(sample, stackup, context, topology, spacing_mm)


def _nearest_partner_gap_mm(
    sample: SamplePoint, partner_segments: tuple[TraceSegment, ...]
) -> Optional[float]:
    if not partner_segments:
        return None
    nearest_segment, center_distance_mm = min(
        ((segment, _distance_to_segment(sample.position, segment)) for segment in partner_segments),
        key=lambda pair: pair[1],
    )
    gap_mm = max(0.0, center_distance_mm - sample.width_mm / 2.0 - nearest_segment.width_mm / 2.0)
    if gap_mm > _DIFF_PAIR_MAX_GAP_WIDTH_MULTIPLE * sample.width_mm:
        return None
    return gap_mm


def _distance_to_segment(point: Point2D, segment: TraceSegment) -> float:
    return point.distance_to(_nearest_point_on_segment(point, segment))


def _nearest_point_on_segment(point: Point2D, segment: TraceSegment) -> Point2D:
    start, end = segment.start, segment.end
    dx, dy = end.x_mm - start.x_mm, end.y_mm - start.y_mm
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return start
    t = ((point.x_mm - start.x_mm) * dx + (point.y_mm - start.y_mm) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    return Point2D(start.x_mm + t * dx, start.y_mm + t * dy)


def _solve_z0(
    sample: SamplePoint,
    stackup: Stackup,
    context: PlaneContext,
    topology: Topology,
    spacing_mm: Optional[float] = None,
) -> tuple[Optional[float], tuple[str, ...]]:
    try:
        if topology is Topology.MICROSTRIP:
            return _microstrip_z0(sample, stackup, context, spacing_mm), ()
        if topology is Topology.STRIPLINE:
            return _stripline_z0(sample, stackup, context, spacing_mm), ()
        if topology is Topology.COPLANAR_GROUNDED:
            return zsolver.cpwg_z0(), ()
    except NotImplementedError:
        return None, ("topology_not_supported",)
    return None, ("topology_unknown",)


def _microstrip_z0(
    sample: SamplePoint, stackup: Stackup, context: PlaneContext, spacing_mm: Optional[float]
) -> float:
    dielectric = context.dielectric_below or context.dielectric_above
    if spacing_mm is None:
        return zsolver.microstrip_z0(
            width_mm=sample.width_mm,
            height_mm=dielectric.height_mm,
            er=dielectric.er,
            t_mm=stackup.copper_thickness_mm,
        )
    return zsolver.diff_microstrip_z0(
        width_mm=sample.width_mm,
        height_mm=dielectric.height_mm,
        spacing_mm=spacing_mm,
        er=dielectric.er,
        t_mm=stackup.copper_thickness_mm,
    )


def _stripline_z0(
    sample: SamplePoint, stackup: Stackup, context: PlaneContext, spacing_mm: Optional[float]
) -> float:
    b_mm = context.dielectric_above.height_mm + context.dielectric_below.height_mm
    er = context.dielectric_below.er  # assumes one uniform dielectric between both planes
    if spacing_mm is None:
        return zsolver.stripline_z0(
            width_mm=sample.width_mm, b_mm=b_mm, er=er, t_mm=stackup.copper_thickness_mm
        )
    return zsolver.diff_stripline_z0(
        width_mm=sample.width_mm, b_mm=b_mm, spacing_mm=spacing_mm, er=er, t_mm=stackup.copper_thickness_mm
    )
