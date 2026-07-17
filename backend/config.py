"""Backend configuration via environment variables."""

import importlib.util
import json
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

# Resolve paths relative to the project root (one level up from backend/)
_BACKEND_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BACKEND_DIR.parent

# Load skills manifest once at import time
_MANIFEST_PATH = _BACKEND_DIR / "skills_manifest.json"
_SKILLS_MANIFEST: dict = (
    json.loads(_MANIFEST_PATH.read_text()) if _MANIFEST_PATH.exists() else {}
)


class Settings(BaseSettings):
    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # Per-stage model overrides (fall back to anthropic_model if empty)
    model_pintable: str = ""
    model_pattern: str = ""
    model_specs: str = ""
    model_validation: str = "claude-sonnet-4-6"
    model_auto_resolve: str = "claude-haiku-4-5-20251001"
    model_normalize: str = "claude-sonnet-4-6"

    # Gemini (set GEMINI_API_KEY to enable)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-pro-preview"

    # Per-stage Gemini model overrides (fall back to gemini_model if empty)
    model_validation_gemini: str = ""
    model_pintable_gemini: str = ""
    model_pattern_gemini: str = ""
    model_specs_gemini: str = ""
    model_auto_resolve_gemini: str = ""
    model_normalize_gemini: str = ""

    # Provider routing — provider_default is the global default; per-stage
    # overrides win when non-empty. Set provider_validation=gemini to route
    # the validation stage to Gemini while leaving extraction on Anthropic.
    provider_default: str = "anthropic"
    provider_pintable: str = ""
    provider_pattern: str = ""
    provider_specs: str = ""
    provider_validation: str = ""
    provider_auto_resolve: str = ""
    provider_normalize: str = ""

    # Per-stage fallback provider/model — used if the primary stage call
    # raises (e.g. Gemini 503 UNAVAILABLE). Leave empty to disable fallback
    # for that stage. If fallback_provider_<stage> is set but
    # fallback_model_<stage> is empty, the fallback uses that provider's
    # default model (anthropic_model or gemini_model).
    fallback_provider_pintable: str = ""
    fallback_provider_pattern: str = ""
    fallback_provider_specs: str = ""
    fallback_provider_validation: str = ""
    fallback_provider_auto_resolve: str = ""
    fallback_provider_normalize: str = ""
    fallback_model_pintable: str = ""
    fallback_model_pattern: str = ""
    fallback_model_specs: str = ""
    fallback_model_validation: str = ""
    fallback_model_auto_resolve: str = ""
    fallback_model_normalize: str = ""

    # Max parallel IC agents — the single knob controlling concurrency for
    # BOTH the IC pintable extraction stage and the direct datasheet review
    # stage. Change this one number (or the IC_CONCURRENCY env var) to scale
    # how many ICs are processed in parallel.
    ic_concurrency: int = 6

    # Per-IC normalize pass — dedup findings sharing a root cause and
    # re-grade severity against a fixed rubric. Runs after submit_review.
    normalize_findings_enabled: bool = True

    # Cross-IC dedup pass — collapse one physical interface defect reported
    # from both ICs (e.g. a 5V-into-3V3 net flagged once per endpoint) into a
    # single finding. Runs once after all per-IC reviews complete.
    cross_ic_dedup_enabled: bool = True

    # Paths (relative to project root, used by LocalStorageBackend)
    data_dir: Path = _PROJECT_ROOT / "data"
    taxonomy_dir: Path = _PROJECT_ROOT / "taxonomy"

    # GCS (if set, use GCSStorageBackend; otherwise LocalStorageBackend)
    gcs_bucket: str = ""

    # Clerk authentication
    clerk_secret_key: str = ""
    clerk_publishable_key: str = ""
    clerk_jwks_url: str = ""

    # DigiKey API (optional — enables auto-fetch datasheets)
    digikey_client_id: str = ""
    digikey_client_secret: str = ""
    digikey_environment: str = "production"
    digikey_locale_site: str = "US"
    digikey_locale_language: str = "en"
    digikey_locale_currency: str = "USD"

    # Purple Parts API (optional — converts LCSC codes to MPNs before DigiKey)
    purple_parts_url: str = ""
    purple_parts_api_key: str = ""

    # Email notifications (Gmail API via service account)
    email_sender: str = ""
    email_frontend_url: str = ""
    email_admin_notify: str = ""  # fixed recipient for pipeline-started alerts
    contact_recipient: str = ""  # where /api/contact submissions are delivered

    # Stripe billing (pay-as-you-go only — no subscription prices needed)
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    # Open-core: master switch for the credits/Stripe billing system.
    # True = credit gating + charges + billing/credits routers.
    # False = OSS/self-host mode: pipelines run free, billing routes unmounted.
    # Defaults to whether the private billing modules exist in this checkout
    # (present in the cloud/gateway repo, absent in the open-source core), so
    # a bare core checkout runs free with no configuration. An explicit
    # BILLING_ENABLED env var always wins.
    billing_enabled: bool = Field(
        default_factory=lambda: importlib.util.find_spec(
            "backend.services.stripe_billing"
        )
        is not None
    )

    # Onboarding survey (Google Sheet)
    survey_sheet_id: str = ""

    # CORS
    cors_origins: list[str] = ["http://localhost:3000"]

    # Cloud Run Job worker (pipeline runner)
    pipeline_worker_job_name: str = "pinscopex-pipeline-worker"
    pipeline_worker_region: str = "us-central1"
    pipeline_worker_project: str = ""  # GCP project id; defaults to GOOGLE_CLOUD_PROJECT or metadata
    pipeline_worker_timeout_seconds: int = 3600

    # Sweeper: a "running" project is considered stale if its last update
    # timestamp is older than this and the worker execution is in a
    # terminal Cloud Run state (or the executor isn't reachable).
    pipeline_sweeper_stale_seconds: int = 60

    model_config = {
        "env_file": str(_BACKEND_DIR / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def use_stripe(self) -> bool:
        return bool(self.stripe_secret_key)

    @property
    def use_digikey(self) -> bool:
        return bool(self.digikey_client_id and self.digikey_client_secret)

    @property
    def use_purple_parts(self) -> bool:
        return bool(self.purple_parts_url and self.purple_parts_api_key)

    @property
    def use_gcs(self) -> bool:
        return bool(self.gcs_bucket)

    @property
    def use_auth(self) -> bool:
        return bool(self.clerk_secret_key and self.clerk_jwks_url)

    @property
    def use_email(self) -> bool:
        return bool(self.email_sender and self.email_frontend_url)

    def provider_for_stage(self, stage: str) -> str:
        """Return the LLM provider name for a pipeline stage."""
        override = getattr(self, f"provider_{stage}", "")
        return override or self.provider_default

    def model_for_stage(self, stage: str) -> str:
        """Return the model for a pipeline stage, provider-aware.

        For Anthropic: falls back to model_<stage>, then anthropic_model.
        For Gemini:    falls back to model_<stage>_gemini, then gemini_model.
        """
        provider = self.provider_for_stage(stage)
        if provider == "gemini":
            override = getattr(self, f"model_{stage}_gemini", "")
            return override or self.gemini_model
        override = getattr(self, f"model_{stage}", "")
        return override or self.anthropic_model

    def fallback_for_stage(self, stage: str) -> tuple[str, str] | None:
        """Return (provider, model) for the stage's fallback, or None if no
        fallback is configured. Used by call_with_fallback() to retry once
        when the primary provider raises.
        """
        fb_provider = getattr(self, f"fallback_provider_{stage}", "")
        if not fb_provider:
            return None
        fb_model = getattr(self, f"fallback_model_{stage}", "")
        if not fb_model:
            fb_model = self.gemini_model if fb_provider == "gemini" else self.anthropic_model
        return (fb_provider, fb_model)

    def get_default_model_version(self) -> str:
        """Return the default model_version for new extractions from skills_manifest.json."""
        return _SKILLS_MANIFEST.get("default_model_version", "1.0.0")

    def get_skill(self, name: str) -> tuple[str, str]:
        """Return (skill_id, version) from skills_manifest.json or raise."""
        entry = _SKILLS_MANIFEST.get(name)
        if not entry:
            raise RuntimeError(
                f"Skill '{name}' not found in {_MANIFEST_PATH}. "
                f"Run scripts/upload_skills.py to create skills."
            )
        return entry["skill_id"], entry["latest_version"]


settings = Settings()
