"""Clerk JWT verification for FastAPI.

Validates JWT tokens from the Authorization header against Clerk's JWKS endpoint.
Extracts user_id (sub claim) for per-user storage scoping.
"""

from __future__ import annotations

import time
from typing import Any

import jwt
from fastapi import Request

from backend.config import settings

# JWKS cache
_jwks_client: jwt.PyJWKClient | None = None
_SKIP_PATHS = {"/docs", "/openapi.json", "/redoc", "/health", "/api/billing/webhook"}


def _get_jwks_client() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        jwks_url = settings.clerk_jwks_url
        if not jwks_url:
            # Default Clerk JWKS URL derived from publishable key
            # Clerk publishable keys start with pk_test_ or pk_live_
            # JWKS is at https://{clerk-frontend-api}/.well-known/jwks.json
            # The user must set CLERK_JWKS_URL explicitly
            raise RuntimeError(
                "CLERK_JWKS_URL must be set for authentication. "
                "Find it in your Clerk dashboard under API Keys."
            )
        _jwks_client = jwt.PyJWKClient(jwks_url, cache_keys=True)
    return _jwks_client


async def verify_clerk_token(request: Request) -> str | None:
    """Verify Clerk JWT and return user_id, or None if invalid.

    Returns None for:
    - Missing Authorization header
    - Invalid/expired token
    - Skip paths (docs, health)
    """
    # Skip auth for docs/health endpoints
    if request.url.path in _SKIP_PATHS:
        return "anonymous"

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        # Fallback: check query param (EventSource/SSE can't send headers)
        token = request.query_params.get("token")
        if not token:
            return None
    else:
        token = auth_header[7:]

    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)

        payload: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={
                "verify_exp": True,
                "verify_aud": False,  # Clerk doesn't always set aud
                "verify_iss": True,
            },
            # Clerk tokens use the Clerk instance URL as issuer
            # e.g. https://abc123.clerk.accounts.dev from https://abc123.clerk.accounts.dev/.well-known/jwks.json
            issuer=settings.clerk_jwks_url.replace("/.well-known/jwks.json", "") if settings.clerk_jwks_url else None,
            leeway=10,  # 10 second clock skew tolerance
        )

        user_id = payload.get("sub")
        if not user_id:
            return None

        return user_id

    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
    except Exception:
        return None
