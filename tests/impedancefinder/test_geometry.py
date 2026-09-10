from __future__ import annotations

from impedancefinder import geometry, net_walk, planes
from impedancefinder.model import Point2D, SamplePoint, Stackup, Topology, TraceSegment, ZonePolygon

from .conftest import rect


def _sample(layer: str = "F.Cu", x_mm: float = 0.0, width_mm: float = 0.2) -> SamplePoint:
    return SamplePoint(net="SIG", layer=layer, distance_along_net_mm=x_mm, position=Point2D(x_mm, 0), width_mm=width_mm)


def test_classify_outer_layer_with_plane_is_microstrip(stackup_4layer, full_ground_plane):
    sample = _sample()
    context = planes.resolve_reference_planes(stackup_4layer, (full_ground_plane,), sample)
    topology = geometry.classify_topology(sample, stackup_4layer, (full_ground_plane,), context)
    assert topology is Topology.MICROSTRIP


def test_classify_inner_layer_is_stripline(stackup_4layer):
    top_plane = ZonePolygon(net="GND", layer="F.Cu", outlines_mm=(rect(-5, -5, 50, 5),))
    bottom_plane = ZonePolygon(net="GND", layer="In2.Cu", outlines_mm=(rect(-5, -5, 50, 5),))
    sample = _sample(layer="In1.Cu", width_mm=0.15)
    zones = (top_plane, bottom_plane)
    context = planes.resolve_reference_planes(stackup_4layer, zones, sample)
    topology = geometry.classify_topology(sample, stackup_4layer, zones, context)
    assert topology is Topology.STRIPLINE


def test_classify_with_no_adjacent_copper_layer_is_unknown():
    # A genuine single-copper-layer board: no adjacent layer can exist at
    # all, unlike a 2-layer board whose reference plane merely has a void
    # (which still counts as "a plane" for classification, just flagged).
    stackup = Stackup(copper_layer_names=("F.Cu",), dielectrics=())
    sample = _sample()
    context = planes.resolve_reference_planes(stackup, (), sample)
    topology = geometry.classify_topology(sample, stackup, (), context)
    assert topology is Topology.UNKNOWN


def test_cpwg_classification_surfaces_not_implemented_flag(stackup_4layer, full_ground_plane):
    # Coplanar ground pour on the trace's own layer, close enough to count.
    coplanar_gnd = ZonePolygon(net="GND", layer="F.Cu", outlines_mm=(rect(0.3, -5, 50, 5),))
    sample = _sample()
    zones = (full_ground_plane, coplanar_gnd)
    context = planes.resolve_reference_planes(stackup_4layer, zones, sample)
    topology = geometry.classify_topology(sample, stackup_4layer, zones, context)
    assert topology is Topology.COPLANAR_GROUNDED

    impedance_sample = geometry.compute_sample_impedance(sample, stackup_4layer, context, topology)
    assert impedance_sample.z0_ohms is None
    assert "topology_not_supported" in impedance_sample.flags


def _flat_samples(branches):
    return [sample for branch in branches for sample in branch.samples]


def test_clean_microstrip_run_has_no_flags(stackup_4layer, full_ground_plane, clean_run_segment):
    branches = net_walk.sample_net((clean_run_segment,), pitch_mm=2.0)
    results = [geometry.analyze_sample(s, stackup_4layer, (full_ground_plane,)) for s in _flat_samples(branches)]
    assert results  # sanity: the fixture actually produced samples
    assert all(not result.flags for result in results)
    assert all(result.topology is Topology.MICROSTRIP for result in results)


def test_neckdown_is_caught_as_a_higher_impedance(stackup_4layer, full_ground_plane, neckdown_segments):
    branches = net_walk.sample_net(neckdown_segments, pitch_mm=1.0)
    results = [geometry.analyze_sample(s, stackup_4layer, (full_ground_plane,)) for s in _flat_samples(branches)]
    wide_z0 = [r.z0_ohms for r in results if r.width_mm == 0.3]
    narrow_z0 = [r.z0_ohms for r in results if r.width_mm == 0.12]
    assert wide_z0 and narrow_z0
    assert min(narrow_z0) > max(wide_z0)


def test_void_crossing_trace_is_flagged(stackup_4layer, split_ground_plane, clean_run_segment):
    branches = net_walk.sample_net((clean_run_segment,), pitch_mm=0.5)
    results = [geometry.analyze_sample(s, stackup_4layer, (split_ground_plane,)) for s in _flat_samples(branches)]
    assert any("plane_broken" in result.flags for result in results)


def test_find_pair_net_name_suffix_conventions():
    assert geometry.find_pair_net_name("USB_D_P") == "USB_D_N"
    assert geometry.find_pair_net_name("USB_D_N") == "USB_D_P"
    assert geometry.find_pair_net_name("D+") == "D-"
    assert geometry.find_pair_net_name("D-") == "D+"


def test_find_pair_net_name_returns_none_for_unpaired_nets():
    assert geometry.find_pair_net_name("GND") is None
    assert geometry.find_pair_net_name("3V3") is None


def test_differential_sample_impedance_is_between_single_and_twice_single(
    stackup_4layer, full_ground_plane
):
    # Two parallel vertical traces 0.4mm apart center-to-center, 0.2mm wide
    # each -> 0.2mm edge-to-edge gap.
    sample = SamplePoint(
        net="D_P", layer="F.Cu", distance_along_net_mm=0, position=Point2D(0, 5), width_mm=0.2
    )
    partner_segments = (
        TraceSegment(net="D_N", layer="F.Cu", start=Point2D(0.4, 0), end=Point2D(0.4, 10), width_mm=0.2),
    )
    single_ended = geometry.analyze_sample(sample, stackup_4layer, (full_ground_plane,))
    differential = geometry.analyze_differential_sample(
        sample, partner_segments, stackup_4layer, (full_ground_plane,)
    )
    assert single_ended.z0_ohms < differential.z0_ohms < 2 * single_ended.z0_ohms


def test_differential_sample_falls_back_to_single_ended_with_no_partner_segments(
    stackup_4layer, full_ground_plane
):
    sample = SamplePoint(
        net="D_P", layer="F.Cu", distance_along_net_mm=0, position=Point2D(0, 5), width_mm=0.2
    )
    single_ended = geometry.analyze_sample(sample, stackup_4layer, (full_ground_plane,))
    differential = geometry.analyze_differential_sample(sample, (), stackup_4layer, (full_ground_plane,))
    assert differential.z0_ohms == single_ended.z0_ohms


def test_differential_sample_falls_back_to_single_ended_when_partner_is_far_away(
    stackup_4layer, full_ground_plane
):
    # Partner net exists but its nearest point is 50mm away -- clearly not a
    # coupled pair at this sample, e.g. before the pair converges.
    sample = SamplePoint(
        net="D_P", layer="F.Cu", distance_along_net_mm=0, position=Point2D(0, 5), width_mm=0.2
    )
    far_partner = (
        TraceSegment(net="D_N", layer="F.Cu", start=Point2D(50, 0), end=Point2D(50, 10), width_mm=0.2),
    )
    single_ended = geometry.analyze_sample(sample, stackup_4layer, (full_ground_plane,))
    differential = geometry.analyze_differential_sample(
        sample, far_partner, stackup_4layer, (full_ground_plane,)
    )
    assert differential.z0_ohms == single_ended.z0_ohms
