"""Global admin settings, persisted via StorageBackend.

Settings are stored at ``admin/settings.json`` in storage (GCS or local
``data/``).  The module mirrors the pattern in ``limits.py``.
"""

from __future__ import annotations

from packaging.version import Version

from backend.services.storage import StorageBackend

_SETTINGS_KEY = "admin/settings.json"

_DEFAULTS: dict[str, str] = {
    "min_model_version": "0.0.0",  # no threshold by default
}


def get_admin_settings(storage: StorageBackend) -> dict:
    """Return the full admin settings dict, with defaults."""
    if storage.exists(_SETTINGS_KEY):
        data = storage.read_json(_SETTINGS_KEY)
        return {**_DEFAULTS, **data}
    return dict(_DEFAULTS)


def get_min_model_version(storage: StorageBackend) -> str:
    """Return the min_model_version threshold."""
    return get_admin_settings(storage).get("min_model_version", "0.0.0")


def set_min_model_version(storage: StorageBackend, version: str) -> None:
    """Set the min_model_version threshold.  Validates semver format."""
    Version(version)  # raises InvalidVersion if bad
    data = get_admin_settings(storage)
    data["min_model_version"] = version
    storage.write_json(_SETTINGS_KEY, data)


def version_is_stale(component_version: str, min_version: str) -> bool:
    """Return True if *component_version* < *min_version* (semver)."""
    if min_version == "0.0.0":
        return False
    try:
        return Version(component_version) < Version(min_version)
    except Exception:
        return True  # unparseable → treat as stale
