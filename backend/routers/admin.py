"""Admin endpoints — library components, user management, and limits.

All endpoints require the requesting user to have role: "admin" in their
Clerk public metadata. In local dev (no auth), all requests are treated as admin.
"""

from __future__ import annotations

import asyncio
import re

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.config import settings
from backend.pinscopex.utils import safe_mpn
from backend.routers.deps import get_storage
from backend.services import admin_settings as settings_svc
from backend.services.billing_hook import get_billing
from backend.services import projects as proj_svc

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Admin verification
# ---------------------------------------------------------------------------

async def is_admin(request: Request) -> bool:
    """Check if the caller is an admin. Result is cached on request.state."""
    cached = getattr(request.state, "_is_admin", None)
    if cached is not None:
        return cached

    user_id: str = request.state.user_id

    # Local dev — no auth, treat as admin
    if not settings.use_auth:
        request.state._is_admin = True
        return True

    # Fetch user from Clerk Backend API and check public_metadata.role
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://api.clerk.com/v1/users/{user_id}",
                headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
            )
        if resp.status_code == 200:
            data = resp.json()
            role = data.get("public_metadata", {}).get("role")
            result = role == "admin"
        else:
            result = False
    except Exception:
        result = False

    request.state._is_admin = result
    return result


async def _require_admin(request: Request) -> str:
    """Return user_id if the caller is an admin, else raise 403."""
    if not await is_admin(request):
        raise HTTPException(403, "Admin access required")
    return request.state.user_id


# ---------------------------------------------------------------------------
# Library components
# ---------------------------------------------------------------------------

@router.get("/components")
async def list_components(request: Request):
    """List all extracted IC components and passive patterns in the library."""
    await _require_admin(request)
    storage = get_storage(request)

    # IC extractions (deduplicate by MPN)
    ic_keys = [
        k for k in storage.list_prefix("library/extracted/")
        if k.endswith(".json")
    ]
    ics = []
    seen_ic_mpns: set[str] = set()
    for key in ic_keys:
        try:
            data = storage.read_json(key)
            mpn = data.get("mpn") or key.rsplit("/", 1)[-1].replace(".json", "")
            if mpn in seen_ic_mpns:
                continue
            seen_ic_mpns.add(mpn)
            ics.append({
                "mpn": mpn,
                "type": "ic",
                "subtype": data.get("component_subtype", ""),
                "pin_count": len(data.get("pintable", [])),
                "has_ratings": bool(data.get("absolute_maximum_ratings")),
            })
        except Exception:
            continue

    # Passive patterns
    pattern_keys = [
        k for k in storage.list_prefix("library/patterns/")
        if k.endswith(".json")
    ]
    passives = []
    seen_passive_names: set[str] = set()
    for key in pattern_keys:
        try:
            data = storage.read_json(key)
            name = data.get("name") or key.rsplit("/", 1)[-1].replace(".json", "")
            if name in seen_passive_names:
                continue
            seen_passive_names.add(name)
            passives.append({
                "mpn": name,
                "type": "passive",
                "subtype": data.get("component_type", ""),
                "description": data.get("description", ""),
                "regex": data.get("regex", ""),
            })
        except Exception:
            continue

    # Simple component models (library/models/) + passive models (library/passives/)
    model_keys = [
        k for k in storage.list_prefix("library/models/")
        if k.endswith(".json")
    ]
    passive_model_keys = [
        k for k in storage.list_prefix("library/passives/")
        if k.endswith(".json")
    ]
    simple_models = []
    seen_model_mpns: set[str] = set()
    for key in model_keys + passive_model_keys:
        try:
            data = storage.read_json(key)
            mpn = data.get("mpn", "")
            if mpn in seen_model_mpns:
                continue
            seen_model_mpns.add(mpn)
            specs = data.get("specs", {})
            simple_models.append({
                "mpn": mpn,
                "type": "simple",
                "specs_type": specs.get("specs_type", ""),
                "subtype": specs.get("component_subtype", ""),
                "param_count": len(specs.get("values", {})),
            })
        except Exception:
            continue

    return JSONResponse(
        content={"ics": ics, "passives": passives, "simple": simple_models},
        headers={"Cache-Control": "no-store"},
    )


def _safe_name(name: str) -> str:
    """Sanitize MPN to safe filename (same logic as pipeline)."""
    safe = safe_mpn(name)
    if ".." in safe or not re.match(r"^[A-Za-z0-9]", safe):
        raise HTTPException(400, "Invalid component name")
    return safe


@router.get("/components/{component_type}/{name:path}")
async def get_component(component_type: str, name: str, request: Request):
    """Return the raw JSON for an IC extraction or passive pattern."""
    await _require_admin(request)
    safe = _safe_name(name)
    storage = get_storage(request)

    if component_type == "ic":
        key = f"library/extracted/{safe}.json"
    elif component_type == "passive":
        key = f"library/patterns/{safe}.json"
    elif component_type == "simple":
        # Check library/passives/ first, then library/models/
        key = f"library/passives/{safe}.json"
        if not storage.exists(key):
            key = f"library/models/{safe}.json"
    else:
        raise HTTPException(400, f"Unknown component type: {component_type}")

    if not storage.exists(key):
        raise HTTPException(404, f"Component not found: {name}")

    return JSONResponse(content=storage.read_json(key))


@router.delete("/components/{component_type}/{name:path}")
async def delete_component(component_type: str, name: str, request: Request):
    """Delete an IC extraction or passive pattern from the shared library."""
    await _require_admin(request)
    safe = _safe_name(name)
    storage = get_storage(request)

    if component_type == "ic":
        key = f"library/extracted/{safe}.json"
    elif component_type == "passive":
        key = f"library/patterns/{safe}.json"
    elif component_type == "simple":
        # Check library/passives/ first, then library/models/
        key = f"library/passives/{safe}.json"
        if not storage.exists(key):
            key = f"library/models/{safe}.json"
    else:
        raise HTTPException(400, f"Unknown component type: {component_type}")

    if not storage.exists(key):
        raise HTTPException(404, f"Component not found: {name}")

    storage.delete_key(key)

    # Delete datasheet ref (blob preserved for other refs; GC cleans orphans)
    from backend.services.datasheet_store import delete_datasheet_ref

    deleted_datasheets = 0
    old_blob = delete_datasheet_ref(storage, name)
    if old_blob:
        deleted_datasheets += 1
    # Legacy flat file cleanup (remove after migration confirmed)
    ds_key = f"library/datasheets/{safe}.pdf"
    if storage.exists(ds_key):
        storage.delete_key(ds_key)
        deleted_datasheets += 1

    return {"deleted": key, "deleted_datasheets": deleted_datasheets}


def _clerk_profile_fields(clerk: dict) -> dict:
    """Pull display name / email / avatar out of a Clerk user object."""
    first = clerk.get("first_name") or ""
    last = clerk.get("last_name") or ""
    emails = clerk.get("email_addresses", [])
    return {
        "name": f"{first} {last}".strip() or None,
        "email": emails[0].get("email_address") if emails else None,
        "image_url": clerk.get("image_url"),
    }


def _base_admin_user(storage, uid: str) -> dict:
    """Build the project-count + balance record for a single user_id."""
    try:
        project_count = len(proj_svc.list_projects(storage, uid))
    except Exception:
        project_count = 0
    try:
        balance = get_billing().get_balance(storage, uid)
    except Exception:
        balance = 0.0
    return {
        "user_id": uid,
        "project_count": project_count,
        "balance": round(balance, 4),
        "name": None,
        "email": None,
        "image_url": None,
    }


async def _enrich_clerk_profiles(users: dict[str, dict]) -> None:
    """Fill name/email/avatar for each user via the Clerk Backend API.

    Fetches in parallel (bounded) so the list stays fast even with many
    users.  Failures per-user are swallowed — the row still renders with
    the user_id as a fallback label.
    """
    sem = asyncio.Semaphore(10)

    async with httpx.AsyncClient(timeout=10.0) as client:
        async def _one(uid: str) -> None:
            async with sem:
                try:
                    resp = await client.get(
                        f"https://api.clerk.com/v1/users/{uid}",
                        headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
                    )
                    if resp.status_code == 200:
                        users[uid].update(_clerk_profile_fields(resp.json()))
                except Exception:
                    pass

        await asyncio.gather(*(_one(uid) for uid in users))


@router.get("/users")
async def list_users(request: Request):
    """List every user with a project or any credit activity.

    The balance file is written on a user's first ``GET /api/credits``
    (trial grant), so this includes everyone who has ever opened the
    authenticated app — not only project creators.  To find a user who has
    never opened the app, use ``GET /api/admin/users/search?email=``.
    """
    await _require_admin(request)
    storage = get_storage(request)

    user_ids: set[str] = set()

    # Project creators (users/{user_id}/...)
    for entry in storage.list_prefix("users/"):
        parts = entry.split("/")
        if len(parts) >= 2 and parts[1]:
            user_ids.add(parts[1])

    # Anyone with credit activity (covers the trial grant on first app open)
    user_ids.update(get_billing().list_user_ids(storage))

    users: dict[str, dict] = {uid: _base_admin_user(storage, uid) for uid in user_ids}

    # Enrich with Clerk user info when auth is enabled
    if settings.use_auth and users:
        await _enrich_clerk_profiles(users)

    return list(users.values())


@router.get("/users/search")
async def search_users(request: Request, email: str):
    """Find users by email via Clerk so any account can be topped up (admin).

    Resolves even users with no project and no credit activity yet — useful
    for granting credits to someone who has just signed up.  Requires auth
    to be enabled (no Clerk directory exists in local dev).
    """
    await _require_admin(request)
    storage = get_storage(request)

    email = email.strip()
    if not email:
        return []
    if not settings.use_auth:
        raise HTTPException(400, "User search requires authentication to be enabled")

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(
                "https://api.clerk.com/v1/users",
                params={"email_address": [email]},
                headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
            )
        except Exception as exc:
            raise HTTPException(502, "Failed to look up user") from exc

    if resp.status_code != 200:
        raise HTTPException(502, "Failed to look up user")

    results: list[dict] = []
    for clerk in resp.json():
        uid = clerk.get("id")
        if not uid:
            continue
        entry = _base_admin_user(storage, uid)
        entry.update(_clerk_profile_fields(clerk))
        results.append(entry)

    return results


# ---------------------------------------------------------------------------
# Usage / cost tracking
# ---------------------------------------------------------------------------

@router.get("/usage")
async def get_usage(request: Request):
    """Aggregate API token usage and cost across all users and projects."""
    await _require_admin(request)
    storage = get_storage(request)

    user_entries = storage.list_prefix("users/")
    seen_uids: set[str] = set()
    user_rows: list[dict] = []
    grand_total = 0.0

    for entry in user_entries:
        parts = entry.split("/")
        if len(parts) >= 2:
            uid = parts[1]
            if uid in seen_uids:
                continue
            seen_uids.add(uid)

            projects = proj_svc.list_projects(storage, uid)
            user_cost = 0.0
            project_details = []
            for p in projects:
                cost = p.total_cost_usd or 0.0
                user_cost += cost
                project_details.append({
                    "id": p.id,
                    "name": p.name,
                    "status": p.status,
                    "cost_usd": cost,
                    "created": p.created,
                })

            user_rows.append({
                "user_id": uid,
                "project_count": len(projects),
                "total_cost_usd": round(user_cost, 4),
                "projects": project_details,
                "name": None,
                "email": None,
            })
            grand_total += user_cost

    # Enrich with Clerk user info
    if settings.use_auth and user_rows:
        async with httpx.AsyncClient() as client:
            for row in user_rows:
                try:
                    resp = await client.get(
                        f"https://api.clerk.com/v1/users/{row['user_id']}",
                        headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
                    )
                    if resp.status_code == 200:
                        clerk = resp.json()
                        first = clerk.get("first_name") or ""
                        last = clerk.get("last_name") or ""
                        row["name"] = f"{first} {last}".strip() or None
                        emails = clerk.get("email_addresses", [])
                        row["email"] = emails[0].get("email_address") if emails else None
                except Exception:
                    pass

    return {
        "grand_total_usd": round(grand_total, 4),
        "users": user_rows,
    }


# ---------------------------------------------------------------------------
# All projects (cross-user)
# ---------------------------------------------------------------------------

async def _enrich_with_clerk_info(
    items: list[dict], uid_key: str = "user_id",
    name_key: str = "owner_name", email_key: str = "owner_email",
) -> None:
    """Enrich a list of dicts with Clerk user info, deduplicating API calls."""
    if not settings.use_auth or not items:
        return
    cache: dict[str, dict] = {}
    async with httpx.AsyncClient() as client:
        for item in items:
            uid = item[uid_key]
            if uid not in cache:
                try:
                    resp = await client.get(
                        f"https://api.clerk.com/v1/users/{uid}",
                        headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
                    )
                    if resp.status_code == 200:
                        clerk = resp.json()
                        first = clerk.get("first_name") or ""
                        last = clerk.get("last_name") or ""
                        emails = clerk.get("email_addresses", [])
                        cache[uid] = {
                            name_key: f"{first} {last}".strip() or None,
                            email_key: emails[0].get("email_address") if emails else None,
                        }
                    else:
                        cache[uid] = {name_key: None, email_key: None}
                except Exception:
                    cache[uid] = {name_key: None, email_key: None}
            item.update(cache[uid])


@router.get("/projects")
async def list_all_projects(request: Request):
    """List all projects across all users with metadata."""
    await _require_admin(request)
    storage = get_storage(request)

    user_entries = storage.list_prefix("users/")
    seen_uids: set[str] = set()
    all_projects: list[dict] = []

    for entry in user_entries:
        parts = entry.split("/")
        if len(parts) >= 2:
            uid = parts[1]
            if uid in seen_uids:
                continue
            seen_uids.add(uid)
            projects = proj_svc.list_projects(storage, uid)
            for p in projects:
                all_projects.append({
                    "id": p.id,
                    "name": p.name,
                    "user_id": p.user_id,
                    "status": p.status,
                    "created": p.created,
                    "updated": p.updated,
                    "has_bom": p.has_bom,
                    "has_netlist": p.has_netlist,
                    "datasheet_count": p.datasheet_count,
                    "total_cost_usd": p.total_cost_usd,
                    "pipeline_state": p.pipeline_state,
                    "summary": p.summary,
                    "owner_name": None,
                    "owner_email": None,
                })

    await _enrich_with_clerk_info(all_projects)
    return all_projects


# ---------------------------------------------------------------------------
# Running pipelines
# ---------------------------------------------------------------------------

@router.get("/runs")
async def list_running_pipelines(request: Request):
    """List queued and running pipelines, plus drive the stale-running sweeper.

    Source of truth is ``project.json`` (``status`` ∈ {queued, running}); we
    cross-check with the Cloud Run Job execution. Any project whose
    execution is in a terminal Cloud Run state but whose status is still
    queued/running is flipped to ``error`` here — this is the sweeper that
    keeps zombie projects from showing "running" forever in the UI.
    """
    from datetime import datetime, timezone

    from backend.services import job_runner

    await _require_admin(request)
    storage = get_storage(request)

    now = datetime.now(timezone.utc)
    runs: list[dict] = []
    seen_uids: set[str] = set()

    for entry in storage.list_prefix("users/"):
        parts = entry.split("/")
        if len(parts) < 2:
            continue
        uid = parts[1]
        if uid in seen_uids:
            continue
        seen_uids.add(uid)
        prefix = f"users/{uid}/projects/"
        for proj_entry in storage.list_prefix(prefix):
            meta_key = (
                proj_entry if proj_entry.endswith("/project.json")
                else f"{proj_entry}/project.json"
            )
            if not storage.exists(meta_key):
                continue
            try:
                meta = proj_svc.ProjectMeta.model_validate(storage.read_json(meta_key))
            except Exception:
                continue
            if meta.status not in (proj_svc.STATUS_QUEUED, proj_svc.STATUS_RUNNING):
                continue

            # Sweeper: if the execution is in a terminal Cloud Run state,
            # the worker is already gone. Flip status → error so the UI
            # stops lying. Skip the sweep when execution_name is missing
            # (worker may still be enqueueing).
            exec_state = "unknown"
            if meta.execution_name:
                exec_state = job_runner.get_execution_state(meta.execution_name)
            if exec_state in ("succeeded", "failed", "cancelled"):
                # Allow a short grace period so we don't race the worker
                # writing its own terminal status. updated may be stale
                # if the worker died before any status write.
                try:
                    last_update = datetime.fromisoformat(meta.updated)
                    age = (now - last_update).total_seconds()
                except Exception:
                    age = settings.pipeline_sweeper_stale_seconds + 1
                if age >= settings.pipeline_sweeper_stale_seconds:
                    proj_svc.mark_stale_running(
                        storage, uid, meta.id,
                        f"Worker terminated (execution state={exec_state}); please restart.",
                    )
                    continue

            try:
                started_at = datetime.fromisoformat(meta.updated)
            except Exception:
                started_at = now
            runs.append({
                "project_id": meta.id,
                "project_name": meta.name,
                "user_id": uid,
                "status": meta.status,
                "execution_name": meta.execution_name,
                "execution_state": exec_state,
                "started_at": started_at.isoformat(),
                "duration_seconds": int((now - started_at).total_seconds()),
                "owner_name": None,
                "owner_email": None,
            })

    await _enrich_with_clerk_info(runs)
    return runs


# ---------------------------------------------------------------------------
# Global settings
# ---------------------------------------------------------------------------

class UpdateMinVersionRequest(BaseModel):
    min_model_version: str


@router.get("/settings")
async def get_settings(request: Request):
    """Get global admin settings (model version threshold, etc.)."""
    await _require_admin(request)
    storage = get_storage(request)
    data = settings_svc.get_admin_settings(storage)
    data["default_model_version"] = settings.get_default_model_version()
    return data


@router.put("/settings/min-model-version")
async def set_min_model_version(req: UpdateMinVersionRequest, request: Request):
    """Set the minimum model version for library reuse."""
    await _require_admin(request)
    storage = get_storage(request)
    try:
        settings_svc.set_min_model_version(storage, req.min_model_version)
    except Exception as e:
        raise HTTPException(400, f"Invalid version: {e}")
    return {"min_model_version": req.min_model_version}


# ---------------------------------------------------------------------------
# Email test
# ---------------------------------------------------------------------------

class TestEmailRequest(BaseModel):
    to_email: str


@router.post("/test-email")
async def test_email(req: TestEmailRequest, request: Request):
    """Send a test email to verify Gmail API setup. Admin only."""
    await _require_admin(request)
    from backend.services.email import send_test_email
    result = await send_test_email(req.to_email)
    return result


# ---------------------------------------------------------------------------
# Project state overrides
# ---------------------------------------------------------------------------

@router.post("/projects/{project_id}/mark-complete")
async def mark_project_complete(project_id: str, request: Request):
    """Admin-only: force a paused project to ``complete`` status.

    Intended for projects stuck at ``paused_insufficient_credits`` that the
    admin has decided to finalize rather than resume. Clears the pause
    checkpoint/reason; does not touch credits, cost totals, or artifacts.
    """
    from backend.routers.deps import resolve_or_404
    from backend.services import projects as proj_svc

    await _require_admin(request)
    storage = get_storage(request)
    owner_user_id, meta = await resolve_or_404(request, project_id)

    if meta.status in (proj_svc.STATUS_RUNNING, proj_svc.STATUS_QUEUED):
        raise HTTPException(409, "Cannot mark a running pipeline complete; cancel it first")
    if meta.status == "complete":
        return {"status": "complete", "project_id": project_id}

    proj_svc.update_project(
        storage,
        owner_user_id,
        project_id,
        status="complete",
        pause_checkpoint=None,
        pause_reason=None,
    )
    return {"status": "complete", "project_id": project_id}


# ---------------------------------------------------------------------------
# Report overrides
# ---------------------------------------------------------------------------

@router.delete("/projects/{project_id}/findings/{finding_id}")
async def delete_finding(project_id: str, finding_id: str, request: Request):
    """Admin-only: delete a single finding (rule violation) from a report.

    Rewrites ``report.json`` without the matching finding, recomputes summary
    counts, and mirrors the summary onto ``ProjectMeta`` so dashboard totals
    stay consistent. Returns 404 if the project, report, or finding is missing.
    """
    from backend.routers.deps import resolve_or_404

    await _require_admin(request)
    storage = get_storage(request)
    owner_user_id, _ = await resolve_or_404(request, project_id)

    key = f"{proj_svc.project_prefix(owner_user_id, project_id)}/report.json"
    if not storage.exists(key):
        raise HTTPException(404, "Report not found")

    report = storage.read_json(key)
    findings = report.get("findings", []) or []
    remaining = [f for f in findings if f.get("finding_id") != finding_id]
    if len(remaining) == len(findings):
        raise HTTPException(404, f"Finding not found: {finding_id}")

    summary = {"total": len(remaining), "ERROR": 0, "WARNING": 0, "INFO": 0}
    for f in remaining:
        status = f.get("status")
        if status in summary:
            summary[status] += 1

    report["findings"] = remaining
    report["summary"] = summary
    storage.write_json(key, report)

    proj_svc.update_project(storage, owner_user_id, project_id, summary=summary)

    return {
        "deleted": finding_id,
        "project_id": project_id,
        "remaining": len(remaining),
        "summary": summary,
    }
