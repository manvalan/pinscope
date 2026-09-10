from __future__ import annotations

from impedancefinder import planes
from impedancefinder.model import Point2D, SamplePoint, ZonePolygon

from .conftest import rect


def _sample_at(x_mm: float, width_mm: float = 0.2) -> SamplePoint:
    return SamplePoint(
        net="SIG", layer="F.Cu", distance_along_net_mm=x_mm, position=Point2D(x_mm, 0), width_mm=width_mm
    )


def test_full_coverage_has_no_flags(full_ground_plane):
    sample = _sample_at(2.0)
    coverage = planes.coverage_at(sample, (full_ground_plane,), "In1.Cu")
    assert coverage.is_covered
    assert planes._flags_for(coverage, sample.width_mm) == ()


def test_void_directly_under_trace_flags_broken(split_ground_plane):
    sample = _sample_at(5.0)  # inside the 4..6mm gap
    coverage = planes.coverage_at(sample, (split_ground_plane,), "In1.Cu")
    assert not coverage.is_covered
    assert planes._flags_for(coverage, sample.width_mm) == ("plane_broken",)


def test_void_near_but_not_under_trace_flags_proximity_only(split_ground_plane):
    sample = _sample_at(3.9, width_mm=0.5)  # covered, close to the gap edge at x=4
    coverage = planes.coverage_at(sample, (split_ground_plane,), "In1.Cu")
    assert coverage.is_covered
    assert planes._flags_for(coverage, sample.width_mm) == ("plane_split_nearby",)


def test_far_from_void_has_no_proximity_flag(split_ground_plane):
    sample = _sample_at(0.0)
    coverage = planes.coverage_at(sample, (split_ground_plane,), "In1.Cu")
    assert coverage.is_covered
    assert planes._flags_for(coverage, sample.width_mm) == ()


def test_missing_plane_layer_reports_uncovered_not_a_crash():
    sample = _sample_at(0.0)
    coverage = planes.coverage_at(sample, (), "In1.Cu")
    assert not coverage.is_covered
    assert coverage.distance_to_void_mm is None


def test_resolve_reference_planes_outer_layer_has_only_below(stackup_4layer, full_ground_plane):
    sample = _sample_at(2.0)
    context = planes.resolve_reference_planes(stackup_4layer, (full_ground_plane,), sample)
    assert context.above is None
    assert context.below is not None
    assert context.reference_plane_count == 1


def test_resolve_reference_planes_inner_layer_has_both(stackup_4layer):
    top_plane = ZonePolygon(net="GND", layer="F.Cu", outlines_mm=(rect(-5, -5, 50, 5),))
    bottom_plane = ZonePolygon(net="GND", layer="In2.Cu", outlines_mm=(rect(-5, -5, 50, 5),))
    sample = SamplePoint(
        net="SIG", layer="In1.Cu", distance_along_net_mm=0, position=Point2D(0, 0), width_mm=0.15
    )
    context = planes.resolve_reference_planes(stackup_4layer, (top_plane, bottom_plane), sample)
    assert context.above is not None and context.above.is_covered
    assert context.below is not None and context.below.is_covered
    assert context.reference_plane_count == 2


def test_exclude_net_ignores_the_traces_own_copper():
    own_net_pour = ZonePolygon(net="SIG", layer="F.Cu", outlines_mm=(rect(-5, -5, 50, 5),))
    sample = _sample_at(0.0)
    coverage = planes.coverage_at(sample, (own_net_pour,), "F.Cu", exclude_net="SIG")
    assert not coverage.is_covered
    assert coverage.distance_to_void_mm is None
