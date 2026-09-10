"""Shared net-analysis orchestration used by both cli.py (one net, full
per-sample detail) and board_report.py (many nets, summarized). Pure: works
on an already-loaded BoardData/Stackup, no pcbnew import needed here, so
it's testable with no KiCad installed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from impedancefinder import geometry, net_walk
from impedancefinder.model import BoardData, ImpedanceSample, NetBranch, Stackup, TraceSegment


@dataclass(frozen=True)
class NetAnalysisResult:
    samples: tuple[ImpedanceSample, ...]
    branch_count: int
    partner_net_name: Optional[str]  # None means single-ended

    @property
    def is_differential(self) -> bool:
        return self.partner_net_name is not None


def analyze_net(
    board_data: BoardData, stackup: Stackup, net_name: str, pitch_mm: float, single_ended: bool = False
) -> NetAnalysisResult:
    """Raises ValueError if the net has no routed segments on this board."""
    segments = segments_for(board_data, net_name)
    if not segments:
        raise ValueError(f"no segments found on net {net_name!r}")
    branches = net_walk.sample_net(segments, pitch_mm)
    partner_net = None if single_ended else geometry.find_pair_net_name(net_name)
    partner_segments = segments_for(board_data, partner_net) if partner_net else ()
    if not partner_segments:
        partner_net = None
    results: list[ImpedanceSample] = []
    for branch in branches:
        results.extend(_analyze_branch(branch, partner_segments, stackup, board_data))
    return NetAnalysisResult(samples=tuple(results), branch_count=len(branches), partner_net_name=partner_net)


def _analyze_branch(
    branch: NetBranch, partner_segments: tuple[TraceSegment, ...], stackup: Stackup, board_data: BoardData
) -> tuple[ImpedanceSample, ...]:
    if not partner_segments:
        return tuple(
            geometry.analyze_sample(sample, stackup, board_data.zone_polygons) for sample in branch.samples
        )
    return tuple(
        geometry.analyze_differential_sample(sample, partner_segments, stackup, board_data.zone_polygons)
        for sample in branch.samples
    )


def segments_for(board_data: BoardData, net_name: str) -> tuple[TraceSegment, ...]:
    return tuple(segment for segment in board_data.segments if segment.net == net_name)


def net_length_mm(board_data: BoardData, net_name: str) -> float:
    return sum(segment.length_mm for segment in segments_for(board_data, net_name))
