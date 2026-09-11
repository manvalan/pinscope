"""JWT verification for FastAPI (Clerk JWKS or local Pinscope HS256)."""

from __future__ import annotations

from typing import Any

import jwt
from fastapi import Request

from backend.config import settings

# JWKS cache
_jwks_client: jwt.PyJWKClient | None = None
_SKIP_PATHS = {
    "/docs",
    "/openapi.json",
    "/redoc",
    "/health",
    "/api/billing/webhook",
    "/api/auth/mode",
    "/api/auth/register",
    "/api/auth/login",
    "/api/contact",
}


def _get_jwks_client() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        jwks_url = settings.clerk_jwks_url
        if not jwks_url:
            raise RuntimeError(
                "CLERK_JWKS_URL must be set for Clerk authentication. "
                "Find it in your Clerk dashboard under API Keys."
            )
        _jwks_client = jwt.PyJWKClient(jwks_url, cache_keys=True)
    return _jwks_client


def _bearer_or_query_token(request: Request) -> str | None:
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    # EventSource/SSE can't send headers
    return request.query_params.get("token")


async def verify_clerk_token(request: Request) -> str | None:
    """Verify Clerk JWT and return user_id, or None if invalid."""
    if request.url.path in _SKIP_PATHS:
        return "anonymous"

    token = _bearer_or_query_token(request)
    if not token:
        return None

    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)

        payload: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={
                "verify_exp": True,
                "verify_aud": False,
                "verify_iss": True,
            },
            issuer=(
                settings.clerk_jwks_url.replace("/.well-known/jwks.json", "")
                if settings.clerk_jwks_url
                else None
            ),
            leeway=10,
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


async def verify_local_token(request: Request) -> str | None:
    """Verify Pinscope local JWT and return user_id, or None if invalid."""
    if request.url.path in _SKIP_PATHS:
        return "anonymous"

    token = _bearer_or_query_token(request)
    if not token:
        return None

    from backend.services.local_jwt import decode_token

    payload = decode_token(token)
    if not payload:
        return None
    user_id = payload.get("sub")
    return str(user_id) if user_id else None


async def verify_request_user(request: Request) -> str | None:
    """Dispatch to Clerk or local JWT verification."""
    if settings.use_clerk:
        return await verify_clerk_token(request)
    if settings.use_local_auth:
        return await verify_local_token(request)
    return None
