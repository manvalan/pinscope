"""Reference-plane resolution, coverage, and void proximity.

This is the crux module: it's what lets the tool catch a broken or split
reference plane under a trace, not just a nominal width-based Z0. Pure and
pcbnew-free — it works entirely off the ZonePolygon outline points that
board_model.py already extracted (see that module's docstring for why
containment/distance are done here with shapely rather than by calling back
into pcbnew's HitTestFilledArea/Contains).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from shapely.geometry import Point as ShapelyPoint
from shapely.geometry import Polygon as ShapelyPolygon

from impedancefinder.model import (
    DielectricLayer,
    PlaneCoverage,
    SamplePoint,
    Stackup,
    ZonePolygon,
)

# A covered sample within this many trace-widths of the plane's edge is
# flagged as approaching a split, even before it fully crosses one.
_VOID_PROXIMITY_WIDTH_MULTIPLE = 3.0


@dataclass(frozen=True)
class PlaneContext:
    """Which reference plane(s) back a sample, and the dielectric between
    the trace and each one. Either side is None when the trace is on an
    outer layer (no plane above) or the stackup has no layer beyond it."""

    above: Optional[PlaneCoverage]
    below: Optional[PlaneCoverage]
    dielectric_above: Optional[DielectricLayer]
    dielectric_below: Optional[DielectricLayer]

    @property
    def reference_plane_count(self) -> int:
        return sum(1 for coverage in (self.above, self.below) if coverage is not None)


def resolve_reference_planes(
    stackup: Stackup, zone_polygons: tuple[ZonePolygon, ...], sample: SamplePoint
) -> PlaneContext:
    """Find the copper layer(s) adjacent to the sample's layer and check
    whether each one actually has copper under/over this point."""
    layer_index = stackup.copper_layer_names.index(sample.layer)
    below_layer = _layer_at(stackup, layer_index + 1)
    above_layer = _layer_at(stackup, layer_index - 1)
    return PlaneContext(
        above=coverage_at(sample, zone_polygons, above_layer) if above_layer else None,
        below=coverage_at(sample, zone_polygons, below_layer) if below_layer else None,
        dielectric_above=stackup.dielectric_between(above_layer, sample.layer) if above_layer else None,
        dielectric_below=stackup.dielectric_between(sample.layer, below_layer) if below_layer else None,
    )


def _layer_at(stackup: Stackup, index: int) -> Optional[str]:
    if 0 <= index < len(stackup.copper_layer_names):
        return stackup.copper_layer_names[index]
    return None


def coverage_at(
    sample: SamplePoint,
    zone_polygons: tuple[ZonePolygon, ...],
    layer: str,
    exclude_net: Optional[str] = None,
) -> PlaneCoverage:
    """Is `layer` actually covered by copper under/over the sample, and how
    close is the nearest plane edge (a covered point's distance to falling
    off the plane, or an uncovered point's distance to landing on one)?

    exclude_net skips zones on the trace's own net — geometry.py reuses this
    to measure the gap to same-layer *coplanar ground* copper, where the
    trace's own copper obviously shouldn't count.
    """
    polygons = [
        polygon
        for zone_polygon in zone_polygons
        if zone_polygon.layer == layer and zone_polygon.net != exclude_net
        for polygon in _to_shapely_polygons(zone_polygon)
    ]
    if not polygons:
        return PlaneCoverage(layer=layer, is_covered=False, distance_to_void_mm=None)
    point = ShapelyPoint(sample.position.x_mm, sample.position.y_mm)
    is_covered = any(polygon.contains(point) for polygon in polygons)
    distance_mm = min(polygon.boundary.distance(point) for polygon in polygons)
    return PlaneCoverage(layer=layer, is_covered=is_covered, distance_to_void_mm=distance_mm)


def _to_shapely_polygons(zone_polygon: ZonePolygon) -> tuple[ShapelyPolygon, ...]:
    # Each outline is treated as its own simple polygon; nested cutouts
    # within one filled zone island aren't modeled separately in this pass.
    return tuple(
        ShapelyPolygon([(point.x_mm, point.y_mm) for point in outline])
        for outline in zone_polygon.outlines_mm
        if len(outline) >= 3
    )


def flags_for_context(context: PlaneContext, width_mm: float) -> tuple[str, ...]:
    """Plane-health flags for a sample, deduplicated across above/below."""
    flags = _flags_for(context.below, width_mm) + _flags_for(context.above, width_mm)
    return tuple(dict.fromkeys(flags))


def _flags_for(coverage: Optional[PlaneCoverage], width_mm: float) -> tuple[str, ...]:
    if coverage is None:
        return ()
    if not coverage.is_covered:
        return ("plane_broken",)
    threshold_mm = _VOID_PROXIMITY_WIDTH_MULTIPLE * width_mm
    if coverage.distance_to_void_mm is not None and coverage.distance_to_void_mm < threshold_mm:
        return ("plane_split_nearby",)
    return ()
