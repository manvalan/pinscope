"""User feedback / ticket system.

Users can submit feedback tickets (bugs, rule reports, feature requests).
Tickets are stored as individual JSON files with JSONL indexes for fast listing.

Storage layout:
  admin/feedback/tickets/{ticket_id}.json
  admin/feedback/index/by_user/{user_id}.jsonl
  admin/feedback/index/by_project/{project_id}.jsonl
  admin/feedback/index/all.jsonl
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.routers.deps import get_storage, get_user_id
from backend.services.storage import StorageBackend

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Storage key helpers
# ---------------------------------------------------------------------------

_TICKETS_PREFIX = "admin/feedback/tickets/"
_INDEX_BY_USER = "admin/feedback/index/by_user/"
_INDEX_BY_PROJECT = "admin/feedback/index/by_project/"
_INDEX_ALL = "admin/feedback/index/all.jsonl"


def _ticket_key(ticket_id: str) -> str:
    return f"{_TICKETS_PREFIX}{ticket_id}.json"


def _user_index_key(user_id: str) -> str:
    return f"{_INDEX_BY_USER}{user_id}.jsonl"


def _project_index_key(project_id: str) -> str:
    return f"{_INDEX_BY_PROJECT}{project_id}.jsonl"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

FeedbackType = Literal["bug", "rule_feedback", "feature_request"]
FeedbackStatus = Literal["open", "acknowledged", "resolved"]


class FeedbackTicket(BaseModel):
    ticket_id: str
    user_id: str
    user_name: str | None = None
    user_email: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    type: FeedbackType
    status: FeedbackStatus = "open"
    finding_id: str | None = None
    finding_text: str | None = None
    finding_designator: str | None = None
    finding_mpn: str | None = None
    finding_status: str | None = None
    message: str
    admin_notes: str | None = None
    created_at: str
    updated_at: str


class CreateFeedbackRequest(BaseModel):
    type: FeedbackType
    message: str = Field(..., min_length=1, max_length=5000)
    project_id: str | None = None
    project_name: str | None = None
    user_name: str | None = None
    user_email: str | None = None
    finding_id: str | None = None
    finding_text: str | None = None
    finding_designator: str | None = None
    finding_mpn: str | None = None
    finding_status: str | None = None


class UpdateFeedbackRequest(BaseModel):
    status: FeedbackStatus | None = None
    admin_notes: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _append_index(storage: StorageBackend, key: str, entry: dict) -> None:
    existing = ""
    if storage.exists(key):
        existing = storage.read_text(key)
    line = json.dumps(entry) + "\n"
    storage.write_text(key, existing + line)


def _read_index(storage: StorageBackend, key: str) -> list[dict]:
    if not storage.exists(key):
        return []
    text = storage.read_text(key)
    entries: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _read_ticket(storage: StorageBackend, ticket_id: str) -> FeedbackTicket | None:
    key = _ticket_key(ticket_id)
    if not storage.exists(key):
        return None
    try:
        data = storage.read_json(key)
        return FeedbackTicket(**data)
    except Exception:
        logger.warning("Failed to read ticket %s", ticket_id)
        return None


def _read_tickets_from_index(
    storage: StorageBackend,
    index_key: str,
    *,
    status: str | None = None,
    ticket_type: str | None = None,
    project_id: str | None = None,
) -> list[FeedbackTicket]:
    index_entries = _read_index(storage, index_key)
    tickets: list[FeedbackTicket] = []
    for entry in reversed(index_entries):
        tid = entry.get("ticket_id")
        if not tid:
            continue
        ticket = _read_ticket(storage, tid)
        if not ticket:
            continue
        if status and ticket.status != status:
            continue
        if ticket_type and ticket.type != ticket_type:
            continue
        if project_id and ticket.project_id != project_id:
            continue
        tickets.append(ticket)
    return tickets


# ---------------------------------------------------------------------------
# User endpoints
# ---------------------------------------------------------------------------


@router.post("/feedback", response_model=FeedbackTicket)
async def create_feedback(body: CreateFeedbackRequest, request: Request):
    storage = get_storage(request)
    user_id = get_user_id(request)
    now = datetime.now(timezone.utc).isoformat()
    ticket_id = uuid.uuid4().hex[:12]

    ticket = FeedbackTicket(
        ticket_id=ticket_id,
        user_id=user_id,
        user_name=body.user_name,
        user_email=body.user_email,
        project_id=body.project_id,
        project_name=body.project_name,
        type=body.type,
        status="open",
        finding_id=body.finding_id,
        finding_text=body.finding_text,
        finding_designator=body.finding_designator,
        finding_mpn=body.finding_mpn,
        finding_status=body.finding_status,
        message=body.message,
        admin_notes=None,
        created_at=now,
        updated_at=now,
    )

    storage.write_json(_ticket_key(ticket_id), ticket.model_dump())

    index_entry = {"ticket_id": ticket_id, "created_at": now}
    _append_index(storage, _user_index_key(user_id), index_entry)
    _append_index(storage, _INDEX_ALL, index_entry)
    if body.project_id:
        _append_index(storage, _project_index_key(body.project_id), index_entry)

    # Notify the admin inbox (fire-and-forget, identical pattern to
    # pipeline-started). Errors are swallowed inside the email service.
    try:
        from backend.services.email import send_feedback_received_email
        await send_feedback_received_email(
            ticket_id=ticket_id,
            user_id=user_id,
            feedback_type=body.type,
            message=body.message,
            submitter_name=body.user_name,
            submitter_email=body.user_email,
            project_name=body.project_name,
            project_id=body.project_id,
            finding_designator=body.finding_designator,
            finding_mpn=body.finding_mpn,
            finding_status=body.finding_status,
            finding_text=body.finding_text,
        )
    except Exception:
        logger.exception("Failed to enqueue feedback-received email for %s", ticket_id)

    return ticket


@router.get("/feedback", response_model=list[FeedbackTicket])
async def list_my_feedback(request: Request, status: str | None = None):
    storage = get_storage(request)
    user_id = get_user_id(request)
    return _read_tickets_from_index(
        storage, _user_index_key(user_id), status=status,
    )


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------


@router.get("/admin/feedback", response_model=list[FeedbackTicket])
async def list_all_feedback(
    request: Request,
    status: str | None = None,
    type: str | None = None,
    project_id: str | None = None,
):
    from backend.routers.admin import _require_admin

    await _require_admin(request)
    storage = get_storage(request)
    return _read_tickets_from_index(
        storage, _INDEX_ALL, status=status, ticket_type=type, project_id=project_id,
    )


@router.put("/admin/feedback/{ticket_id}", response_model=FeedbackTicket)
async def update_feedback(ticket_id: str, body: UpdateFeedbackRequest, request: Request):
    from backend.routers.admin import _require_admin

    await _require_admin(request)
    storage = get_storage(request)

    ticket = _read_ticket(storage, ticket_id)
    if not ticket:
        raise HTTPException(404, "Ticket not found")

    prev_admin_notes = (ticket.admin_notes or "").strip()

    if body.status is not None:
        ticket.status = body.status
    if body.admin_notes is not None:
        ticket.admin_notes = body.admin_notes
    ticket.updated_at = datetime.now(timezone.utc).isoformat()

    storage.write_json(_ticket_key(ticket_id), ticket.model_dump())

    # If admin_notes changed to a new, non-empty value, notify the submitter.
    new_admin_notes = (ticket.admin_notes or "").strip()
    if new_admin_notes and new_admin_notes != prev_admin_notes:
        try:
            from backend.services.email import send_feedback_reply_email
            await send_feedback_reply_email(
                user_id=ticket.user_id,
                reply_text=new_admin_notes,
                original_message=ticket.message,
                recipient_name=ticket.user_name,
                recipient_email=ticket.user_email,
                project_name=ticket.project_name,
                finding_designator=ticket.finding_designator,
                finding_mpn=ticket.finding_mpn,
            )
        except Exception:
            logger.exception(
                "Failed to enqueue feedback-reply email for ticket %s", ticket_id
            )

    return ticket
