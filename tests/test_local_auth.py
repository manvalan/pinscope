"""Local Pinscope auth — users, JWT, email lookup (no FastAPI required)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


@pytest.fixture()
def auth_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "auth_jwt_secret", "test-secret-not-for-prod")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "clerk_secret_key", "")
    monkeypatch.setattr(settings, "clerk_jwks_url", "")
    monkeypatch.setattr(settings, "auth_admin_emails", "")
    return tmp_path


def test_create_user_and_authenticate(auth_env: Path):
    from backend.services import local_users

    user = local_users.create_user("a@example.com", "password12", "Ada")
    assert user.is_admin is True
    assert local_users.authenticate("a@example.com", "password12")
    assert local_users.authenticate("a@example.com", "wrong") is None
    assert local_users.find_by_email("a@example.com").user_id == user.user_id


def test_second_user_not_admin(auth_env: Path):
    from backend.services import local_users
    from backend.services.user_directory import find_user_id_by_email

    local_users.create_user("owner@example.com", "password12", "Owner")
    mate = local_users.create_user("mate@example.com", "password12", "Mate")
    assert mate.is_admin is False
    uid = asyncio.run(find_user_id_by_email("mate@example.com"))
    assert uid == mate.user_id


def test_migrate_local_projects_on_first_register(auth_env: Path):
    from backend.services import local_users

    proj = auth_env / "users" / "local" / "projects" / "abc123"
    proj.mkdir(parents=True)
    (proj / "meta.json").write_text('{"id":"abc123","name":"Old"}\n')

    user = local_users.create_user("first@example.com", "password12")
    assert (auth_env / "users" / user.user_id / "projects" / "abc123").is_dir()
    assert not (auth_env / "users" / "local" / "projects" / "abc123").exists()


def test_duplicate_email_rejected(auth_env: Path):
    from backend.services import local_users

    local_users.create_user("a@example.com", "password12")
    with pytest.raises(ValueError, match="already exists"):
        local_users.create_user("a@example.com", "password12")
