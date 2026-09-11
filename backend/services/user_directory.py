"""User profile lookup for collaborators / admin — Clerk or local auth."""

from __future__ import annotations

import logging

from backend.config import settings

logger = logging.getLogger(__name__)


async def find_user_id_by_email(email: str) -> str | None:
    email = email.strip().lower()
    if not email:
        return None
    if settings.use_clerk:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.clerk.com/v1/users",
                params={"email_address": [email]},
                headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
            )
        if resp.status_code != 200:
            logger.warning("Clerk email lookup failed: %s", resp.status_code)
            return None
        users = resp.json()
        if not users:
            return None
        return users[0].get("id")
    if settings.use_local_auth:
        from backend.services import local_users

        user = local_users.find_by_email(email)
        return user.user_id if user else None
    return None


async def get_user_profile(user_id: str) -> dict:
    """Return {user_id, name, email, image_url, is_admin?}."""
    entry = {
        "user_id": user_id,
        "name": None,
        "email": None,
        "image_url": None,
        "is_admin": False,
    }
    if settings.use_clerk:
        import httpx

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://api.clerk.com/v1/users/{user_id}",
                    headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
                )
            if resp.status_code == 200:
                clerk = resp.json()
                first = clerk.get("first_name") or ""
                last = clerk.get("last_name") or ""
                entry["name"] = f"{first} {last}".strip() or None
                emails = clerk.get("email_addresses", [])
                if emails:
                    entry["email"] = emails[0].get("email_address")
                entry["image_url"] = clerk.get("image_url")
                role = (clerk.get("public_metadata") or {}).get("role")
                entry["is_admin"] = role == "admin"
        except Exception:
            logger.exception("Clerk profile fetch failed for %s", user_id)
        return entry
    if settings.use_local_auth:
        from backend.services import local_users

        user = local_users.get_user(user_id)
        if user:
            entry["name"] = user.name
            entry["email"] = user.email
            entry["is_admin"] = user.is_admin
        return entry
    return entry
