from __future__ import annotations

import pytest

from impedancefinder import net_walk
from impedancefinder.model import Point2D, TraceSegment


def test_pitch_must_be_positive():
    with pytest.raises(ValueError):
        net_walk.sample_net((), pitch_mm=0.0)


def test_bend_chains_into_one_continuous_branch():
    # Two segments sharing an exact endpoint at (5, 0) -- a bend, not a via.
    first = TraceSegment(net="SIG", layer="F.Cu", start=Point2D(0, 0), end=Point2D(5, 0), width_mm=0.2)
    second = TraceSegment(net="SIG", layer="F.Cu", start=Point2D(5, 0), end=Point2D(5, 5), width_mm=0.2)
    branches = net_walk.sample_net((first, second), pitch_mm=1.0)
    assert len(branches) == 1
    distances = [s.distance_along_net_mm for s in branches[0].samples]
    assert distances == sorted(distances)
    assert distances[0] == 0.0
    assert distances[-1] == pytest.approx(10.0)  # 5mm + 5mm, continuous


def test_via_like_layer_change_stays_continuous():
    # A segment on F.Cu ending exactly where a segment on In1.Cu begins --
    # this is what a via looks like geometrically, with no ViaSpan needed
    # for net_walk to treat it as one continuous run.
    top = TraceSegment(net="SIG", layer="F.Cu", start=Point2D(0, 0), end=Point2D(3, 0), width_mm=0.2)
    bottom = TraceSegment(net="SIG", layer="In1.Cu", start=Point2D(3, 0), end=Point2D(7, 0), width_mm=0.2)
    branches = net_walk.sample_net((top, bottom), pitch_mm=1.0)
    assert len(branches) == 1
    assert branches[0].samples[-1].distance_along_net_mm == pytest.approx(7.0)


def test_t_junction_splits_into_three_branches_zeroed_at_the_junction():
    junction = Point2D(0, 0)
    spoke_a = TraceSegment(net="SIG", layer="F.Cu", start=junction, end=Point2D(3, 0), width_mm=0.2)
    spoke_b = TraceSegment(net="SIG", layer="F.Cu", start=junction, end=Point2D(0, 4), width_mm=0.2)
    spoke_c = TraceSegment(net="SIG", layer="F.Cu", start=Point2D(-5, 0), end=junction, width_mm=0.2)
    branches = net_walk.sample_net((spoke_a, spoke_b, spoke_c), pitch_mm=1.0)
    assert len(branches) == 3
    lengths = sorted(branch.samples[-1].distance_along_net_mm for branch in branches)
    assert lengths == pytest.approx([3.0, 4.0, 5.0])
    # every branch must start at the junction, not at its far leaf
    assert all(branch.samples[0].distance_along_net_mm == 0.0 for branch in branches)


def test_disconnected_segments_become_separate_branches():
    isolated_a = TraceSegment(net="SIG", layer="F.Cu", start=Point2D(0, 0), end=Point2D(2, 0), width_mm=0.2)
    isolated_b = TraceSegment(net="SIG", layer="F.Cu", start=Point2D(100, 0), end=Point2D(103, 0), width_mm=0.2)
    branches = net_walk.sample_net((isolated_a, isolated_b), pitch_mm=1.0)
    assert len(branches) == 2
    lengths = sorted(branch.samples[-1].distance_along_net_mm for branch in branches)
    assert lengths == pytest.approx([2.0, 3.0])


def test_zero_length_segment_produces_a_single_sample():
    point_segment = TraceSegment(net="SIG", layer="F.Cu", start=Point2D(1, 1), end=Point2D(1, 1), width_mm=0.2)
    branches = net_walk.sample_net((point_segment,), pitch_mm=1.0)
    assert len(branches) == 1
    assert len(branches[0].samples) == 1
    assert branches[0].samples[0].distance_along_net_mm == 0.0
