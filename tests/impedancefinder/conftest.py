"""Synthetic fixtures for the pure engine — no board_model, no pcbnew."""
from __future__ import annotations

import pytest

from impedancefinder.model import DielectricLayer, Point2D, Stackup, TraceSegment, ZonePolygon


def rect(x0: float, y0: float, x1: float, y1: float) -> tuple[Point2D, ...]:
    return (Point2D(x0, y0), Point2D(x1, y0), Point2D(x1, y1), Point2D(x0, y1))


@pytest.fixture
def stackup_4layer() -> Stackup:
    return Stackup(
        copper_layer_names=("F.Cu", "In1.Cu", "In2.Cu", "B.Cu"),
        dielectrics=(
            DielectricLayer("prepreg_top", 4.3, 0.15),
            DielectricLayer("core", 4.4, 0.7),
            DielectricLayer("prepreg_bottom", 4.3, 0.15),
        ),
        copper_thickness_mm=0.035,
    )


@pytest.fixture
def full_ground_plane() -> ZonePolygon:
    """A ground pour on In1.Cu with no voids, spanning the whole test area."""
    return ZonePolygon(net="GND", layer="In1.Cu", outlines_mm=(rect(-5, -5, 50, 5),))


@pytest.fixture
def split_ground_plane() -> ZonePolygon:
    """A ground pour on In1.Cu with a gap between x=4mm and x=6mm."""
    return ZonePolygon(
        net="GND",
        layer="In1.Cu",
        outlines_mm=(rect(-5, -5, 4, 5), rect(6, -5, 50, 5)),
    )


@pytest.fixture
def clean_run_segment() -> TraceSegment:
    return TraceSegment(net="SIG", layer="F.Cu", start=Point2D(0, 0), end=Point2D(10, 0), width_mm=0.2)


@pytest.fixture
def neckdown_segments() -> tuple[TraceSegment, ...]:
    """A trace that narrows partway along its run."""
    return (
        TraceSegment(net="SIG", layer="F.Cu", start=Point2D(0, 0), end=Point2D(5, 0), width_mm=0.3),
        TraceSegment(net="SIG", layer="F.Cu", start=Point2D(5, 0), end=Point2D(10, 0), width_mm=0.12),
    )
