"""Onboarding survey — appends responses to a Google Sheet and tracks completion."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from backend.config import settings
from backend.services.storage import StorageBackend

logger = logging.getLogger(__name__)

_SURVEY_PREFIX = "admin/survey/"


def _status_key(user_id: str) -> str:
    return f"{_SURVEY_PREFIX}{user_id}.json"


def is_completed(storage: StorageBackend, user_id: str) -> bool:
    return storage.exists(_status_key(user_id))


def _mark_completed(storage: StorageBackend, user_id: str) -> None:
    payload = {"completed": True, "timestamp": datetime.now(timezone.utc).isoformat()}
    storage.write_json(_status_key(user_id), payload)


def _build_sheets_service():
    """Build an authenticated Google Sheets API service.

    On Cloud Run, google.auth.default() returns Compute Engine credentials
    which are auto-scoped. We just need the Sheets API enabled in the GCP
    project and the service account shared on the sheet.
    """
    try:
        import google.auth
        from googleapiclient.discovery import build
    except ImportError:
        logger.warning("google-api-python-client not installed; survey sheet disabled")
        return None

    try:
        credentials, project = google.auth.default()
        logger.debug("Sheets: credentials type=%s project=%s", type(credentials).__name__, project)
        # Compute Engine credentials don't need explicit scopes — they use
        # the access scopes set on the instance (which default to cloud-platform).
        # For user/SA key credentials, we need to scope them.
        if hasattr(credentials, "with_scopes"):
            credentials = credentials.with_scopes(
                ["https://www.googleapis.com/auth/spreadsheets"]
            )
        return build("sheets", "v4", credentials=credentials, cache_discovery=False)
    except Exception:
        logger.exception("Could not build Sheets service")
        return None


async def append_to_sheet(
    user_id: str,
    email: str,
    name: str,
    referral_source: str,
    user_profile: str,
) -> bool:
    """Append a survey row to the configured Google Sheet. Returns True on success."""
    sheet_id = settings.survey_sheet_id
    if not sheet_id:
        logger.warning("SURVEY_SHEET_ID not set; skipping sheet append for user %s", user_id)
        return False

    service = _build_sheets_service()
    if not service:
        return False

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    row = [timestamp, user_id, email, name, referral_source, user_profile]

    try:
        await asyncio.to_thread(
            service.spreadsheets()
            .values()
            .append(
                spreadsheetId=sheet_id,
                range="Sheet1!A:F",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            )
            .execute
        )
        logger.info("Survey response appended for user %s", user_id)
        return True
    except Exception:
        logger.exception("Failed to append survey response to Google Sheet for user %s", user_id)
        return False
