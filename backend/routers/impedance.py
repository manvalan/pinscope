"""Impedance calculator and ImpedenceFinder analysis of PCB nets."""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
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
from backend.pinscopex.impedance_traces import (
    NET_WALK_PITCH_MM,
    analyze_specified_nets,
)
from backend.pinscopex.parsers_kicad_pcb import parse_kicad_pcb
from backend.routers.deps import get_storage, resolve_or_404
from backend.services import projects as proj_svc

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


class NetsRequest(BaseModel):
    nets: list[str]
    pitch_mm: float | None = None


@router.get("/projects/{project_id}/impedance/nets")
async def get_project_impedance_nets(project_id: str, request: Request):
    storage = get_storage(request)
    owner, _ = await resolve_or_404(request, project_id)
    prefix = proj_svc.project_prefix(owner, project_id)
    key = f"{prefix}/impedance_nets.json"
    if not storage.exists(key):
        return {"pitch_mm": NET_WALK_PITCH_MM, "nets": [], "skipped": "not run"}
    return storage.read_json(key)


@router.post("/projects/{project_id}/impedance/nets")
async def analyze_project_impedance_nets(
    project_id: str, body: NetsRequest, request: Request,
):
    storage = get_storage(request)
    owner, _ = await resolve_or_404(request, project_id)
    prefix = proj_svc.project_prefix(owner, project_id)
    pcb_key = f"{prefix}/uploads/pcb.kicad_pcb"
    if not storage.exists(pcb_key):
        raise HTTPException(400, "No .kicad_pcb on this project")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".kicad_pcb")
    try:
        tmp.write(storage.read_bytes(pcb_key))
        tmp.close()
        layout = parse_kicad_pcb(tmp.name)
    finally:
        os.unlink(tmp.name)
    pitch = body.pitch_mm if body.pitch_mm is not None else NET_WALK_PITCH_MM
    try:
        rows = analyze_specified_nets(layout, body.nets, pitch)
    except GeometryError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"pitch_mm": pitch, "nets": rows, "skipped": None}
