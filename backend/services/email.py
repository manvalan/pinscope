"""Email notification service using Gmail API with domain-wide delegation."""

from __future__ import annotations

import asyncio
import base64
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from backend.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Clerk user resolution
# ---------------------------------------------------------------------------


async def _resolve_clerk_user(user_id: str) -> dict | None:
    """Fetch user profile from Clerk Backend API. Returns None on failure."""
    if not settings.use_auth:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.clerk.com/v1/users/{user_id}",
                headers={"Authorization": f"Bearer {settings.clerk_secret_key}"},
            )
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        logger.warning("Failed to fetch Clerk user %s for email notification", user_id)
    return None


# ---------------------------------------------------------------------------
# Gmail API
# ---------------------------------------------------------------------------


def _build_gmail_service():
    """Build an authenticated Gmail API service using domain-wide delegation.

    On Cloud Run, google.auth.default() returns compute engine credentials
    which don't support .with_subject() for domain-wide delegation. We use
    the IAM signBlob API to create proper service account credentials that
    can impersonate the sender via domain-wide delegation.

    Returns None if credentials cannot be built.
    """
    try:
        import google.auth
        import google.auth.transport.requests
        from google.auth import iam
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        logger.warning("google-api-python-client not installed; email disabled")
        return None

    scopes = ["https://www.googleapis.com/auth/gmail.send"]

    try:
        source_credentials, _ = google.auth.default()
        logger.debug("Gmail: got default credentials type=%s", type(source_credentials).__name__)

        # Check if these credentials already support with_subject (e.g. key-file)
        if hasattr(source_credentials, "_signer"):
            logger.debug("Gmail: using service account key-file path (with_subject)")
            delegated = source_credentials.with_subject(settings.email_sender)
            return build("gmail", "v1", credentials=delegated, cache_discovery=False)

        # Cloud Run path: use IAM signBlob to create credentials that support
        # the `subject` claim needed for domain-wide delegation.
        logger.debug("Gmail: using IAM signBlob path (Cloud Run / Compute Engine)")
        request = google.auth.transport.requests.Request()
        source_credentials.refresh(request)
        sa_email = source_credentials.service_account_email
        logger.debug("Gmail: resolved service account email=%s", sa_email)

        signer = iam.Signer(
            request=request,
            credentials=source_credentials,
            service_account_email=sa_email,
        )

        credentials = service_account.Credentials(
            signer=signer,
            service_account_email=sa_email,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=scopes,
            subject=settings.email_sender,
        )

        svc = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        logger.debug("Gmail: service built successfully, sender=%s", settings.email_sender)
        return svc

    except Exception:
        logger.warning("Could not obtain credentials for Gmail API", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# HTML email template
# ---------------------------------------------------------------------------

_STATUS_COLORS = {
    "ERROR": "#ef4444",
    "WARNING": "#f59e0b",
    "INFO": "#3b82f6",
}


def _render_report_email(
    recipient_name: str,
    project_name: str,
    project_id: str,
    summary: dict[str, int],
    total_cost_usd: float | None,
) -> str:
    """Render the HTML email body with inline CSS."""
    report_url = f"{settings.email_frontend_url}/project/{project_id}/report"

    total = summary.get("total", 0)
    errors = summary.get("ERROR", 0)
    warnings = summary.get("WARNING", 0)
    infos = summary.get("INFO", 0)

    # Summary rows
    summary_rows = ""
    for label, count, color in [
        ("Errors", errors, _STATUS_COLORS["ERROR"]),
        ("Warnings", warnings, _STATUS_COLORS["WARNING"]),
        ("Info", infos, _STATUS_COLORS["INFO"]),
    ]:
        if count > 0:
            summary_rows += f"""
            <tr>
              <td style="padding: 6px 0; font-size: 15px; color: #374151;">
                <span style="display: inline-block; width: 10px; height: 10px; border-radius: 50%; background-color: {color}; margin-right: 8px; vertical-align: middle;"></span>
                {count} {label}
              </td>
            </tr>"""

    # Headline color based on worst finding
    if errors > 0:
        headline_color = _STATUS_COLORS["ERROR"]
        headline_text = f"{errors} error{'s' if errors != 1 else ''} found"
    elif warnings > 0:
        headline_color = _STATUS_COLORS["WARNING"]
        headline_text = f"{warnings} warning{'s' if warnings != 1 else ''} found"
    elif total == 0:
        headline_color = "#10b981"
        headline_text = "No issues found"
    else:
        headline_color = _STATUS_COLORS["INFO"]
        headline_text = f"{infos} note{'s' if infos != 1 else ''}"

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f3f4f6; -webkit-font-smoothing: antialiased;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f3f4f6;">
    <tr><td align="center" style="padding: 40px 16px;">
      <table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width: 600px; width: 100%; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">

        <!-- Header -->
        <tr><td style="background-color: #111827; padding: 28px 32px;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 20px; font-weight: 700; color: #ffffff; letter-spacing: -0.025em;">
                Pinscope
              </td>
              <td align="right" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 12px; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.05em;">
                Report Ready
              </td>
            </tr>
          </table>
        </td></tr>

        <!-- Body -->
        <tr><td style="padding: 32px;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">

            <!-- Greeting -->
            <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 15px; color: #374151; padding-bottom: 8px;">
              Hi {recipient_name},
            </td></tr>
            <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 15px; color: #374151; padding-bottom: 24px;">
              Your validation report for <strong style="color: #111827;">{project_name}</strong> is ready.
            </td></tr>

            <!-- Headline badge -->
            <tr><td style="padding-bottom: 20px;">
              <table cellpadding="0" cellspacing="0" border="0" style="background-color: {headline_color}; border-radius: 6px;">
                <tr><td style="padding: 8px 16px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 14px; font-weight: 600; color: #ffffff;">
                  {headline_text}
                </td></tr>
              </table>
            </td></tr>

            <!-- Summary card -->
            <tr><td>
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f9fafb; border-radius: 8px; border: 1px solid #e5e7eb;">
                <tr><td style="padding: 20px;">
                  <table width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 11px; font-weight: 600; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em; padding-bottom: 12px;">
                      Validation Summary
                    </td></tr>
                    <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 28px; font-weight: 700; color: #111827; padding-bottom: 16px;">
                      {total} <span style="font-size: 15px; font-weight: 400; color: #6b7280;">findings</span>
                    </td></tr>
                    <tr><td>
                      <table width="100%" cellpadding="0" cellspacing="0" border="0" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
                        {summary_rows}
                      </table>
                    </td></tr>
                  </table>
                </td></tr>
              </table>
            </td></tr>

            <!-- CTA button -->
            <tr><td align="center" style="padding-top: 28px; padding-bottom: 8px;">
              <!--[if mso]>
              <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" href="{report_url}" style="height:48px;v-text-anchor:middle;width:220px;" arcsize="14%" fillcolor="#3b82f6" stroke="f">
                <w:anchorlock/>
                <center style="color:#ffffff;font-family:sans-serif;font-size:15px;font-weight:bold;">View Report &rarr;</center>
              </v:roundrect>
              <![endif]-->
              <!--[if !mso]><!-->
              <a href="{report_url}" target="_blank" style="display: inline-block; background-color: #3b82f6; color: #ffffff; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 15px; font-weight: 600; text-decoration: none; padding: 12px 32px; border-radius: 8px; letter-spacing: -0.01em;">
                View Report &rarr;
              </a>
              <!--<![endif]-->
            </td></tr>

          </table>
        </td></tr>

        <!-- Footer -->
        <tr><td style="background-color: #f9fafb; padding: 20px 32px; border-top: 1px solid #e5e7eb;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 12px; color: #9ca3af;">
              Pinscope &middot; Agentic schematic validation
            </td></tr>
          </table>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Pipeline-started email template (admin notification)
# ---------------------------------------------------------------------------


def _render_pipeline_started_email(
    creator_name: str,
    creator_email: str,
    project_name: str,
    project_id: str,
    num_components: int,
    num_nets: int,
    num_ics: int,
    num_passives: int,
    num_simple: int,
) -> str:
    """Render the pipeline-started HTML email for admin notification."""
    project_url = f"{settings.email_frontend_url}/project/{project_id}"

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f3f4f6; -webkit-font-smoothing: antialiased;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f3f4f6;">
    <tr><td align="center" style="padding: 40px 16px;">
      <table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width: 600px; width: 100%; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">

        <!-- Header -->
        <tr><td style="background-color: #111827; padding: 28px 32px;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 20px; font-weight: 700; color: #ffffff; letter-spacing: -0.025em;">
                Pinscope
              </td>
              <td align="right" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 12px; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.05em;">
                Pipeline Started
              </td>
            </tr>
          </table>
        </td></tr>

        <!-- Body -->
        <tr><td style="padding: 32px;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">

            <!-- Headline -->
            <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 15px; color: #374151; padding-bottom: 24px;">
              A new pipeline has been triggered for <strong style="color: #111827;">{project_name}</strong>.
            </td></tr>

            <!-- Creator card -->
            <tr><td style="padding-bottom: 16px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f0f9ff; border-radius: 8px; border: 1px solid #bae6fd;">
                <tr><td style="padding: 16px 20px;">
                  <table width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 11px; font-weight: 600; color: #0369a1; text-transform: uppercase; letter-spacing: 0.05em; padding-bottom: 8px;">
                      Created by
                    </td></tr>
                    <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 16px; font-weight: 600; color: #111827;">
                      {creator_name}
                    </td></tr>
                    <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 14px; color: #6b7280; padding-top: 2px;">
                      {creator_email}
                    </td></tr>
                  </table>
                </td></tr>
              </table>
            </td></tr>

            <!-- Design stats card -->
            <tr><td>
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f9fafb; border-radius: 8px; border: 1px solid #e5e7eb;">
                <tr><td style="padding: 20px;">
                  <table width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 11px; font-weight: 600; color: #6b7280; text-transform: uppercase; letter-spacing: 0.05em; padding-bottom: 16px;">
                      Design Overview
                    </td></tr>
                    <tr><td>
                      <table width="100%" cellpadding="0" cellspacing="0" border="0" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
                        <!-- Components row -->
                        <tr>
                          <td width="50%" style="padding: 8px 0; vertical-align: top;">
                            <span style="font-size: 28px; font-weight: 700; color: #111827;">{num_components}</span>
                            <span style="font-size: 14px; color: #6b7280; display: block; padding-top: 2px;">Components</span>
                          </td>
                          <td width="50%" style="padding: 8px 0; vertical-align: top;">
                            <span style="font-size: 28px; font-weight: 700; color: #111827;">{num_nets}</span>
                            <span style="font-size: 14px; color: #6b7280; display: block; padding-top: 2px;">Nets</span>
                          </td>
                        </tr>
                      </table>
                    </td></tr>
                    <!-- Breakdown -->
                    <tr><td style="padding-top: 12px; border-top: 1px solid #e5e7eb;">
                      <table width="100%" cellpadding="0" cellspacing="0" border="0" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 14px; color: #374151;">
                        <tr>
                          <td style="padding: 6px 0;">
                            <span style="display: inline-block; width: 10px; height: 10px; border-radius: 50%; background-color: #6366f1; margin-right: 8px; vertical-align: middle;"></span>
                            {num_ics} IC{"s" if num_ics != 1 else ""}
                          </td>
                          <td style="padding: 6px 0;">
                            <span style="display: inline-block; width: 10px; height: 10px; border-radius: 50%; background-color: #8b5cf6; margin-right: 8px; vertical-align: middle;"></span>
                            {num_passives} Passive{"s" if num_passives != 1 else ""}
                          </td>
                          <td style="padding: 6px 0;">
                            <span style="display: inline-block; width: 10px; height: 10px; border-radius: 50%; background-color: #a78bfa; margin-right: 8px; vertical-align: middle;"></span>
                            {num_simple} Discrete
                          </td>
                        </tr>
                      </table>
                    </td></tr>
                  </table>
                </td></tr>
              </table>
            </td></tr>

            <!-- CTA button -->
            <tr><td align="center" style="padding-top: 28px; padding-bottom: 8px;">
              <!--[if mso]>
              <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" href="{project_url}" style="height:48px;v-text-anchor:middle;width:220px;" arcsize="14%" fillcolor="#3b82f6" stroke="f">
                <w:anchorlock/>
                <center style="color:#ffffff;font-family:sans-serif;font-size:15px;font-weight:bold;">View Project &rarr;</center>
              </v:roundrect>
              <![endif]-->
              <!--[if !mso]><!-->
              <a href="{project_url}" target="_blank" style="display: inline-block; background-color: #3b82f6; color: #ffffff; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 15px; font-weight: 600; text-decoration: none; padding: 12px 32px; border-radius: 8px; letter-spacing: -0.01em;">
                View Project &rarr;
              </a>
              <!--<![endif]-->
            </td></tr>

          </table>
        </td></tr>

        <!-- Footer -->
        <tr><td style="background-color: #f9fafb; padding: 20px 32px; border-top: 1px solid #e5e7eb;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 12px; color: #9ca3af;">
              Pinscope &middot; Agentic schematic validation
            </td></tr>
          </table>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _encode_message(msg: MIMEMultipart) -> dict:
    """Encode a MIME message as a Gmail API payload."""
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return {"raw": raw}


async def _send_raw(to_email: str, msg: MIMEMultipart, label: str) -> None:
    """Send a MIME message via Gmail API. Logs but never raises."""
    try:
        service = _build_gmail_service()
        if not service:
            logger.warning("Gmail service unavailable; skipping %s", label)
            return
        await asyncio.to_thread(
            service.users().messages().send(
                userId="me", body=_encode_message(msg)
            ).execute
        )
        logger.info("%s sent to %s", label, to_email)
    except Exception:
        logger.exception("Failed to send %s to %s", label, to_email)


def _build_report_message(
    to_email: str,
    recipient_name: str,
    project_name: str,
    project_id: str,
    summary: dict[str, int],
    total_cost_usd: float | None,
) -> MIMEMultipart:
    """Build the report-ready email message."""
    msg = MIMEMultipart("alternative")
    msg["From"] = f"Pinscope <{settings.email_sender}>"
    msg["To"] = to_email
    msg["Subject"] = f"Report ready: {project_name}"

    # Plain text fallback
    report_url = f"{settings.email_frontend_url}/project/{project_id}/report"
    total = summary.get("total", 0)
    errors = summary.get("ERROR", 0)
    warnings = summary.get("WARNING", 0)
    infos = summary.get("INFO", 0)
    text_body = (
        f"Hi {recipient_name},\n\n"
        f"Your Pinscope validation report for \"{project_name}\" is ready.\n\n"
        f"Summary: {total} findings — {errors} errors, {warnings} warnings, {infos} info\n\n"
        f"View the report: {report_url}\n"
    )
    msg.attach(MIMEText(text_body, "plain"))

    html_body = _render_report_email(
        recipient_name, project_name, project_id,
        summary, total_cost_usd,
    )
    msg.attach(MIMEText(html_body, "html"))
    return msg


def _build_paused_message(
    to_email: str,
    recipient_name: str,
    project_name: str,
    project_id: str,
    last_completed: str | None,
    stage: str | None,
    balance: float,
    credits_needed_low: float,
) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["From"] = f"Pinscope <{settings.email_sender}>"
    msg["To"] = to_email
    msg["Subject"] = f"Paused: {project_name} is waiting for credits"

    project_url = f"{settings.email_frontend_url}/project/{project_id}"
    last_line = f"Last completed: {last_completed}." if last_completed else ""
    stage_line = f"Paused during: {stage}." if stage else ""

    text_body = (
        f"Hi {recipient_name},\n\n"
        f"Your Pinscope run for \"{project_name}\" paused because you're low on credits.\n\n"
        f"{last_line}\n{stage_line}\n\n"
        f"Current balance: {balance:.2f} credits\n"
        f"Credits needed to finish (est): {credits_needed_low:.2f}+\n\n"
        f"Top up and resume here: {project_url}\n"
    )
    msg.attach(MIMEText(text_body, "plain"))
    return msg


def _build_topup_failed_message(
    to_email: str, recipient_name: str,
    amount_usd: float, reason: str,
) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["From"] = f"Pinscope <{settings.email_sender}>"
    msg["To"] = to_email
    msg["Subject"] = "Pinscope: auto top-up failed"
    manage_url = f"{settings.email_frontend_url}/credits"
    text_body = (
        f"Hi {recipient_name},\n\n"
        f"We tried to auto top-up your Pinscope balance with "
        f"${amount_usd:.2f} but the charge failed.\n\n"
        f"Reason: {reason}\n\n"
        f"Auto top-up has been disabled until you update your payment method. "
        f"Update your card here: {manage_url}\n"
    )
    msg.attach(MIMEText(text_body, "plain"))
    return msg


async def send_topup_failed_email(
    user_id: str, *, amount_usd: float, reason: str,
) -> None:
    if not settings.use_email:
        return
    clerk_user = await _resolve_clerk_user(user_id)
    if not clerk_user:
        return
    emails = clerk_user.get("email_addresses", [])
    to_email = emails[0].get("email_address") if emails else None
    if not to_email:
        return
    first = clerk_user.get("first_name") or ""
    last = clerk_user.get("last_name") or ""
    name = f"{first} {last}".strip() or "there"
    msg = _build_topup_failed_message(to_email, name, amount_usd, reason)
    await _send_raw(to_email, msg, "Top-up-failed email")


def _build_low_balance_message(
    to_email: str, recipient_name: str, balance: float, threshold: float,
) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["From"] = f"Pinscope <{settings.email_sender}>"
    msg["To"] = to_email
    msg["Subject"] = "Pinscope: low credit balance"
    credits_url = f"{settings.email_frontend_url}/credits"
    text_body = (
        f"Hi {recipient_name},\n\n"
        f"Your Pinscope credit balance has dropped to "
        f"{balance:.2f} credits (below your threshold of {threshold:.2f}).\n\n"
        f"Top up here so your pipelines don't pause mid-run: {credits_url}\n"
    )
    msg.attach(MIMEText(text_body, "plain"))
    return msg


async def send_low_balance_email(
    user_id: str, *, balance: float, threshold: float,
) -> None:
    if not settings.use_email:
        return
    clerk_user = await _resolve_clerk_user(user_id)
    if not clerk_user:
        return
    emails = clerk_user.get("email_addresses", [])
    to_email = emails[0].get("email_address") if emails else None
    if not to_email:
        return
    first = clerk_user.get("first_name") or ""
    last = clerk_user.get("last_name") or ""
    name = f"{first} {last}".strip() or "there"
    msg = _build_low_balance_message(to_email, name, balance, threshold)
    await _send_raw(to_email, msg, "Low-balance email")


async def send_pipeline_paused_email(
    user_id: str,
    project_name: str,
    project_id: str,
    *,
    last_completed: str | None,
    stage: str | None,
    balance: float,
    credits_needed_low: float,
) -> None:
    """Send a 'pipeline paused, awaiting credits' email.  Fire-and-forget."""
    if not settings.use_email:
        return
    clerk_user = await _resolve_clerk_user(user_id)
    if not clerk_user:
        return
    emails = clerk_user.get("email_addresses", [])
    to_email = emails[0].get("email_address") if emails else None
    if not to_email:
        return
    first = clerk_user.get("first_name") or ""
    last = clerk_user.get("last_name") or ""
    recipient_name = f"{first} {last}".strip() or "there"

    msg = _build_paused_message(
        to_email, recipient_name, project_name, project_id,
        last_completed, stage, balance, credits_needed_low,
    )
    await _send_raw(to_email, msg, "Pipeline-paused email")


async def send_report_ready_email(
    user_id: str,
    project_name: str,
    project_id: str,
    summary: dict[str, int],
    total_cost_usd: float | None = None,
) -> None:
    """Send a 'report ready' email to the project creator. Fire-and-forget."""
    if not settings.use_email:
        return

    clerk_user = await _resolve_clerk_user(user_id)
    if not clerk_user:
        logger.warning("Cannot send report email: Clerk user %s not found", user_id)
        return

    emails = clerk_user.get("email_addresses", [])
    to_email = emails[0].get("email_address") if emails else None
    if not to_email:
        logger.warning("Cannot send report email: no email for Clerk user %s", user_id)
        return

    first = clerk_user.get("first_name") or ""
    last = clerk_user.get("last_name") or ""
    recipient_name = f"{first} {last}".strip() or "there"

    msg = _build_report_message(
        to_email, recipient_name, project_name, project_id,
        summary, total_cost_usd,
    )
    await _send_raw(to_email, msg, "Report-ready email")


async def send_test_email(to_email: str) -> dict:
    """Send a test email directly to the given address. Returns a status dict."""
    result: dict = {"ok": False, "step": "", "error": ""}

    if not settings.use_email:
        result["step"] = "config"
        result["error"] = f"use_email=False (email_sender={settings.email_sender!r}, email_frontend_url={settings.email_frontend_url!r})"
        return result

    result["step"] = "build_service"
    try:
        import google.auth
        import google.auth.transport.requests
        from google.auth import iam
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as e:
        result["error"] = f"Import failed: {e}"
        return result

    scopes = ["https://www.googleapis.com/auth/gmail.send"]
    try:
        source_credentials, _ = google.auth.default()
        cred_type = type(source_credentials).__name__

        if hasattr(source_credentials, "_signer"):
            delegated = source_credentials.with_subject(settings.email_sender)
            service = build("gmail", "v1", credentials=delegated, cache_discovery=False)
        else:
            req = google.auth.transport.requests.Request()
            source_credentials.refresh(req)
            sa_email = source_credentials.service_account_email
            signer = iam.Signer(request=req, credentials=source_credentials, service_account_email=sa_email)
            credentials = service_account.Credentials(
                signer=signer,
                service_account_email=sa_email,
                token_uri="https://oauth2.googleapis.com/token",
                scopes=scopes,
                subject=settings.email_sender,
            )
            service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
            cred_type = f"{cred_type} → IAM signer sa={sa_email}"

        result["step"] = "send"
        msg = MIMEMultipart("alternative")
        msg["From"] = f"Pinscope <{settings.email_sender}>"
        msg["To"] = to_email
        msg["Subject"] = "Pinscope email test"
        msg.attach(MIMEText(f"Test email from Pinscope. Sender: {settings.email_sender}. Creds: {cred_type}", "plain"))

        import asyncio as _asyncio
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        await _asyncio.to_thread(
            service.users().messages().send(userId="me", body={"raw": raw}).execute
        )
        result["ok"] = True
        result["step"] = "sent"
        result["error"] = ""
        logger.info("Test email sent to %s via %s", to_email, cred_type)
    except Exception as exc:
        result["error"] = str(exc)
        logger.exception("Test email failed at step=%s", result["step"])

    return result


async def send_pipeline_started_email(
    user_id: str,
    project_name: str,
    project_id: str,
    num_components: int,
    num_nets: int,
    num_ics: int,
    num_passives: int,
    num_simple: int,
) -> None:
    """Send a 'pipeline started' email to the admin. Fire-and-forget."""
    if not settings.use_email or not settings.email_admin_notify:
        return

    # Resolve creator info from Clerk
    creator_name = "Unknown"
    creator_email = "unknown"
    clerk_user = await _resolve_clerk_user(user_id)
    if clerk_user:
        first = clerk_user.get("first_name") or ""
        last = clerk_user.get("last_name") or ""
        creator_name = f"{first} {last}".strip() or "Unknown"
        emails = clerk_user.get("email_addresses", [])
        creator_email = emails[0].get("email_address", "unknown") if emails else "unknown"

    to_email = settings.email_admin_notify

    # Build message
    msg = MIMEMultipart("alternative")
    msg["From"] = f"Pinscope <{settings.email_sender}>"
    msg["To"] = to_email
    msg["Subject"] = f"Pipeline started: {project_name} ({num_components} components)"

    text_body = (
        f"Pipeline started for \"{project_name}\"\n\n"
        f"Created by: {creator_name} ({creator_email})\n"
        f"Components: {num_components} ({num_ics} ICs, {num_passives} passives, {num_simple} discrete)\n"
        f"Nets: {num_nets}\n\n"
        f"View project: {settings.email_frontend_url}/project/{project_id}\n"
    )
    msg.attach(MIMEText(text_body, "plain"))

    html_body = _render_pipeline_started_email(
        creator_name, creator_email, project_name, project_id,
        num_components, num_nets, num_ics, num_passives, num_simple,
    )
    msg.attach(MIMEText(html_body, "html"))

    await _send_raw(to_email, msg, "Pipeline-started email")


# ---------------------------------------------------------------------------
# Feedback received (admin notification)
# ---------------------------------------------------------------------------


_FEEDBACK_TYPE_LABELS = {
    "bug": "Bug report",
    "rule_feedback": "Finding feedback",
    "feature_request": "Feature request",
}

_FEEDBACK_TYPE_COLORS = {
    "bug": "#ef4444",
    "rule_feedback": "#f59e0b",
    "feature_request": "#3b82f6",
}


def _esc(s: str | None) -> str:
    """Minimal HTML escape so user text can't break the template."""
    if s is None:
        return ""
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _render_feedback_email(
    ticket_id: str,
    feedback_type: str,
    type_label: str,
    type_color: str,
    submitter_name: str,
    submitter_email: str,
    project_name: str | None,
    project_id: str | None,
    finding_designator: str | None,
    finding_mpn: str | None,
    finding_status: str | None,
    finding_text: str | None,
    message: str,
) -> str:
    admin_url = f"{settings.email_frontend_url}/admin?tab=feedback"

    project_row = ""
    if project_name:
        project_link = (
            f"{settings.email_frontend_url}/project/{project_id}"
            if project_id else ""
        )
        project_value = (
            f'<a href="{project_link}" style="color: #111827; text-decoration: none;">{_esc(project_name)}</a>'
            if project_link else _esc(project_name)
        )
        project_row = f"""
                <tr><td style="padding: 6px 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 14px; color: #6b7280;">
                  <span style="display: inline-block; min-width: 90px; color: #9ca3af;">Project</span>
                  <span style="color: #111827;">{project_value}</span>
                </td></tr>"""

    finding_rows = ""
    if finding_designator or finding_mpn or finding_status:
        bits = []
        if finding_designator:
            bits.append(f'<span style="font-family: ui-monospace, monospace; color: #111827;">{_esc(finding_designator)}</span>')
        if finding_mpn:
            bits.append(f'<span style="font-family: ui-monospace, monospace; color: #6b7280;">{_esc(finding_mpn)}</span>')
        if finding_status:
            bits.append(f'<span style="text-transform: uppercase; font-size: 11px; font-weight: 600; color: #6b7280;">{_esc(finding_status)}</span>')
        finding_rows = f"""
                <tr><td style="padding: 6px 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 14px;">
                  <span style="display: inline-block; min-width: 90px; color: #9ca3af;">Finding</span>
                  {' &middot; '.join(bits)}
                </td></tr>"""

    finding_text_block = ""
    if finding_text:
        finding_text_block = f"""
            <tr><td style="padding-top: 16px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f9fafb; border-radius: 8px; border: 1px solid #e5e7eb;">
                <tr><td style="padding: 14px 18px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 13px; color: #4b5563; white-space: pre-wrap;">
                  <span style="font-size: 11px; font-weight: 600; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.05em; display: block; padding-bottom: 6px;">Finding text</span>
                  {_esc(finding_text)}
                </td></tr>
              </table>
            </td></tr>"""

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f3f4f6; -webkit-font-smoothing: antialiased;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f3f4f6;">
    <tr><td align="center" style="padding: 40px 16px;">
      <table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width: 600px; width: 100%; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">

        <!-- Header -->
        <tr><td style="background-color: #111827; padding: 28px 32px;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 20px; font-weight: 700; color: #ffffff; letter-spacing: -0.025em;">
                Pinscope
              </td>
              <td align="right" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 12px; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.05em;">
                Feedback Received
              </td>
            </tr>
          </table>
        </td></tr>

        <!-- Body -->
        <tr><td style="padding: 32px;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">

            <!-- Type badge -->
            <tr><td style="padding-bottom: 20px;">
              <table cellpadding="0" cellspacing="0" border="0" style="background-color: {type_color}; border-radius: 6px;">
                <tr><td style="padding: 8px 16px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 14px; font-weight: 600; color: #ffffff;">
                  {type_label}
                </td></tr>
              </table>
            </td></tr>

            <!-- Submitter + context card -->
            <tr><td>
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f0f9ff; border-radius: 8px; border: 1px solid #bae6fd;">
                <tr><td style="padding: 16px 20px;">
                  <table width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 11px; font-weight: 600; color: #0369a1; text-transform: uppercase; letter-spacing: 0.05em; padding-bottom: 8px;">
                      Submitted by
                    </td></tr>
                    <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 16px; font-weight: 600; color: #111827;">
                      {_esc(submitter_name)}
                    </td></tr>
                    <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 14px; color: #6b7280; padding-top: 2px;">
                      {_esc(submitter_email)}
                    </td></tr>
                  </table>
                </td></tr>
              </table>
            </td></tr>

            <!-- Context (project + finding) -->
            <tr><td style="padding-top: 16px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0">
                {project_row}
                {finding_rows}
              </table>
            </td></tr>

            {finding_text_block}

            <!-- Message body -->
            <tr><td style="padding-top: 20px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px;">
                <tr><td style="padding: 18px 22px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 15px; color: #111827; line-height: 1.55; white-space: pre-wrap;">
                  {_esc(message)}
                </td></tr>
              </table>
            </td></tr>

            <!-- CTA button -->
            <tr><td align="center" style="padding-top: 28px; padding-bottom: 8px;">
              <!--[if mso]>
              <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" href="{admin_url}" style="height:48px;v-text-anchor:middle;width:220px;" arcsize="14%" fillcolor="#3b82f6" stroke="f">
                <w:anchorlock/>
                <center style="color:#ffffff;font-family:sans-serif;font-size:15px;font-weight:bold;">Open in admin &rarr;</center>
              </v:roundrect>
              <![endif]-->
              <!--[if !mso]><!-->
              <a href="{admin_url}" target="_blank" style="display: inline-block; background-color: #3b82f6; color: #ffffff; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 15px; font-weight: 600; text-decoration: none; padding: 12px 32px; border-radius: 8px; letter-spacing: -0.01em;">
                Open in admin &rarr;
              </a>
              <!--<![endif]-->
            </td></tr>

            <!-- Ticket id -->
            <tr><td align="center" style="padding-top: 14px; font-family: ui-monospace, monospace; font-size: 11px; color: #9ca3af;">
              Ticket {_esc(ticket_id)}
            </td></tr>

          </table>
        </td></tr>

        <!-- Footer -->
        <tr><td style="background-color: #f9fafb; padding: 20px 32px; border-top: 1px solid #e5e7eb;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 12px; color: #9ca3af;">
              Pinscope &middot; Agentic schematic validation
            </td></tr>
          </table>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


async def send_feedback_received_email(
    ticket_id: str,
    user_id: str,
    feedback_type: str,
    message: str,
    *,
    submitter_name: str | None = None,
    submitter_email: str | None = None,
    project_name: str | None = None,
    project_id: str | None = None,
    finding_designator: str | None = None,
    finding_mpn: str | None = None,
    finding_status: str | None = None,
    finding_text: str | None = None,
) -> None:
    """Notify the admin inbox that a new feedback ticket landed. Fire-and-forget."""
    if not settings.use_email or not settings.email_admin_notify:
        return

    # Fill in submitter info from Clerk when the client didn't pass it.
    name = (submitter_name or "").strip()
    email = (submitter_email or "").strip()
    if not name or not email:
        clerk_user = await _resolve_clerk_user(user_id)
        if clerk_user:
            if not name:
                first = clerk_user.get("first_name") or ""
                last = clerk_user.get("last_name") or ""
                name = f"{first} {last}".strip()
            if not email:
                emails = clerk_user.get("email_addresses", [])
                email = emails[0].get("email_address", "") if emails else ""
    name = name or "Unknown user"
    email = email or user_id

    type_label = _FEEDBACK_TYPE_LABELS.get(feedback_type, feedback_type)
    type_color = _FEEDBACK_TYPE_COLORS.get(feedback_type, "#6b7280")

    to_email = settings.email_admin_notify
    subject_ctx = project_name or "general"
    msg = MIMEMultipart("alternative")
    msg["From"] = f"Pinscope <{settings.email_sender}>"
    msg["To"] = to_email
    msg["Subject"] = f"Feedback ({type_label}): {subject_ctx}"

    # Plain text fallback
    lines = [
        f"{type_label} from {name} <{email}>",
    ]
    if project_name:
        lines.append(f"Project: {project_name}")
    if finding_designator or finding_mpn or finding_status:
        finding_bits = " · ".join(
            x for x in (finding_designator, finding_mpn, finding_status) if x
        )
        lines.append(f"Finding: {finding_bits}")
    if finding_text:
        lines.append(f"Finding text: {finding_text}")
    lines.append("")
    lines.append(message)
    lines.append("")
    lines.append(f"Open in admin: {settings.email_frontend_url}/admin?tab=feedback")
    lines.append(f"Ticket: {ticket_id}")
    msg.attach(MIMEText("\n".join(lines), "plain"))

    html_body = _render_feedback_email(
        ticket_id=ticket_id,
        feedback_type=feedback_type,
        type_label=type_label,
        type_color=type_color,
        submitter_name=name,
        submitter_email=email,
        project_name=project_name,
        project_id=project_id,
        finding_designator=finding_designator,
        finding_mpn=finding_mpn,
        finding_status=finding_status,
        finding_text=finding_text,
        message=message,
    )
    msg.attach(MIMEText(html_body, "html"))

    await _send_raw(to_email, msg, "Feedback-received email")


# ---------------------------------------------------------------------------
# Feedback reply (notify the original submitter)
# ---------------------------------------------------------------------------


def _render_feedback_reply_email(
    recipient_first_name: str,
    project_name: str | None,
    finding_designator: str | None,
    finding_mpn: str | None,
    original_message: str,
    reply_text: str,
) -> str:
    feedback_url = f"{settings.email_frontend_url}/feedback"

    context_line = ""
    if project_name:
        finding_bits = " · ".join(
            x for x in (finding_designator, finding_mpn) if x
        )
        context_suffix = f" on <span style=\"font-family: ui-monospace, monospace;\">{_esc(finding_bits)}</span>" if finding_bits else ""
        context_line = f"""
            <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 14px; color: #6b7280; padding-bottom: 18px;">
              In response to your feedback on <strong style="color: #111827;">{_esc(project_name)}</strong>{context_suffix}.
            </td></tr>"""
    else:
        context_line = """
            <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 14px; color: #6b7280; padding-bottom: 18px;">
              In response to the feedback you shared.
            </td></tr>"""

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
<body style="margin: 0; padding: 0; background-color: #f3f4f6; -webkit-font-smoothing: antialiased;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f3f4f6;">
    <tr><td align="center" style="padding: 40px 16px;">
      <table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width: 600px; width: 100%; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">

        <!-- Header -->
        <tr><td style="background-color: #111827; padding: 28px 32px;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 20px; font-weight: 700; color: #ffffff; letter-spacing: -0.025em;">
                Pinscope
              </td>
              <td align="right" style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 12px; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.05em;">
                New Reply
              </td>
            </tr>
          </table>
        </td></tr>

        <!-- Body -->
        <tr><td style="padding: 32px;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">

            <!-- Greeting -->
            <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 15px; color: #374151; padding-bottom: 8px;">
              Hi {_esc(recipient_first_name)},
            </td></tr>
            <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 15px; color: #374151; padding-bottom: 18px;">
              The Pinscope team just replied to your feedback.
            </td></tr>

            {context_line}

            <!-- Reply card -->
            <tr><td>
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #ecfdf5; border-radius: 8px; border: 1px solid #a7f3d0;">
                <tr><td style="padding: 18px 22px;">
                  <table width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 11px; font-weight: 600; color: #047857; text-transform: uppercase; letter-spacing: 0.05em; padding-bottom: 10px;">
                      Pinscope team
                    </td></tr>
                    <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 15px; color: #064e3b; line-height: 1.55; white-space: pre-wrap;">
                      {_esc(reply_text)}
                    </td></tr>
                  </table>
                </td></tr>
              </table>
            </td></tr>

            <!-- Original feedback (quoted) -->
            <tr><td style="padding-top: 18px;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f9fafb; border-radius: 8px; border: 1px solid #e5e7eb;">
                <tr><td style="padding: 14px 18px;">
                  <table width="100%" cellpadding="0" cellspacing="0" border="0">
                    <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 11px; font-weight: 600; color: #9ca3af; text-transform: uppercase; letter-spacing: 0.05em; padding-bottom: 8px;">
                      Your original message
                    </td></tr>
                    <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 13px; color: #4b5563; line-height: 1.5; white-space: pre-wrap;">
                      {_esc(original_message)}
                    </td></tr>
                  </table>
                </td></tr>
              </table>
            </td></tr>

            <!-- CTA button -->
            <tr><td align="center" style="padding-top: 28px; padding-bottom: 20px;">
              <!--[if mso]>
              <v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" href="{feedback_url}" style="height:48px;v-text-anchor:middle;width:240px;" arcsize="14%" fillcolor="#3b82f6" stroke="f">
                <w:anchorlock/>
                <center style="color:#ffffff;font-family:sans-serif;font-size:15px;font-weight:bold;">View in Pinscope &rarr;</center>
              </v:roundrect>
              <![endif]-->
              <!--[if !mso]><!-->
              <a href="{feedback_url}" target="_blank" style="display: inline-block; background-color: #3b82f6; color: #ffffff; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 15px; font-weight: 600; text-decoration: none; padding: 12px 32px; border-radius: 8px; letter-spacing: -0.01em;">
                View in Pinscope &rarr;
              </a>
              <!--<![endif]-->
            </td></tr>

            <!-- Thanks -->
            <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 15px; color: #374151; line-height: 1.55; padding-top: 8px;">
              Thank you so much for taking the time to share your feedback — we truly value it.
            </td></tr>
            <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 15px; color: #374151; padding-top: 6px;">
              — The Pinscope team
            </td></tr>

          </table>
        </td></tr>

        <!-- Footer -->
        <tr><td style="background-color: #f9fafb; padding: 20px 32px; border-top: 1px solid #e5e7eb;">
          <table width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr><td style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; font-size: 12px; color: #9ca3af;">
              Pinscope &middot; Agentic schematic validation
            </td></tr>
          </table>
        </td></tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


async def send_feedback_reply_email(
    user_id: str,
    reply_text: str,
    original_message: str,
    *,
    recipient_name: str | None = None,
    recipient_email: str | None = None,
    project_name: str | None = None,
    finding_designator: str | None = None,
    finding_mpn: str | None = None,
) -> None:
    """Notify the original submitter that the Pinscope team replied. Fire-and-forget."""
    if not settings.use_email:
        return

    full_name = (recipient_name or "").strip()
    to_email = (recipient_email or "").strip()
    if not full_name or not to_email:
        clerk_user = await _resolve_clerk_user(user_id)
        if clerk_user:
            if not full_name:
                first = clerk_user.get("first_name") or ""
                last = clerk_user.get("last_name") or ""
                full_name = f"{first} {last}".strip()
            if not to_email:
                emails = clerk_user.get("email_addresses", [])
                to_email = emails[0].get("email_address", "") if emails else ""

    if not to_email:
        logger.warning(
            "Cannot send feedback-reply email: no email for user %s", user_id
        )
        return

    first_name = full_name.split()[0] if full_name else "there"

    msg = MIMEMultipart("alternative")
    msg["From"] = f"Pinscope <{settings.email_sender}>"
    msg["To"] = to_email
    msg["Subject"] = "The Pinscope team replied to your feedback"

    # Plain text fallback
    text_lines = [
        f"Hi {first_name},",
        "",
        "The Pinscope team just replied to your feedback.",
        "",
        "— Reply —",
        reply_text,
        "",
        "— Your original message —",
        original_message,
        "",
        f"View in Pinscope: {settings.email_frontend_url}/feedback",
        "",
        "Thank you so much for taking the time to share your feedback — we truly value it.",
        "— The Pinscope team",
    ]
    msg.attach(MIMEText("\n".join(text_lines), "plain"))

    html_body = _render_feedback_reply_email(
        recipient_first_name=first_name,
        project_name=project_name,
        finding_designator=finding_designator,
        finding_mpn=finding_mpn,
        original_message=original_message,
        reply_text=reply_text,
    )
    msg.attach(MIMEText(html_body, "html"))

    await _send_raw(to_email, msg, "Feedback-reply email")
