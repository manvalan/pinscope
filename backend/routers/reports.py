"""Report, graph, datasheet, and API log serving endpoints."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.pinscopex.utils import safe_mpn
from backend.routers.deps import get_storage, get_user_id, resolve_or_404
from backend.services import projects as proj_svc

router = APIRouter(tags=["reports"])

# Allow alphanumeric, dash, underscore, dot, colon, forward-slash, plus, hash, space
_SAFE_MPN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-\.:/ +#,()]*$")


def _validate_mpn(mpn: str) -> None:
    """Reject MPN values that could cause path traversal."""
    if not _SAFE_MPN.match(mpn) or ".." in mpn:
        raise HTTPException(400, "Invalid MPN format")


@router.get("/report/{project_id}")
async def get_report(project_id: str, request: Request):
    storage = get_storage(request)
    owner_user_id, _ = await resolve_or_404(request, project_id)
    prefix = proj_svc.project_prefix(owner_user_id, project_id)
    key = f"{prefix}/report.json"
    if not storage.exists(key):
        raise HTTPException(404, "Report not found — run the pipeline first")
    return JSONResponse(storage.read_json(key))


class AddCommentBody(BaseModel):
    finding_id: str
    text: str
    user_name: str
    mentions: list[str] = []


@router.post("/report/{project_id}/comments")
async def add_comment(project_id: str, body: AddCommentBody, request: Request):
    storage = get_storage(request)
    owner_user_id, _ = await resolve_or_404(request, project_id)
    user_id = get_user_id(request)
    prefix = proj_svc.project_prefix(owner_user_id, project_id)
    key = f"{prefix}/report.json"
    if not storage.exists(key):
        raise HTTPException(404, "Report not found")
    report_data = storage.read_json(key)
    comment = {
        "comment_id": str(uuid.uuid4()),
        "finding_id": body.finding_id,
        "user_id": user_id,
        "user_name": body.user_name,
        "text": body.text,
        "mentions": body.mentions,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    comments = report_data.setdefault("comments", {})
    comments.setdefault(body.finding_id, []).append(comment)
    storage.write_json(key, report_data)
    return JSONResponse(comment, status_code=201)


@router.delete("/report/{project_id}/comments/{comment_id}")
async def delete_comment(project_id: str, comment_id: str, request: Request):
    storage = get_storage(request)
    owner_user_id, meta = await resolve_or_404(request, project_id)
    user_id = get_user_id(request)
    prefix = proj_svc.project_prefix(owner_user_id, project_id)
    key = f"{prefix}/report.json"
    if not storage.exists(key):
        raise HTTPException(404, "Report not found")
    report_data = storage.read_json(key)
    comments = report_data.get("comments", {})
    for finding_id, comment_list in comments.items():
        for i, c in enumerate(comment_list):
            if c["comment_id"] == comment_id:
                if c["user_id"] != user_id and user_id != owner_user_id:
                    raise HTTPException(403, "Cannot delete another user's comment")
                comment_list.pop(i)
                if not comment_list:
                    del comments[finding_id]
                storage.write_json(key, report_data)
                return JSONResponse({"ok": True})
    raise HTTPException(404, "Comment not found")


@router.get("/bom/{project_id}")
async def get_bom_summary(project_id: str, request: Request):
    storage = get_storage(request)
    owner_user_id, _ = await resolve_or_404(request, project_id)
    prefix = proj_svc.project_prefix(owner_user_id, project_id)
    key = f"{prefix}/bom_summary.json"
    if not storage.exists(key):
        raise HTTPException(404, "BOM summary not found — run the pipeline first")
    return JSONResponse(storage.read_json(key))


@router.get("/derating/{project_id}")
async def get_derating(project_id: str, request: Request):
    storage = get_storage(request)
    owner_user_id, _ = await resolve_or_404(request, project_id)
    prefix = proj_svc.project_prefix(owner_user_id, project_id)
    key = f"{prefix}/derating.json"
    if not storage.exists(key):
        raise HTTPException(404, "Derating data not found — run the pipeline first")
    return JSONResponse(storage.read_json(key))


@router.get("/graph/{project_id}")
async def get_graph(project_id: str, request: Request):
    storage = get_storage(request)
    owner_user_id, _ = await resolve_or_404(request, project_id)
    prefix = proj_svc.project_prefix(owner_user_id, project_id)
    key = f"{prefix}/design_graph.json"
    if not storage.exists(key):
        raise HTTPException(404, "Design graph not found — run the pipeline first")
    return JSONResponse(storage.read_json(key))


@router.get("/projects/{project_id}/logs")
async def get_project_logs(project_id: str, request: Request):
    """Return API call logs for a project pipeline run."""
    storage = get_storage(request)
    owner_user_id, _ = await resolve_or_404(request, project_id)
    prefix = proj_svc.project_prefix(owner_user_id, project_id)
    key = f"{prefix}/api_logs.jsonl"
    if not storage.exists(key):
        return JSONResponse([])
    text = storage.read_text(key)
    entries = [json.loads(line) for line in text.strip().split("\n") if line.strip()]
    return JSONResponse(entries)


def _find_datasheet_key(
    storage, owner_user_id: str, project_id: str, safe: str,
    mpn: str | None = None,
) -> str | None:
    """Return the storage key for a datasheet PDF, or None."""
    from backend.services.datasheet_store import resolve_datasheet

    # 1. Project uploads
    key = f"{proj_svc.project_prefix(owner_user_id, project_id)}/uploads/datasheets/{safe}.pdf"
    if storage.exists(key):
        return key
    # 2. Content-addressed ref lookup
    resolved = resolve_datasheet(storage, safe)
    if resolved:
        return resolved
    # 3. Legacy flat file fallback (remove after migration confirmed)
    key = f"library/datasheets/{safe}.pdf"
    if storage.exists(key):
        return key
    # 4. Pattern-based fallback (passives with shared datasheets)
    if mpn:
        return proj_svc.library_has_datasheet(storage, mpn)
    return None


@router.get("/projects/{project_id}/datasheet-url/{mpn:path}")
async def get_datasheet_url(project_id: str, mpn: str, request: Request):
    """Return a URL for accessing a datasheet PDF.

    Returns a backend proxy URL that streams the PDF through Cloud Run.
    This avoids GCS signed-URL issues (IAM signBlob scope problems) and
    works identically for local and cloud storage.
    """
    storage = get_storage(request)
    owner_user_id, _ = await resolve_or_404(request, project_id)
    _validate_mpn(mpn)
    safe = safe_mpn(mpn)

    key = _find_datasheet_key(storage, owner_user_id, project_id, safe, mpn=mpn)
    if key is None:
        raise HTTPException(404, f"Datasheet not found for MPN: {mpn}")

    # Return a proxy URL that points back to this backend
    proxy_path = f"/api/projects/{project_id}/datasheet/{mpn}"
    base = str(request.base_url).rstrip("/")
    return {"url": f"{base}{proxy_path}"}


@router.get("/projects/{project_id}/datasheet/{mpn:path}")
async def get_datasheet_proxy(project_id: str, mpn: str, request: Request):
    """Stream a datasheet PDF from storage (GCS or local).

    This is the proxy endpoint returned by get_datasheet_url.
    """
    from fastapi.responses import Response

    storage = get_storage(request)
    owner_user_id, _ = await resolve_or_404(request, project_id)
    _validate_mpn(mpn)
    safe = safe_mpn(mpn)

    key = _find_datasheet_key(storage, owner_user_id, project_id, safe, mpn=mpn)
    if key is None:
        raise HTTPException(404, f"Datasheet not found for MPN: {mpn}")

    data = storage.read_bytes(key)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{safe}.pdf"'},
    )


@router.get("/datasheets/{mpn}")
async def get_datasheet(mpn: str, request: Request):
    """Serve a datasheet PDF (legacy local-dev endpoint)."""
    from fastapi.responses import FileResponse

    from backend.services.storage import LocalStorageBackend

    storage = get_storage(request)
    user_id = get_user_id(request)
    _validate_mpn(mpn)
    safe = safe_mpn(mpn)

    if not isinstance(storage, LocalStorageBackend):
        raise HTTPException(
            400,
            "Use GET /projects/{project_id}/datasheet-url/{mpn} for cloud storage",
        )

    user_prefix = f"users/{user_id}/projects/"
    for entry in storage.list_prefix(user_prefix):
        pdf_key = f"{entry}/uploads/datasheets/{safe}.pdf"
        if storage.exists(pdf_key):
            return FileResponse(
                storage._path(pdf_key),
                media_type="application/pdf",
                filename=f"{safe}.pdf",
            )

    raise HTTPException(404, f"Datasheet not found for MPN: {mpn}")
