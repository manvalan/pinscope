"""Local Pinscope user directory (self-host auth, no Clerk).

Users live under ``data/auth/users/{user_id}.json`` with an email index.
Passwords use stdlib ``hashlib.scrypt``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from backend.config import settings

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class LocalUser:
    user_id: str
    email: str
    name: str | None
    password_hash: str
    is_admin: bool = False
    created_at: str = ""

    def public(self) -> dict:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "name": self.name,
            "is_admin": self.is_admin,
        }


def _auth_root() -> Path:
    root = Path(settings.data_dir) / "auth"
    (root / "users").mkdir(parents=True, exist_ok=True)
    (root / "by_email").mkdir(parents=True, exist_ok=True)
    return root


def _email_key(email: str) -> str:
    return email.strip().lower()


def _email_path(email: str) -> Path:
    # Filesystem-safe key from normalized email
    key = _email_key(email).replace("/", "_")
    return _auth_root() / "by_email" / f"{key}.json"


def _user_path(user_id: str) -> Path:
    return _auth_root() / "users" / f"{user_id}.json"


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32
    )
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, salt_hex, digest_hex = encoded.split("$", 2)
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    salt = bytes.fromhex(salt_hex)
    check = hash_password(password, salt=salt)
    return secrets.compare_digest(check, encoded)


def validate_email(email: str) -> str:
    e = email.strip().lower()
    if not _EMAIL_RE.match(e):
        raise ValueError("Invalid email address")
    return e


def validate_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")


def get_user(user_id: str) -> LocalUser | None:
    path = _user_path(user_id)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return LocalUser(**data)


def find_by_email(email: str) -> LocalUser | None:
    path = _email_path(email)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    uid = data.get("user_id")
    if not uid:
        return None
    return get_user(uid)


def list_users() -> list[LocalUser]:
    users_dir = _auth_root() / "users"
    out: list[LocalUser] = []
    for path in sorted(users_dir.glob("*.json")):
        try:
            out.append(LocalUser(**json.loads(path.read_text(encoding="utf-8"))))
        except Exception:
            logger.exception("Skipping corrupt user file %s", path)
    return out


def _save_user(user: LocalUser) -> None:
    _user_path(user.user_id).write_text(
        json.dumps(asdict(user), indent=2) + "\n", encoding="utf-8"
    )
    _email_path(user.email).write_text(
        json.dumps({"user_id": user.user_id}) + "\n", encoding="utf-8"
    )


def user_count() -> int:
    return len(list((_auth_root() / "users").glob("*.json")))


def _migrate_local_projects(new_owner_id: str) -> int:
    """Move ``users/local/projects/*`` under the first admin, if present."""
    local_projects = Path(settings.data_dir) / "users" / "local" / "projects"
    if not local_projects.is_dir():
        return 0
    dest_root = Path(settings.data_dir) / "users" / new_owner_id / "projects"
    dest_root.mkdir(parents=True, exist_ok=True)
    moved = 0
    for child in local_projects.iterdir():
        if not child.is_dir():
            continue
        target = dest_root / child.name
        if target.exists():
            continue
        shutil.move(str(child), str(target))
        moved += 1
        logger.info("Migrated project %s → user %s", child.name, new_owner_id)
    return moved


def create_user(email: str, password: str, name: str | None = None) -> LocalUser:
    email = validate_email(email)
    validate_password(password)
    if find_by_email(email):
        raise ValueError("An account with that email already exists")

    from datetime import datetime, timezone

    first = user_count() == 0
    admin_emails = {
        e.strip().lower()
        for e in (settings.auth_admin_emails or "").split(",")
        if e.strip()
    }
    is_admin = first or email in admin_emails

    user = LocalUser(
        user_id="usr_" + uuid.uuid4().hex,
        email=email,
        name=(name or "").strip() or None,
        password_hash=hash_password(password),
        is_admin=is_admin,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    _save_user(user)

    if first:
        try:
            n = _migrate_local_projects(user.user_id)
            if n:
                logger.info("First admin inherited %s local project(s)", n)
        except Exception:
            logger.exception("Failed to migrate users/local projects")

    return user


def authenticate(email: str, password: str) -> LocalUser | None:
    user = find_by_email(email)
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user
