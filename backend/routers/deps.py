"""Shared dependencies for FastAPI routers."""

from __future__ import annotations

from fastapi import HTTPException, Request

from backend.services import projects as proj_svc
from backend.services.storage import StorageBackend


def get_storage(request: Request) -> StorageBackend:
    return request.app.state.storage


def get_user_id(request: Request) -> str:
    return request.state.user_id


async def resolve_or_404(request: Request, project_id: str) -> tuple[str, proj_svc.ProjectMeta]:
    """Resolve project access (owner, collaborator, or admin) or raise 404."""
    storage = get_storage(request)
    user_id = get_user_id(request)

    # 1. Try normal access — cheap, no external API call
    result = proj_svc.resolve_project_access(storage, user_id, project_id)
    if result:
        return result

    # 2. Admin fallback — Clerk API call only when normal access fails
    from backend.routers.admin import is_admin

    if await is_admin(request):
        result = proj_svc.find_project_any_user(storage, project_id)
        if result:
            return result

    raise HTTPException(404, "Project not found")
