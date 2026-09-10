"""Standalone impedance calculator (no PCB, no findings)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.pinscopex.impedance import (
    GeometryError,
    TraceGeometry,
    coupled_diff_z,
    cpw_z0,
    export_kicad_dru,
    microstrip_z0,
    solve_width,
    stackup_targets,
    stripline_z0,
)

router = APIRouter(tags=["impedance"])


class ImpedanceRequest(BaseModel):
    mode: Literal["trace", "stackup"] = "trace"
    kind: Literal["microstrip", "stripline", "cpw", "diff"] | None = None
    h: float
    er: float
    t: float = 0.035
    w: float | None = None
    s: float | None = None
    target_z: float | None = None


def _z_for_kind(kind: str, geo: TraceGeometry) -> dict:
    if kind == "microstrip":
        return {"z0": microstrip_z0(geo), "w_mm": geo.w, "s_mm": geo.s, "kind": kind}
    if kind == "stripline":
        return {"z0": stripline_z0(geo), "w_mm": geo.w, "s_mm": geo.s, "kind": kind}
    if kind == "cpw":
        return {"z0": cpw_z0(geo), "w_mm": geo.w, "s_mm": geo.s, "kind": kind}
    if kind == "diff":
        zodd, zeven, zdiff = coupled_diff_z(geo)
        return {
            "z0": None,
            "zodd": zodd,
            "zeven": zeven,
            "zdiff": zdiff,
            "w_mm": geo.w,
            "s_mm": geo.s,
            "kind": kind,
        }
    raise GeometryError(f"unknown kind {kind}")


@router.post("/impedance")
def compute_impedance(body: ImpedanceRequest):
    try:
        if body.mode == "stackup":
            s = body.s if body.s is not None else 0.2
            targets = stackup_targets(h=body.h, er=body.er, t=body.t, s=s)
            return {
                "targets": {k: asdict(v) for k, v in targets.items()},
                "kicad_dru": export_kicad_dru(targets),
            }
        kind = body.kind or "microstrip"
        w = body.w
        if body.target_z is not None:
            w = solve_width(kind, body.target_z, body.h, body.er, body.t, s=body.s)
        geo = TraceGeometry(h=body.h, er=body.er, t=body.t, w=w, s=body.s)
        return _z_for_kind(kind, geo)
    except GeometryError as exc:
        raise HTTPException(400, str(exc)) from exc
