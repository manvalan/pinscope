"""Pinscope facade over ImpedanceFinder's closed-form Z0 solver.

All Z0 numbers come from ImpedenceFinder (`vendor/impedancefinder`,
Hammerstad-Jensen / Cohn as in KiCad pcb_calculator). This module only
validates geometry, inverts width for a target Z, and exports KiCad
custom-rule advice. It never emits Findings. CPWG is not implemented
upstream — we raise instead of inventing a number.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.vendor_path import ensure_impedancefinder

ensure_impedancefinder()
from impedancefinder import zsolver


class GeometryError(ValueError):
    """Trace geometry is missing, non-physical, or unsupported."""


@dataclass(frozen=True)
class TraceGeometry:
    h: float
    er: float
    t: float
    w: float | None = None
    s: float | None = None


@dataclass(frozen=True)
class ImpedanceResult:
    kind: str
    w_mm: float | None = None
    s_mm: float | None = None
    z0: float | None = None
    zodd: float | None = None
    zeven: float | None = None
    zdiff: float | None = None
    formula: str = "impedancefinder"


def _require_positive(name: str, value: float | None) -> float:
    if value is None or value <= 0:
        raise GeometryError(f"{name} must be > 0")
    return float(value)


def microstrip_z0(geo: TraceGeometry) -> float:
    h = _require_positive("h", geo.h)
    er = _require_positive("er", geo.er)
    w = _require_positive("w", geo.w)
    t = geo.t
    if t < 0:
        raise GeometryError("t must be >= 0")
    return zsolver.microstrip_z0(w, h, er, t)


def stripline_z0(geo: TraceGeometry) -> float:
    h = _require_positive("h", geo.h)
    er = _require_positive("er", geo.er)
    w = _require_positive("w", geo.w)
    t = _require_positive("t", geo.t)
    try:
        return zsolver.stripline_z0(w, h, er, t)
    except ValueError as exc:
        raise GeometryError(str(exc)) from exc


def coupled_diff_z(geo: TraceGeometry) -> tuple[float, float, float]:
    """Return (Zodd, Zeven, Zdiff) via ImpedanceFinder IPC-2141A odd-mode."""
    s = _require_positive("s", geo.s)
    h = _require_positive("h", geo.h)
    z0 = microstrip_z0(geo)
    zdiff = zsolver.diff_microstrip_z0(
        _require_positive("w", geo.w), h, s, geo.er, geo.t
    )
    zodd = zdiff / 2.0
    zeven = 2.0 * z0 - zodd
    return (zodd, zeven, zdiff)


def cpw_z0(geo: TraceGeometry) -> float:
    _require_positive("h", geo.h)
    _require_positive("er", geo.er)
    _require_positive("w", geo.w)
    _require_positive("s", geo.s)
    try:
        return zsolver.cpwg_z0(geo.w, geo.h, geo.s, geo.er, geo.t)
    except NotImplementedError as exc:
        raise GeometryError(str(exc)) from exc


def solve_width(
    kind: str,
    target_z: float,
    h: float,
    er: float,
    t: float,
    s: float | None = None,
) -> float:
    _require_positive("target_z", target_z)
    _require_positive("h", h)
    _require_positive("er", er)
    if kind == "stripline":
        _require_positive("t", t)
    elif t < 0:
        raise GeometryError("t must be >= 0")

    def z_of(w: float) -> float:
        geo = TraceGeometry(h=h, er=er, t=t, w=w, s=s)
        if kind == "microstrip":
            return microstrip_z0(geo)
        if kind == "stripline":
            return stripline_z0(geo)
        if kind == "diff":
            return coupled_diff_z(geo)[2]
        if kind == "cpw":
            return cpw_z0(geo)
        raise GeometryError(f"unknown kind {kind}")

    lo, hi = 0.01 * h, 40.0 * h
    z_lo, z_hi = z_of(lo), z_of(hi)
    if not (min(z_lo, z_hi) <= target_z <= max(z_lo, z_hi)):
        raise GeometryError("target_z is outside the solvable width range")
    for _ in range(48):
        mid = 0.5 * (lo + hi)
        zm = z_of(mid)
        if zm > target_z:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def stackup_targets(
    h: float,
    er: float,
    t: float,
    s: float,
) -> dict[str, ImpedanceResult]:
    w50 = solve_width("microstrip", 50.0, h, er, t)
    w90 = solve_width("diff", 90.0, h, er, t, s=s)
    w100 = solve_width("diff", 100.0, h, er, t, s=s)
    z50 = microstrip_z0(TraceGeometry(h=h, er=er, t=t, w=w50))
    _, _, zd90 = coupled_diff_z(TraceGeometry(h=h, er=er, t=t, w=w90, s=s))
    _, _, zd100 = coupled_diff_z(TraceGeometry(h=h, er=er, t=t, w=w100, s=s))
    return {
        "microstrip_50": ImpedanceResult(kind="microstrip", w_mm=w50, z0=z50),
        "diff_90": ImpedanceResult(kind="diff", w_mm=w90, s_mm=s, zdiff=zd90),
        "diff_100": ImpedanceResult(kind="diff", w_mm=w100, s_mm=s, zdiff=zd100),
    }


def export_kicad_dru(targets: dict[str, ImpedanceResult]) -> str:
    """KiCad custom-rule advice. The user applies it; Pinscope does not DRC the PCB."""
    lines = [
        "(version 1)",
        "# Pinscope impedance advice (ImpedanceFinder solver) — apply in pcbnew.",
    ]
    mapping = (
        ("microstrip_50", "PINSCOPE_50OHM", "50Ohm"),
        ("diff_90", "PINSCOPE_90OHM_USB", "90Ohm"),
        ("diff_100", "PINSCOPE_100OHM_DIFF", "100Ohm"),
    )
    for key, rule, netclass in mapping:
        r = targets[key]
        w = r.w_mm
        if w is None:
            continue
        lines.append("")
        lines.append(f"(rule {rule}")
        lines.append(f'  (constraint track_width (min {w:.4f}mm) (opt {w:.4f}mm) (max {w:.4f}mm))')
        if r.s_mm:
            lines.append(
                f"  (constraint diff_pair_gap (min {r.s_mm:.4f}mm) "
                f"(opt {r.s_mm:.4f}mm) (max {r.s_mm:.4f}mm))"
            )
        lines.append(f'  (condition "A.NetClass == \'{netclass}\'"))')
    return "\n".join(lines) + "\n"
