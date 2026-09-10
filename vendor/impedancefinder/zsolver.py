"""Closed-form characteristic-impedance solvers.

Static (zero-frequency) Hammerstad-Jensen microstrip and Cohn/IPC-2141
stripline formulas, ported term-for-term from KiCad's own pcb_calculator
engine (common/transline_calculations/{microstrip,stripline}.cpp, verified
against the KiCad 10.0.4 source tag) with the frequency-dispersion, cover,
and conductor/dielectric-loss terms dropped — this tool needs the static Z0
for post-route verification, not a full RF loss/dispersion analysis.
Differential corrections use the separate, simpler IPC-2141A empirical
odd-mode formulas rather than KiCad's full coupled-line even/odd-mode solver.

All functions are pure: same inputs always give the same output, no state,
no I/O. Dimensions are millimetres, er is dimensionless, results are ohms.
"""
from __future__ import annotations

import math

_FREE_SPACE_IMPEDANCE_OHMS = 376.730313668  # NIST CODATA Z0


def _thickness_width_correction(u: float, t_h: float, er: float) -> float:
    """Hammerstad-Jensen effective-width correction for finite copper
    thickness (delta_u in microstrip.cpp)."""
    if t_h <= 0:
        return 0.0
    delta_u = (t_h / math.pi) * math.log(
        1.0 + (4.0 * math.e) * math.tanh(math.sqrt(6.517 * u)) ** 2 / t_h
    )
    return 0.5 * delta_u * (1.0 + 1.0 / math.cosh(math.sqrt(er - 1.0)))


def _homogeneous_impedance_ohms(u: float) -> float:
    """Hammerstad's single-formula air-filled microstrip impedance for
    shape ratio u = W/H, valid across the full range of u."""
    shape = 6.0 + (2.0 * math.pi - 6.0) * math.exp(-((30.666 / u) ** 0.7528))
    return (_FREE_SPACE_IMPEDANCE_OHMS / (2.0 * math.pi)) * math.log(
        shape / u + math.sqrt(1.0 + 4.0 / (u * u))
    )


def _filling_factor(u: float, er: float) -> float:
    """Hammerstad-Jensen dielectric filling factor q for shape ratio u."""
    u2, u3, u4 = u * u, u**3, u**4
    a = (
        1.0
        + math.log((u4 + u2 / 2704.0) / (u4 + 0.432)) / 49.0
        + math.log(1.0 + u3 / 5929.741) / 18.7
    )
    b = 0.564 * ((er - 0.9) / (er + 3.0)) ** 0.053
    return (1.0 + 10.0 / u) ** (-a * b)


def microstrip_z0(width_mm: float, height_mm: float, er: float, t_mm: float = 0.0) -> float:
    """Single-ended microstrip Z0 via Hammerstad-Jensen, static (f=0).

    width_mm: trace width. height_mm: dielectric height to the reference
    plane below the trace. er: dielectric relative permittivity. t_mm:
    copper thickness (0 disables the thickness correction).
    """
    u = width_mm / height_mm
    t_h = t_mm / height_mm

    u_er = u + _thickness_width_correction(u, t_h, er)
    z0_dielectric = _homogeneous_impedance_ohms(u_er)

    q = _filling_factor(u_er, er) - (2.0 * math.log(2.0) / math.pi) * (t_h / math.sqrt(u_er))
    er_eff = 0.5 * (er + 1.0) + 0.5 * q * (er - 1.0)

    return z0_dielectric / math.sqrt(er_eff)


def _stripline_line_impedance_ohms(
    plane_spacing_mm: float, width_mm: float, t_mm: float, er: float
) -> float:
    """Cohn's stripline formula as used by KiCad's stripline.cpp, specialized
    to the width-dominated (>=0.35) and narrow-trace regimes."""
    hmt = plane_spacing_mm - t_mm
    if width_mm / hmt >= 0.35:
        wide = width_mm + (
            2.0 * plane_spacing_mm * math.log((2.0 * plane_spacing_mm - t_mm) / hmt)
            - t_mm * math.log(plane_spacing_mm**2 / hmt**2 - 1.0)
        ) / math.pi
        return _FREE_SPACE_IMPEDANCE_OHMS * hmt / math.sqrt(er) / 4.0 / wide

    ratio = t_mm / width_mm
    if ratio > 1.0:
        ratio = width_mm / t_mm
    effective_diameter = (
        1.0 + ratio / math.pi * (1.0 + math.log(4.0 * math.pi / ratio)) + 0.236 * ratio**1.65
    )
    effective_diameter *= (t_mm / 2.0) if (t_mm / width_mm) > 1.0 else (width_mm / 2.0)
    return (
        _FREE_SPACE_IMPEDANCE_OHMS
        / (2.0 * math.pi * math.sqrt(er))
        * math.log(4.0 * plane_spacing_mm / math.pi / effective_diameter)
    )


def stripline_z0(width_mm: float, b_mm: float, er: float, t_mm: float) -> float:
    """Symmetric (centered) stripline Z0, ported from KiCad's Cohn-derived
    stripline.cpp, specialized to a trace centered between two reference
    planes spaced b_mm apart.

    t_mm must be > 0: the formula divides by hmt = b_mm - t_mm and takes a
    log that is singular at t_mm == 0. Real copper always has finite
    thickness, so callers must supply it (e.g. 0.035 mm for 1 oz copper).
    """
    if t_mm <= 0:
        raise ValueError("stripline_z0 requires t_mm > 0 (finite copper thickness)")
    return _stripline_line_impedance_ohms(b_mm, width_mm, t_mm, er)


def diff_microstrip_z0(
    width_mm: float, height_mm: float, spacing_mm: float, er: float, t_mm: float = 0.0
) -> float:
    """Edge-coupled differential microstrip Z0: IPC-2141A's empirical
    odd-mode correction applied to the single-ended Hammerstad-Jensen value.

    spacing_mm: edge-to-edge gap between the two traces of the pair.
    """
    z0 = microstrip_z0(width_mm, height_mm, er, t_mm)
    return 2.0 * z0 * (1.0 - 0.48 * math.exp(-0.96 * spacing_mm / height_mm))


def diff_stripline_z0(
    width_mm: float, b_mm: float, spacing_mm: float, er: float, t_mm: float
) -> float:
    """Edge-coupled differential stripline Z0: IPC-2141A's empirical
    odd-mode correction applied to the single-ended stripline value."""
    z0 = stripline_z0(width_mm, b_mm, er, t_mm)
    return 2.0 * z0 * (1.0 - 0.347 * math.exp(-2.9 * spacing_mm / b_mm))


def cpwg_z0(*_args, **_kwargs) -> float:
    """Grounded coplanar waveguide Z0 — not implemented yet.

    CPWG needs the coplanar-ground-gap geometry in addition to the
    reference-plane height, which isn't modeled by this solver set yet.
    Raises explicitly so a CPWG classification surfaces as a clear
    "not supported" flag (see geometry.classify_topology) instead of a
    silently wrong number.
    """
    raise NotImplementedError("CPWG closed-form solver is not implemented yet")
