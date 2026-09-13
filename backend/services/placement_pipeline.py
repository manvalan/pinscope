"""Placement pipeline — parallel to analysis (no LLM).

Stages: ensure_graph → classify → write_plan → pack (F2 gated).
Writes ``placement_plan.json`` (+ ``functional_groups.json``) and
``placement_pack.json`` (mm only when PCB + numeric layout_rules exist).
Uses ``placement_status`` so analysis ``status`` is untouched.
"""

from __future__ import annotations

import logging
from pathlib import Path

from backend.pinscopex.functional_groups import build_placement_plan
from backend.pinscopex.graph import build_graph
from backend.pinscopex.models import ComponentConstraints, DesignGraph, LayoutGraph
from backend.pinscopex.placement_pack import build_placement_pack
from backend.services import projects as proj_svc
from backend.services.pipeline import PipelineWorkspace, broker
from backend.services.storage import StorageBackend

logger = logging.getLogger(__name__)

_PLACEMENT_ACTIVE = frozenset({"queued", "running"})
_ANALYSIS_BUSY = frozenset({
    proj_svc.STATUS_QUEUED,
    proj_svc.STATUS_RUNNING,
})


def _load_constraints_map(extracted_dir: Path) -> dict[str, ComponentConstraints]:
    """Load per-MPN extractions without importing the Anthropic review path."""
    result: dict[str, ComponentConstraints] = {}
    if not extracted_dir.is_dir():
        return result
    for f in extracted_dir.glob("*.json"):
        try:
            c = ComponentConstraints.model_validate_json(
                f.read_text(encoding="utf-8"),
            )
        except Exception:
            logger.exception("skipping bad extraction %s", f)
            continue
        result[c.mpn] = c
    return result


def _publish(project_id: str, event: str, data: dict) -> None:
    broker.publish(project_id, event, data)


def _step(project_id: str, stage: str, status: str, detail: str = "") -> None:
    payload: dict = {"stage": stage, "status": status}
    if detail:
        payload["detail"] = detail
    _publish(project_id, "placement_step_update", payload)


async def run_placement_pipeline(
    storage: StorageBackend, user_id: str, project_id: str,
) -> None:
    """Run the placement topology pipeline (no extraction / review)."""
    meta = proj_svc.get_project(storage, user_id, project_id)
    if not meta:
        raise ValueError(f"Project {project_id} not found")

    # Boot: queued → running on placement_status only.
    if meta.placement_status not in _PLACEMENT_ACTIVE:
        logger.warning(
            "placement worker booted with placement_status=%s for %s; exiting",
            meta.placement_status, project_id,
        )
        return

    proj_svc.update_project(
        storage, user_id, project_id,
        placement_status="running",
        placement_cancel_requested=False,
        placement_state=None,
    )

    try:
        async with PipelineWorkspace(storage, user_id, project_id) as ws:
            if _cancelled(storage, user_id, project_id):
                _finish_cancelled(storage, user_id, project_id)
                return

            graph = await _ensure_graph(ws, meta, project_id)
            if _cancelled(storage, user_id, project_id):
                _finish_cancelled(storage, user_id, project_id)
                return

            _step(project_id, "classify", "running", "domains and satellite roles")
            extracted_dir = ws.local_path("extracted")
            cmap = _load_constraints_map(extracted_dir)
            plan = build_placement_plan(graph, cmap)
            _step(
                project_id, "classify", "complete",
                f"{len(plan.domains)} domains, {len(plan.groups)} IC groups",
            )

            if _cancelled(storage, user_id, project_id):
                _finish_cancelled(storage, user_id, project_id)
                return

            _step(project_id, "write_plan", "running")
            plan_path = ws.local_path("placement_plan.json")
            plan_json = plan.model_dump_json(indent=2) + "\n"
            plan_path.write_text(plan_json)
            # Keep functional_groups.json in sync for consumers that already read it.
            fg_path = ws.local_path("functional_groups.json")
            fg_path.write_text(plan_json)
            ws._upload_file("placement_plan.json")
            ws._upload_file("functional_groups.json")
            _step(project_id, "write_plan", "complete", "placement_plan.json")

            if _cancelled(storage, user_id, project_id):
                _finish_cancelled(storage, user_id, project_id)
                return

            _step(project_id, "pack", "running", "F2 gated pack")
            layout = _load_layout(ws)
            pack = build_placement_pack(plan, layout, graph)
            pack_path = ws.local_path("placement_pack.json")
            pack_path.write_text(pack.model_dump_json(indent=2) + "\n")
            ws._upload_file("placement_pack.json")
            pack_detail = (
                f"{len(pack.placements)} proposals"
                if pack.status == "packed"
                else f"skipped:{pack.skip_reason}"
            )
            _step(project_id, "pack", "complete", pack_detail)

        proj_svc.update_project(
            storage, user_id, project_id,
            placement_status="complete",
            placement_state={
                "domains": len(plan.domains),
                "groups": len(plan.groups),
                "pack_status": pack.status,
                "pack_count": len(pack.placements),
                "pack_skip_reason": pack.skip_reason,
            },
            placement_cancel_requested=False,
        )
        _publish(project_id, "placement_complete", {
            "domains": len(plan.domains),
            "groups": len(plan.groups),
            "pack_status": pack.status,
            "pack_count": len(pack.placements),
            "pack_skip_reason": pack.skip_reason,
        })
    except Exception as e:
        logger.exception("placement pipeline failed for %s", project_id)
        proj_svc.update_project(
            storage, user_id, project_id,
            placement_status="error",
            placement_state={"error": str(e)},
        )
        _publish(project_id, "placement_error", {"error": str(e)})


async def _ensure_graph(ws: PipelineWorkspace, meta, project_id: str) -> DesignGraph:
    """Reuse design_graph.json when present; otherwise graph_build only."""
    graph_path = ws.local_path("design_graph.json")
    if graph_path.is_file():
        _step(project_id, "ensure_graph", "running", "reusing design_graph.json")
        graph = DesignGraph.model_validate_json(graph_path.read_text(encoding="utf-8"))
        _step(
            project_id, "ensure_graph", "complete",
            f"{len(graph.components)} components (cached)",
        )
        return graph

    _step(project_id, "ensure_graph", "running", "building design graph")
    bom_path = ws.local_path("uploads/bom.csv")
    netlist_path = ws.netlist_local_path()
    if not bom_path.is_file() or not Path(netlist_path).is_file():
        raise FileNotFoundError("Missing BOM or netlist for placement graph_build")

    col_map = meta.bom_columns or {}
    graph = build_graph(
        str(netlist_path),
        str(bom_path),
        str(ws.local_path("extracted")),
        str(ws.local_path("patterns")),
        str(ws.local_path("models")),
        reference_col=col_map.get("reference", "Reference"),
        mpn_col=col_map.get("mpn", "Manufacturer Part Number"),
        include_subdesigns=(
            set(meta.netlist_subdesigns)
            if meta.netlist_subdesigns is not None
            else None
        ),
        pcb_path=ws.local_path("uploads/pcb.kicad_pcb"),
    )
    graph_path.write_text(graph.model_dump_json(indent=2) + "\n")
    ws._upload_file("design_graph.json")
    _step(
        project_id, "ensure_graph", "complete",
        f"{len(graph.components)} components, {len(graph.nets)} nets",
    )
    return graph


def _load_layout(ws: PipelineWorkspace) -> LayoutGraph | None:
    """Reuse layout_graph.json, or parse uploads/pcb.kicad_pcb once."""
    cached = ws.local_path("layout_graph.json")
    if cached.is_file():
        try:
            return LayoutGraph.model_validate_json(
                cached.read_text(encoding="utf-8"),
            )
        except Exception:
            logger.exception("bad layout_graph.json — trying pcb parse")

    pcb = ws.local_path("uploads/pcb.kicad_pcb")
    if not pcb.is_file():
        return None
    try:
        from backend.pinscopex.parsers_kicad_pcb import parse_kicad_pcb

        layout = parse_kicad_pcb(pcb)
        cached.write_text(layout.model_dump_json(indent=2) + "\n")
        ws._upload_file("layout_graph.json")
        return layout
    except Exception:
        logger.exception("kicad_pcb parse failed during placement pack")
        return None


def _cancelled(storage: StorageBackend, user_id: str, project_id: str) -> bool:
    meta = proj_svc.get_project(storage, user_id, project_id)
    return bool(meta and meta.placement_cancel_requested)


def _finish_cancelled(storage: StorageBackend, user_id: str, project_id: str) -> None:
    proj_svc.update_project(
        storage, user_id, project_id,
        placement_status="cancelled",
        placement_cancel_requested=False,
    )
    _publish(project_id, "placement_cancelled", {})


def placement_busy(meta: proj_svc.ProjectMeta) -> bool:
    return (meta.placement_status or "draft") in _PLACEMENT_ACTIVE


def analysis_busy(meta: proj_svc.ProjectMeta) -> bool:
    return meta.status in _ANALYSIS_BUSY
