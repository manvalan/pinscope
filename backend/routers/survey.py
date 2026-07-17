"""Onboarding survey endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.config import settings
from backend.routers.deps import get_storage, get_user_id
from backend.services import survey as survey_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/survey", tags=["survey"])


class SurveySubmission(BaseModel):
    referral_source: str
    user_profile: str


@router.get("/status")
async def survey_status(request: Request):
    storage = get_storage(request)
    user_id = get_user_id(request)
    return {"completed": survey_svc.is_completed(storage, user_id)}


@router.post("")
async def submit_survey(request: Request, body: SurveySubmission):
    storage = get_storage(request)
    user_id = get_user_id(request)

    if survey_svc.is_completed(storage, user_id):
        return {"ok": True, "detail": "already_submitted"}

    # Resolve user email/name from Clerk if available
    email = "unknown"
    name = "unknown"
    if settings.use_auth:
        try:
            from backend.services.email import _resolve_clerk_user

            clerk_user = await _resolve_clerk_user(user_id)
            if clerk_user:
                emails = clerk_user.get("email_addresses", [])
                email = emails[0].get("email_address", "unknown") if emails else "unknown"
                first = clerk_user.get("first_name") or ""
                last = clerk_user.get("last_name") or ""
                name = f"{first} {last}".strip() or "unknown"
        except Exception:
            logger.warning("Failed to resolve Clerk user %s for survey", user_id)

    sheet_ok = await survey_svc.append_to_sheet(
        user_id=user_id,
        email=email,
        name=name,
        referral_source=body.referral_source,
        user_profile=body.user_profile,
    )

    if sheet_ok or not settings.survey_sheet_id:
        survey_svc._mark_completed(storage, user_id)
        return {"ok": True}

    # Sheet write failed — don't mark completed so the user can retry
    return {"ok": False, "detail": "sheet_write_failed"}
