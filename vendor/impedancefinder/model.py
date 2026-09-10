"""Pure, pcbnew-free domain model for ImpedanceFinder.

Every value here is a plain dataclass in millimetres. Nothing in this module
imports pcbnew, performs I/O, or calls a solver — it only describes shapes
that are valid by construction (invalid states can't be built).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional


@dataclass(frozen=True)
class Point2D:
    x_mm: float
    y_mm: float

    def distance_to(self, other: "Point2D") -> float:
        return ((self.x_mm - other.x_mm) ** 2 + (self.y_mm - other.y_mm) ** 2) ** 0.5


@dataclass(frozen=True)
class DielectricLayer:
    name: str
    er: float
    height_mm: float

    def __post_init__(self) -> None:
        if self.er <= 0:
            raise ValueError(f"er must be positive, got {self.er}")
        if self.height_mm <= 0:
            raise ValueError(f"height_mm must be positive, got {self.height_mm}")


@dataclass(frozen=True)
class Stackup:
    """Copper layers top-to-bottom, with one dielectric between each pair."""

    copper_layer_names: tuple[str, ...]
    dielectrics: tuple[DielectricLayer, ...]
    copper_thickness_mm: float = 0.035  # 1 oz/ft^2 copper, the common PCB default

    def __post_init__(self) -> None:
        expected = len(self.copper_layer_names) - 1
        if len(self.dielectrics) != expected:
            raise ValueError(
                f"expected {expected} dielectrics between "
                f"{len(self.copper_layer_names)} copper layers, "
                f"got {len(self.dielectrics)}"
            )
        if self.copper_thickness_mm <= 0:
            raise ValueError(f"copper_thickness_mm must be positive, got {self.copper_thickness_mm}")

    def dielectric_between(self, top_layer: str, bottom_layer: str) -> DielectricLayer:
        top_index = self.copper_layer_names.index(top_layer)
        bottom_index = self.copper_layer_names.index(bottom_layer)
        if bottom_index != top_index + 1:
            raise ValueError(f"{top_layer!r} and {bottom_layer!r} are not adjacent")
        return self.dielectrics[top_index]

    def is_outer_layer(self, layer_name: str) -> bool:
        return layer_name in (self.copper_layer_names[0], self.copper_layer_names[-1])


@dataclass(frozen=True)
class TraceSegment:
    net: str
    layer: str
    start: Point2D
    end: Point2D
    width_mm: float

    def __post_init__(self) -> None:
        if self.width_mm <= 0:
            raise ValueError(f"width_mm must be positive, got {self.width_mm}")

    @property
    def length_mm(self) -> float:
        return self.start.distance_to(self.end)


@dataclass(frozen=True)
class ViaSpan:
    net: str
    position: Point2D
    top_layer: str
    bottom_layer: str
    drill_mm: float

    def __post_init__(self) -> None:
        if self.drill_mm <= 0:
            raise ValueError(f"drill_mm must be positive, got {self.drill_mm}")


@dataclass(frozen=True)
class SamplePoint:
    """One point along a net's routed length, before plane/impedance
    analysis has been attached (see planes.py, geometry.py)."""

    net: str
    layer: str
    distance_along_net_mm: float
    position: Point2D
    width_mm: float

    def __post_init__(self) -> None:
        if self.width_mm <= 0:
            raise ValueError(f"width_mm must be positive, got {self.width_mm}")


@dataclass(frozen=True)
class NetBranch:
    """One continuous, ordered run of samples with a monotonic distance
    axis. A net with a single point-to-point route is one branch; a
    T-topology net (one fan-out point) is split into one branch per spoke,
    each restarting its distance axis at the fan-out point. See
    net_walk.sample_net."""

    samples: tuple[SamplePoint, ...]


@dataclass(frozen=True)
class PlaneCoverage:
    """Whether a reference plane actually covers a sample point, and if not,
    how close the nearest plane edge/void is (None when covered and the
    distance wasn't computed)."""

    layer: str
    is_covered: bool
    distance_to_void_mm: Optional[float] = None

    def __post_init__(self) -> None:
        if self.distance_to_void_mm is not None and self.distance_to_void_mm < 0:
            raise ValueError("distance_to_void_mm must be >= 0")


class Topology(Enum):
    MICROSTRIP = auto()
    STRIPLINE = auto()
    COPLANAR_GROUNDED = auto()  # CPWG
    UNKNOWN = auto()


@dataclass(frozen=True)
class ImpedanceSample:
    distance_along_net_mm: float
    position: Point2D
    layer: str
    width_mm: float
    topology: Topology
    z0_ohms: Optional[float]
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.width_mm <= 0:
            raise ValueError(f"width_mm must be positive, got {self.width_mm}")
        if self.z0_ohms is not None and self.z0_ohms <= 0:
            raise ValueError(f"z0_ohms must be positive, got {self.z0_ohms}")


@dataclass(frozen=True)
class NetProfile:
    net_name: str
    samples: tuple[ImpedanceSample, ...]

    @property
    def has_flags(self) -> bool:
        return any(sample.flags for sample in self.samples)


@dataclass(frozen=True)
class NetSummary:
    """One row of a batch report (board_report.py): length + impedance
    range for a whole net, collapsed from its per-sample ImpedanceSample
    profile. topologies/flags are the distinct values seen, in first-seen
    order, so a net that changes layer (MICROSTRIP -> STRIPLINE) or crosses
    a plane void is still visible in one row instead of only in the
    full per-sample CSV."""

    net_name: str
    length_mm: float
    branch_count: int
    is_differential: bool
    partner_net_name: Optional[str]
    topologies: tuple[str, ...]
    z0_min_ohms: Optional[float]
    z0_max_ohms: Optional[float]
    z0_avg_ohms: Optional[float]
    flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ZonePolygon:
    """A filled zone's outline(s) on one copper layer, in mm. Each entry in
    outlines_mm is one closed ring (KiCad's SHAPE_POLY_SET "outline"); a
    zone with disjoint copper islands has more than one. Extracted by
    board_model.py, consumed by planes.py — pure data, no pcbnew handle."""

    net: str
    layer: str
    outlines_mm: tuple[tuple[Point2D, ...], ...]


@dataclass(frozen=True)
class BoardOutline:
    """Bounding box of the board's Edge.Cuts outline, in mm, in pcbnew's own
    coordinate convention (Y increases downward, matching the screen) --
    NOT necessarily the same convention a Gerber-consuming tool expects.
    See gerber2ems_export.board_origin_mm's docstring before using this as
    a "bottom-left" origin for anything outside pcbnew."""

    min_corner: Point2D
    max_corner: Point2D


@dataclass(frozen=True)
class BoardData:
    """Everything the pure engine needs from a routed board, in mm."""

    segments: tuple[TraceSegment, ...]
    vias: tuple[ViaSpan, ...]
    zone_polygons: tuple[ZonePolygon, ...]
    copper_layer_names: tuple[str, ...]
    stackup: Optional[Stackup]
    outline: Optional[BoardOutline]
