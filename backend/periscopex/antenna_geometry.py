"""Parametric PCB antenna templates → segments, SVG, KiCad footprint.

Templates (IFA / meander / stub) use a documented λ/4 electrical length with
εeff≈(εr+1)/2. This is a routing-first drawing aid — not an EM / VSWR result.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field

_C_MPS = 299_792_458.0

AntennaTemplate = Literal["ifa", "meander", "stub"]
FitStatus = Literal["ok", "scaled", "overflow", "need_f0"]

_NOTE = (
    "Parametric template from λ/4 (εeff≈(εr+1)/2) — routing aid only, "
    "not an EM / VSWR result. Tune matching on the board."
)


class AntennaSegment(BaseModel):
    points: list[tuple[float, float]]  # local mm, origin = feed
    width_mm: float


class AntennaGeometry(BaseModel):
    template: AntennaTemplate
    fit: FitStatus
    segments: list[AntennaSegment] = Field(default_factory=list)
    total_length_mm: float | None = None
    length_ideal_mm: float | None = None
    scale: float = 1.0
    svg: str | None = None
    kicad_mod: str | None = None
    footprint_name: str | None = None
    note: str = _NOTE
    detail: str = ""


def quarter_wave_mm(f0_mhz: float, er: float) -> float:
    """Electrical λ/4 in mm using εeff≈(εr+1)/2."""
    eeff = (er + 1.0) / 2.0
    f_hz = f0_mhz * 1e6
    return (_C_MPS / (4.0 * f_hz * math.sqrt(eeff))) * 1e3


def build_geometry(
    template: AntennaTemplate,
    *,
    f0_mhz: float | None,
    w_mm: float,
    er: float,
    zone_bbox_mm: tuple[float, float, float, float] | None = None,
    feed_xy: tuple[float, float] | None = None,
) -> AntennaGeometry:
    if f0_mhz is None or f0_mhz <= 0:
        return AntennaGeometry(
            template=template,
            fit="need_f0",
            detail="Set f0 (MHz) to generate radiator geometry.",
        )
    if w_mm <= 0:
        return AntennaGeometry(
            template=template,
            fit="overflow",
            detail="Feed width w_mm must be > 0.",
        )

    ideal = quarter_wave_mm(f0_mhz, er)
    segs_local, length = _template_segments(template, ideal, w_mm)
    fit: FitStatus = "ok"
    scale = 1.0
    detail = f"{template.upper()} template at {f0_mhz:g} MHz."

    avail = _available_span(zone_bbox_mm, feed_xy)
    if avail is not None:
        need_w, need_h = _bbox_size(segs_local)
        free_w, free_h = avail
        max_span = max(free_w, free_h)
        need_span = max(need_w, need_h)
        if need_span > max_span + 1e-6 and max_span > 0:
            scale = max_span / need_span
            min_scale = 0.45
            if scale < min_scale:
                return AntennaGeometry(
                    template=template,
                    fit="overflow",
                    length_ideal_mm=round(ideal, 2),
                    total_length_mm=None,
                    scale=round(scale, 4),
                    detail=(
                        f"Zone too small for {template.upper()} "
                        f"(need ~{need_span:.1f} mm, have {max_span:.1f} mm)."
                    ),
                )
            segs_local = _scale_segments(segs_local, scale)
            length *= scale
            fit = "scaled"
            detail = (
                f"Scaled to {scale:.2f}× to fit antenna zone "
                f"({max_span:.1f} mm free). Retune matching."
            )

    name = f"Antenna_{template.upper()}_{int(round(f0_mhz))}"
    svg = _segments_to_svg(segs_local, w_mm)
    mod = _segments_to_kicad_mod(name, segs_local, w_mm, template)

    return AntennaGeometry(
        template=template,
        fit=fit,
        segments=segs_local,
        total_length_mm=round(length, 2),
        length_ideal_mm=round(ideal, 2),
        scale=round(scale, 4),
        svg=svg,
        kicad_mod=mod,
        footprint_name=name,
        detail=detail,
    )


def _template_segments(
    template: AntennaTemplate,
    length_mm: float,
    w_mm: float,
) -> tuple[list[AntennaSegment], float]:
    if template == "ifa":
        return _ifa(length_mm, w_mm)
    if template == "meander":
        return _meander(length_mm, w_mm)
    return _stub(length_mm, w_mm)


def _ifa(length_mm: float, w_mm: float) -> tuple[list[AntennaSegment], float]:
    """Inverted-F: shorting stub + horizontal arm; feed on the arm at origin.

    Local: feed (0,0) on the arm. Shorting at x=-d toward -Y (GND edge).
    Arm runs to +X. Proportions: stub ≈ 0.12 L, feed offset ≈ 0.15 L.
    """
    L = max(length_mm, 4.0 * w_mm)
    stub_h = max(0.12 * L, 2.0 * w_mm)
    d = max(0.15 * L, 2.0 * w_mm)
    open_x = L - d
    segs = [
        AntennaSegment(points=[(-d, 0.0), (-d, -stub_h)], width_mm=w_mm),
        AntennaSegment(points=[(-d, 0.0), (open_x, 0.0)], width_mm=w_mm),
    ]
    path = stub_h + L
    return segs, path


def _meander(length_mm: float, w_mm: float) -> tuple[list[AntennaSegment], float]:
    """Serpentine that consumes ~length_mm inside a compact bbox."""
    pitch = max(3.0 * w_mm, 1.2)
    run = max(length_mm / 6.0, 4.0 * w_mm)
    pts: list[tuple[float, float]] = [(0.0, 0.0)]
    x = 0.0
    y = 0.0
    going_up = True
    consumed = 0.0
    target = max(length_mm, 4.0 * w_mm)
    guard = 0
    while consumed < target - 1e-6 and guard < 80:
        guard += 1
        dy = run if going_up else -run
        remain = target - consumed
        if remain < abs(dy):
            dy = math.copysign(remain, dy)
        y2 = y + dy
        pts.append((x, y2))
        consumed += abs(dy)
        y = y2
        if consumed >= target - 1e-6:
            break
        remain = target - consumed
        dx = min(pitch, remain)
        x2 = x + dx
        pts.append((x2, y))
        consumed += dx
        x = x2
        going_up = not going_up
    segs = [AntennaSegment(points=pts, width_mm=w_mm)]
    return segs, consumed


def _stub(length_mm: float, w_mm: float) -> tuple[list[AntennaSegment], float]:
    """Open L-stub monopole: short vertical then horizontal arm."""
    L = max(length_mm, 4.0 * w_mm)
    h = max(0.2 * L, 2.0 * w_mm)
    arm = max(L - h, 2.0 * w_mm)
    segs = [
        AntennaSegment(points=[(0.0, 0.0), (0.0, -h)], width_mm=w_mm),
        AntennaSegment(points=[(0.0, -h), (arm, -h)], width_mm=w_mm),
    ]
    return segs, h + arm


def _available_span(
    zone_bbox: tuple[float, float, float, float] | None,
    feed_xy: tuple[float, float] | None,
) -> tuple[float, float] | None:
    """Free width/height from feed into the zone (mm)."""
    if zone_bbox is None or feed_xy is None:
        return None
    xmin, ymin, xmax, ymax = zone_bbox
    fx, fy = feed_xy
    fx = min(max(fx, xmin), xmax)
    fy = min(max(fy, ymin), ymax)
    free_w = max(fx - xmin, xmax - fx)
    free_h = max(fy - ymin, ymax - fy)
    return free_w, free_h


def _bbox_size(segs: list[AntennaSegment]) -> tuple[float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for s in segs:
        for x, y in s.points:
            xs.append(x)
            ys.append(y)
    if not xs:
        return 0.0, 0.0
    return max(xs) - min(xs), max(ys) - min(ys)


def _scale_segments(
    segs: list[AntennaSegment], scale: float,
) -> list[AntennaSegment]:
    out: list[AntennaSegment] = []
    for s in segs:
        out.append(
            AntennaSegment(
                points=[(x * scale, y * scale) for x, y in s.points],
                width_mm=s.width_mm,
            )
        )
    return out


def _segments_to_svg(segs: list[AntennaSegment], default_w: float) -> str:
    xs: list[float] = []
    ys: list[float] = []
    for s in segs:
        for x, y in s.points:
            xs.append(x)
            ys.append(y)
    if not xs:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80"/>'
    pad = max(default_w * 2, 1.0)
    xmin, xmax = min(xs) - pad, max(xs) + pad
    ymin, ymax = min(ys) - pad, max(ys) + pad
    bw = max(xmax - xmin, 1e-3)
    bh = max(ymax - ymin, 1e-3)
    paths: list[str] = []
    for s in segs:
        if len(s.points) < 2:
            continue
        d_parts = []
        for i, (x, y) in enumerate(s.points):
            cmd = "M" if i == 0 else "L"
            d_parts.append(f"{cmd}{x:.3f},{-y:.3f}")
        sw = s.width_mm
        paths.append(
            f'<path d="{" ".join(d_parts)}" fill="none" stroke="#1a1a1a" '
            f'stroke-width="{sw:.3f}" stroke-linecap="round" '
            f'stroke-linejoin="round"/>'
        )
    paths.append(
        f'<circle cx="0" cy="0" r="{max(default_w, 0.3):.3f}" fill="#c45c26"/>'
    )
    vb = f"{xmin:.3f} {-ymax:.3f} {bw:.3f} {bh:.3f}"
    body = "\n  ".join(paths)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" '
        f'width="280" height="160" style="background:#f7f5f2">'
        f"\n  {body}\n</svg>"
    )


def _segments_to_kicad_mod(
    name: str,
    segs: list[AntennaSegment],
    w_mm: float,
    template: AntennaTemplate,
) -> str:
    lines = [
        f'(footprint "{name}"',
        "  (version 20240108)",
        '  (generator "periscope")',
        '  (layer "F.Cu")',
        f'  (descr "Periscope {template.upper()} PCB antenna template '
        f'— not EM-validated")',
        "  (attr smd)",
        f'  (pad "1" smd circle (at 0 0) (size {w_mm * 2:.4f} {w_mm * 2:.4f}) '
        f'(layers "F.Cu") (uuid 00000000-0000-4000-8000-000000000001))',
    ]
    if template == "ifa" and segs:
        tip = segs[0].points[-1]
        lines.append(
            f'  (pad "2" smd circle (at {tip[0]:.4f} {tip[1]:.4f}) '
            f"(size {w_mm * 2:.4f} {w_mm * 2:.4f}) "
            f'(layers "F.Cu") (uuid 00000000-0000-4000-8000-000000000002))'
        )
    uid = 10
    for s in segs:
        pts = s.points
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            lines.append(
                f"  (fp_line (start {x1:.4f} {y1:.4f}) (end {x2:.4f} {y2:.4f}) "
                f"(stroke (width {s.width_mm:.4f}) (type default)) "
                f'(layer "F.Cu") (uuid 00000000-0000-4000-8000-{uid:012d}))'
            )
            uid += 1
    lines.append(")")
    return "\n".join(lines) + "\n"
