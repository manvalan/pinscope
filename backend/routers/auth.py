"""Local Pinscope auth endpoints (register / login / me)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.config import settings
from backend.services import local_jwt, local_users

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)
    name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


def _require_local_auth() -> None:
    if not settings.use_local_auth:
        raise HTTPException(
            400,
            "Local auth is not enabled. Set AUTH_JWT_SECRET on the server.",
        )


@router.get("/mode")
async def auth_mode():
    """Public: how the frontend should authenticate."""
    if settings.use_clerk:
        return {"mode": "clerk", "auth_enabled": True}
    if settings.use_local_auth:
        return {"mode": "local", "auth_enabled": True}
    return {"mode": "off", "auth_enabled": False}


@router.post("/register")
async def register(body: RegisterRequest):
    _require_local_auth()
    try:
        user = local_users.create_user(body.email, body.password, body.name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    token = local_jwt.issue_token(user.user_id, user.email)
    return {"token": token, "user": user.public()}


@router.post("/login")
async def login(body: LoginRequest):
    _require_local_auth()
    user = local_users.authenticate(body.email, body.password)
    if not user:
        raise HTTPException(401, "Invalid email or password")
    token = local_jwt.issue_token(user.user_id, user.email)
    return {"token": token, "user": user.public()}


@router.get("/me")
async def me(request: Request):
    _require_local_auth()
    user_id = getattr(request.state, "user_id", None)
    if not user_id or user_id == "local" or user_id == "anonymous":
        raise HTTPException(401, "Authentication required")
    user = local_users.get_user(user_id)
    if not user:
        raise HTTPException(401, "User not found")
    return user.public()
