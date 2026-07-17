"""Pipeline start, SSE events, and status endpoints.

Pipelines run in a Cloud Run Job worker (or, in local dev, a child
subprocess). The API only enqueues, transitions status with
``if-generation-match`` for idempotency, and tails the GCS-backed event
log for SSE.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from pydantic import BaseModel

from backend.routers.deps import get_storage, resolve_or_404
from backend.services import event_bridge, job_runner
from backend.services import projects as proj_svc

logger = logging.getLogger(__name__)

VALID_REGEN_STAGES = {"derating"}


class RegenRequest(BaseModel):
    stages: list[str]


router = APIRouter(tags=["pipeline"])


# Statuses from which a fresh ``/start`` is allowed to transition into queued.
_START_OK_FROM = frozenset({
    proj_svc.STATUS_DRAFT,
    proj_svc.STATUS_COMPLETE,
    proj_svc.STATUS_ERROR,
    proj_svc.STATUS_CANCELLED,
})


def _project_active(meta: proj_svc.ProjectMeta) -> bool:
    """A project is "active" if a worker is or could be running for it.

    Used as the running-guard. We trust the meta status as the primary
    signal, and only fall back to the Cloud Run execution state when the
    status is one we expect a worker to be touching. This deliberately
    does NOT call get_execution_state on every request — it's an admin
    API call. The stale-running sweeper is responsible for clearing
    zombie ``running`` projects.
    """
    return meta.status in (proj_svc.STATUS_QUEUED, proj_svc.STATUS_RUNNING)


@router.post("/pipeline/{project_id}/start", status_code=202)
async def start(project_id: str, request: Request):
    from backend.routers.deps import get_user_id
    from backend.services.billing_hook import get_billing

    storage = get_storage(request)
    owner_user_id, meta = await resolve_or_404(request, project_id)
    if not meta.has_bom or not meta.has_netlist:
        raise HTTPException(400, "Upload BOM and netlist before starting pipeline")

    # Ensure the caller has at least their trial credits allocated.  The
    # pipeline itself enforces pause-on-empty — this just makes sure a
    # brand-new user isn't blocked before their grant is issued.
    get_billing().ensure_trial_grant(storage, get_user_id(request))

    # Idempotent enqueue: only one ``draft|complete|error|cancelled`` ->
    # ``queued`` transition can win. Concurrent /start clicks => 409.
    from backend._version import PINSCOPE_VERSION
    try:
        proj_svc.transition_status(
            storage, owner_user_id, project_id,
            from_status=_START_OK_FROM,
            to_status=proj_svc.STATUS_QUEUED,
            cancel_requested=False,
            execution_name=None,
            pinscope_version=PINSCOPE_VERSION,
        )
    except proj_svc.StatusConflict:
        raise HTTPException(409, "Pipeline already running or queued")

    try:
        execution_name = job_runner.enqueue_pipeline(
            project_id, owner_user_id, resume=False, free=False,
        )
    except Exception:
        logger.exception("enqueue_pipeline failed for %s", project_id)
        # Roll the meta back so the user can retry.
        proj_svc.update_project(
            storage, owner_user_id, project_id,
            status=proj_svc.STATUS_ERROR,
            pipeline_state={"error": "Failed to enqueue worker"},
        )
        raise HTTPException(503, "Failed to enqueue pipeline worker; please retry")

    proj_svc.update_project(
        storage, owner_user_id, project_id, execution_name=execution_name,
    )
    return {"status": "started", "project_id": project_id}


@router.post("/pipeline/{project_id}/cancel")
async def cancel(project_id: str, request: Request):
    """Soft-cancel: set ``cancel_requested`` so the worker exits cleanly.

    The worker re-reads this flag inside ``_charge_for_logs`` after every
    Claude API call (throttled). Cancellation latency is bounded by the
    in-flight call's duration, typ 1–60s.
    """
    storage = get_storage(request)
    owner_user_id, meta = await resolve_or_404(request, project_id)
    if not _project_active(meta):
        raise HTTPException(409, f"Pipeline is not running (status={meta.status})")
    proj_svc.request_cancel(storage, owner_user_id, project_id)
    return {"status": "cancel_requested", "project_id": project_id}


@router.post("/pipeline/{project_id}/estimate")
async def estimate(project_id: str, request: Request):
    """Pre-flight cost estimate — read-only, no side effects."""
    from backend.services.cost_estimator import estimate_pipeline_cost

    storage = get_storage(request)
    owner_user_id, meta = await resolve_or_404(request, project_id)
    if not meta.has_bom:
        raise HTTPException(400, "Upload a BOM before requesting an estimate")
    try:
        est = estimate_pipeline_cost(storage, owner_user_id, project_id)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc)) from exc
    return est.model_dump()


@router.post("/pipeline/{project_id}/resume", status_code=202)
async def resume(project_id: str, request: Request):
    """Resume a pipeline that was paused for insufficient credits."""
    storage = get_storage(request)
    owner_user_id, meta = await resolve_or_404(request, project_id)
    if meta.status != proj_svc.STATUS_PAUSED:
        raise HTTPException(
            400,
            f"Project is not paused (status={meta.status}); nothing to resume.",
        )
    if not meta.has_bom or not meta.has_netlist:
        raise HTTPException(400, "Project is missing BOM or netlist")

    try:
        proj_svc.transition_status(
            storage, owner_user_id, project_id,
            from_status=proj_svc.STATUS_PAUSED,
            to_status=proj_svc.STATUS_QUEUED,
            cancel_requested=False,
        )
    except proj_svc.StatusConflict:
        raise HTTPException(409, "Project state changed; refresh and retry")

    try:
        execution_name = job_runner.enqueue_pipeline(
            project_id, owner_user_id, resume=True, free=False,
        )
    except Exception:
        logger.exception("enqueue_pipeline (resume) failed for %s", project_id)
        proj_svc.update_project(
            storage, owner_user_id, project_id,
            status=proj_svc.STATUS_ERROR,
            pipeline_state={"error": "Failed to enqueue worker"},
        )
        raise HTTPException(503, "Failed to enqueue pipeline worker; please retry")

    proj_svc.update_project(
        storage, owner_user_id, project_id, execution_name=execution_name,
    )
    return {"status": "resumed", "project_id": project_id}


@router.post("/pipeline/{project_id}/restart", status_code=202)
async def restart(project_id: str, request: Request):
    """Admin-only: wipe per-project extractions and run the pipeline free."""
    from backend.routers.admin import _require_admin

    await _require_admin(request)
    storage = get_storage(request)
    owner_user_id, meta = await resolve_or_404(request, project_id)
    if not meta.has_bom or not meta.has_netlist:
        raise HTTPException(400, "Upload BOM and netlist before starting pipeline")

    # If something is currently running/queued, request cancel and wait
    # briefly for the worker to honour it (or exit on its own). Hard-kill
    # the execution as a last resort.
    if _project_active(meta):
        proj_svc.request_cancel(storage, owner_user_id, project_id)
        await _await_terminal(storage, owner_user_id, project_id, timeout_s=10.0)
        # If still active, hard-kill via Cloud Run cancel.
        meta = proj_svc.get_project(storage, owner_user_id, project_id) or meta
        if _project_active(meta) and meta.execution_name:
            job_runner.cancel_execution(meta.execution_name)
            await _await_terminal(storage, owner_user_id, project_id, timeout_s=5.0)

    proj_svc.clear_project_extractions(storage, owner_user_id, project_id)

    # After clear_project_extractions the project is left in whatever
    # status it was; the transition below enforces queued.
    try:
        proj_svc.transition_status(
            storage, owner_user_id, project_id,
            from_status=_START_OK_FROM | {proj_svc.STATUS_PAUSED},
            to_status=proj_svc.STATUS_QUEUED,
            cancel_requested=False,
            execution_name=None,
        )
    except proj_svc.StatusConflict:
        raise HTTPException(409, "Pipeline is busy; cancel first then retry")

    try:
        execution_name = job_runner.enqueue_pipeline(
            project_id, owner_user_id, resume=False, free=True,
        )
    except Exception:
        logger.exception("enqueue_pipeline (restart) failed for %s", project_id)
        proj_svc.update_project(
            storage, owner_user_id, project_id,
            status=proj_svc.STATUS_ERROR,
            pipeline_state={"error": "Failed to enqueue worker"},
        )
        raise HTTPException(503, "Failed to enqueue pipeline worker; please retry")

    proj_svc.update_project(
        storage, owner_user_id, project_id, execution_name=execution_name,
    )
    return {"status": "restarted", "project_id": project_id}


@router.post("/pipeline/{project_id}/regen", status_code=202)
async def regen(project_id: str, req: RegenRequest, request: Request):
    """Rebuild graph and regenerate only the requested stages."""
    storage = get_storage(request)
    owner_user_id, meta = await resolve_or_404(request, project_id)
    if not meta.has_bom or not meta.has_netlist:
        raise HTTPException(400, "Upload BOM and netlist before running regen")
    invalid = set(req.stages) - VALID_REGEN_STAGES
    if invalid:
        raise HTTPException(400, f"Invalid regen stages: {sorted(invalid)}. Valid: {sorted(VALID_REGEN_STAGES)}")
    if not req.stages:
        raise HTTPException(400, "At least one stage is required")

    if _project_active(meta):
        proj_svc.request_cancel(storage, owner_user_id, project_id)
        await _await_terminal(storage, owner_user_id, project_id, timeout_s=10.0)
        meta = proj_svc.get_project(storage, owner_user_id, project_id) or meta
        if _project_active(meta) and meta.execution_name:
            job_runner.cancel_execution(meta.execution_name)
            await _await_terminal(storage, owner_user_id, project_id, timeout_s=5.0)

    try:
        proj_svc.transition_status(
            storage, owner_user_id, project_id,
            from_status=_START_OK_FROM | {proj_svc.STATUS_PAUSED},
            to_status=proj_svc.STATUS_QUEUED,
            cancel_requested=False,
            execution_name=None,
        )
    except proj_svc.StatusConflict:
        raise HTTPException(409, "Pipeline is busy; cancel first then retry")

    try:
        execution_name = job_runner.enqueue_pipeline_regen(
            project_id, owner_user_id, stages=req.stages,
        )
    except Exception:
        logger.exception("enqueue_pipeline_regen failed for %s", project_id)
        proj_svc.update_project(
            storage, owner_user_id, project_id,
            status=proj_svc.STATUS_ERROR,
            pipeline_state={"error": "Failed to enqueue worker"},
        )
        raise HTTPException(503, "Failed to enqueue pipeline worker; please retry")

    proj_svc.update_project(
        storage, owner_user_id, project_id, execution_name=execution_name,
    )
    return {"status": "regen_started", "project_id": project_id, "stages": req.stages}


# ---------------------------------------------------------------------------
# SSE events
# ---------------------------------------------------------------------------


_EXEC_TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


@router.get("/pipeline/{project_id}/events")
async def events(project_id: str, request: Request):
    """SSE stream of pipeline progress events.

    Tails the GCS-backed event log written by the worker. Stops on
    terminal events as today, but also has two hard-crash escape
    hatches: the project's status reaching a terminal value, and the
    Cloud Run execution reaching a terminal state. Either of those
    triggers a synthetic ``pipeline_error`` so the SSE doesn't hang
    forever when the worker dies without writing its terminal event.
    """
    owner_user_id, meta = await resolve_or_404(request, project_id)
    storage = get_storage(request)

    async def event_generator():
        execution_name = meta.execution_name
        # Drive the GCS tail and the escape-hatch poll concurrently. The
        # tail yields events; the escape hatch flips a flag.
        crash_detected: dict[str, str | None] = {"reason": None}

        async def watch_status() -> None:
            poll_interval = 2.0
            while True:
                await asyncio.sleep(poll_interval)
                try:
                    cur = proj_svc.get_project(storage, owner_user_id, project_id)
                except Exception:
                    continue
                if cur is None:
                    continue
                if cur.status in proj_svc.TERMINAL_STATUSES:
                    crash_detected["reason"] = (
                        f"project status={cur.status} (terminal)"
                    )
                    return
                # Cloud Run hard-crash detection
                if execution_name:
                    try:
                        state = job_runner.get_execution_state(execution_name)
                    except Exception:
                        state = "unknown"
                    if state in _EXEC_TERMINAL:
                        crash_detected["reason"] = (
                            f"execution state={state}"
                        )
                        return

        watcher = asyncio.create_task(watch_status())
        try:
            async for msg in event_bridge.tail_events(
                storage, owner_user_id, project_id,
            ):
                if crash_detected["reason"] is not None:
                    break
                yield {
                    "event": msg["event"],
                    "data": json.dumps(msg.get("data", {})),
                }
                if msg["event"] in event_bridge.TERMINAL_EVENTS:
                    return

            # tail_events exited without a terminal event — escape hatch
            if crash_detected["reason"] is not None:
                # Re-read the current meta so the synthetic event has
                # the most up-to-date error information.
                cur = proj_svc.get_project(storage, owner_user_id, project_id)
                err = (
                    (cur.pipeline_state or {}).get("error")
                    if cur and cur.pipeline_state
                    else crash_detected["reason"]
                )
                yield {
                    "event": "pipeline_error",
                    "data": json.dumps({
                        "error": err or "worker terminated without writing a terminal event",
                        "synthetic": True,
                    }),
                }
        finally:
            watcher.cancel()
            try:
                await watcher
            except (asyncio.CancelledError, Exception):
                pass

    return EventSourceResponse(event_generator())


@router.get("/pipeline/{project_id}/status")
async def status(project_id: str, request: Request):
    """Polling fallback — returns current project state."""
    _, meta = await resolve_or_404(request, project_id)
    return {
        "status": meta.status,
        "summary": meta.summary,
        "pipeline_state": meta.pipeline_state,
        "running": meta.status in (proj_svc.STATUS_RUNNING, proj_svc.STATUS_QUEUED),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _await_terminal(
    storage, user_id: str, project_id: str, *, timeout_s: float,
) -> None:
    """Poll project status until it reaches a terminal state or the timeout
    elapses. Used by /restart and /regen between cancel and re-enqueue.
    """
    poll = 0.5
    elapsed = 0.0
    while elapsed < timeout_s:
        try:
            meta = proj_svc.get_project(storage, user_id, project_id)
        except Exception:
            meta = None
        if meta is None:
            return
        if meta.status in proj_svc.TERMINAL_STATUSES:
            return
        await asyncio.sleep(poll)
        elapsed += poll
