"""Profile assembly and export.

build_profile is pure. to_csv is this module's impure edge (writes a file).

gerber2ems export/import now lives in gerber2ems_export.py, not here (see
docs/field-solver-export-plan.md for why it grew into its own module and
what was verified against the real installed tool). to_rf2dfieldsolver
below is still a stub -- RF2DFieldSolver is GUI-only with no batch mode
(verified against its main.cpp), so an automated export-and-read-back loop
isn't possible for it the way it is for gerber2ems; see the plan doc for
the full comparison.
"""
from __future__ import annotations

import csv

from impedancefinder import net_analysis
from impedancefinder.model import BoardData, ImpedanceSample, NetProfile, NetSummary
from impedancefinder.net_analysis import NetAnalysisResult

_CSV_COLUMNS = (
    "distance_along_net_mm",
    "x_mm",
    "y_mm",
    "layer",
    "width_mm",
    "topology",
    "z0_ohms",
    "flags",
)

_SUMMARY_CSV_COLUMNS = (
    "net_name",
    "length_mm",
    "branch_count",
    "is_differential",
    "partner_net_name",
    "topologies",
    "z0_min_ohms",
    "z0_max_ohms",
    "z0_avg_ohms",
    "flags",
)


def build_profile(net_name: str, samples: tuple[ImpedanceSample, ...]) -> NetProfile:
    return NetProfile(net_name=net_name, samples=samples)


def to_csv(profile: NetProfile, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(_CSV_COLUMNS)
        for sample in profile.samples:
            writer.writerow(_csv_row(sample))


def _csv_row(sample: ImpedanceSample) -> tuple:
    return (
        sample.distance_along_net_mm,
        sample.position.x_mm,
        sample.position.y_mm,
        sample.layer,
        sample.width_mm,
        sample.topology.name,
        "" if sample.z0_ohms is None else sample.z0_ohms,
        ";".join(sample.flags),
    )


def summarize_net(net_name: str, board_data: BoardData, result: NetAnalysisResult) -> NetSummary:
    """Collapse one net's per-sample analysis into a single report row —
    used by board_report.py for a batch of nets, one closed-form pass each,
    no openEMS involved."""
    z0_values = tuple(sample.z0_ohms for sample in result.samples if sample.z0_ohms is not None)
    return NetSummary(
        net_name=net_name,
        length_mm=net_analysis.net_length_mm(board_data, net_name),
        branch_count=result.branch_count,
        is_differential=result.is_differential,
        partner_net_name=result.partner_net_name,
        topologies=_unique_in_order(sample.topology.name for sample in result.samples),
        z0_min_ohms=min(z0_values) if z0_values else None,
        z0_max_ohms=max(z0_values) if z0_values else None,
        z0_avg_ohms=sum(z0_values) / len(z0_values) if z0_values else None,
        flags=_unique_in_order(flag for sample in result.samples for flag in sample.flags),
    )


def _unique_in_order(values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def summaries_to_csv(summaries: tuple[NetSummary, ...], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(_SUMMARY_CSV_COLUMNS)
        for summary in summaries:
            writer.writerow(_summary_csv_row(summary))


def _summary_csv_row(summary: NetSummary) -> tuple:
    return (
        summary.net_name,
        summary.length_mm,
        summary.branch_count,
        summary.is_differential,
        summary.partner_net_name or "",
        ";".join(summary.topologies),
        "" if summary.z0_min_ohms is None else summary.z0_min_ohms,
        "" if summary.z0_max_ohms is None else summary.z0_max_ohms,
        "" if summary.z0_avg_ohms is None else summary.z0_avg_ohms,
        ";".join(summary.flags),
    )


def to_rf2dfieldsolver(profile: NetProfile, path: str) -> None:
    """Export a 2D cross-section for RF2DFieldSolver at a flagged region.
    Not implemented yet — see the module docstring and
    docs/field-solver-export-plan.md."""
    raise NotImplementedError("RF2DFieldSolver export is not implemented yet")
