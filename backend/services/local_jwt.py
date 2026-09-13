"""Periscope local JWT helpers (HS256)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from backend.config import settings

ALGORITHM = "HS256"
TOKEN_TTL_DAYS = 30


def issue_token(user_id: str, email: str) -> str:
    secret = settings.auth_jwt_secret
    if not secret:
        raise RuntimeError("AUTH_JWT_SECRET is not configured")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "email": email,
        "iss": "periscope-local",
        "iat": now,
        "exp": now + timedelta(days=TOKEN_TTL_DAYS),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    secret = settings.auth_jwt_secret
    if not secret:
        return None
    # Accept pre-rebrand issuer so existing sessions keep working.
    for issuer in ("periscope-local", "pinscope-local"):
        try:
            return jwt.decode(
                token,
                secret,
                algorithms=[ALGORITHM],
                issuer=issuer,
                options={"verify_aud": False},
                leeway=10,
            )
        except jwt.PyJWTError:
            continue
    return None
