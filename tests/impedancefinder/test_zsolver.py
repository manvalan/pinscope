"""zsolver validation: a published reference point plus monotonicity checks
against the underlying physics, so the tests don't just re-derive whatever
the implementation happens to compute.
"""
from __future__ import annotations

import pytest

from impedancefinder import zsolver


def test_microstrip_matches_classic_50ohm_fr4_rule_of_thumb():
    # ~3mm trace on 1.6mm FR4 (er~4.5) is the textbook "50 ohm microstrip"
    # widely quoted in PCB fab application notes.
    z0 = zsolver.microstrip_z0(width_mm=3.0, height_mm=1.6, er=4.5, t_mm=0.035)
    assert z0 == pytest.approx(50.0, rel=0.05)


def test_microstrip_z0_decreases_with_width():
    narrow = zsolver.microstrip_z0(0.2, 0.15, 4.3, 0.035)
    wide = zsolver.microstrip_z0(0.6, 0.15, 4.3, 0.035)
    assert wide < narrow


def test_microstrip_z0_increases_with_dielectric_height():
    thin = zsolver.microstrip_z0(0.3, 0.1, 4.3, 0.035)
    thick = zsolver.microstrip_z0(0.3, 0.3, 4.3, 0.035)
    assert thick > thin


def test_microstrip_z0_decreases_with_er():
    low_er = zsolver.microstrip_z0(0.3, 0.15, 3.0, 0.035)
    high_er = zsolver.microstrip_z0(0.3, 0.15, 5.0, 0.035)
    assert high_er < low_er


def test_microstrip_z0_finite_thickness_correction_is_a_small_effect():
    with_thickness = zsolver.microstrip_z0(0.3, 0.15, 4.3, 0.035)
    without_thickness = zsolver.microstrip_z0(0.3, 0.15, 4.3, 0.0)
    assert without_thickness == pytest.approx(with_thickness, rel=0.15)


def test_stripline_requires_positive_copper_thickness():
    with pytest.raises(ValueError):
        zsolver.stripline_z0(0.15, 0.3, 4.4, 0.0)


def test_stripline_z0_decreases_with_width():
    narrow = zsolver.stripline_z0(0.1, 0.5, 4.4, 0.035)
    wide = zsolver.stripline_z0(0.3, 0.5, 4.4, 0.035)
    assert wide < narrow


def test_stripline_z0_increases_with_plane_spacing():
    tight = zsolver.stripline_z0(0.15, 0.3, 4.4, 0.035)
    loose = zsolver.stripline_z0(0.15, 0.6, 4.4, 0.035)
    assert loose > tight


def test_diff_microstrip_is_between_single_ended_and_twice_single_ended():
    single = zsolver.microstrip_z0(0.2, 0.15, 4.3, 0.035)
    diff = zsolver.diff_microstrip_z0(0.2, 0.15, 0.2, 4.3, 0.035)
    assert single < diff < 2 * single


def test_diff_microstrip_approaches_twice_single_ended_as_spacing_grows():
    single = zsolver.microstrip_z0(0.2, 0.15, 4.3, 0.035)
    wide_gap = zsolver.diff_microstrip_z0(0.2, 0.15, 5.0, 4.3, 0.035)
    assert wide_gap == pytest.approx(2 * single, rel=0.02)


def test_diff_stripline_approaches_twice_single_ended_as_spacing_grows():
    single = zsolver.stripline_z0(0.15, 0.3, 4.4, 0.035)
    wide_gap = zsolver.diff_stripline_z0(0.15, 0.3, 5.0, 4.4, 0.035)
    assert wide_gap == pytest.approx(2 * single, rel=0.02)


def test_cpwg_is_not_implemented():
    with pytest.raises(NotImplementedError):
        zsolver.cpwg_z0()
