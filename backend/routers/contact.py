"""Public contact form endpoint — no authentication required."""

from __future__ import annotations

import html
import logging
import time

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import APIRouter, Request
from pydantic import BaseModel, EmailStr, Field

from backend.config import settings
from backend.services.email import _send_raw

logger = logging.getLogger(__name__)

router = APIRouter()

# Simple in-memory rate limiting (per-instance, resets on deploy)
_recent: dict[str, float] = {}
_RATE_LIMIT_SECONDS = 60


class ContactRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr = Field(..., max_length=254)
    message: str = Field(..., min_length=1, max_length=5000)
    company: str = Field("", max_length=200)
    subject: str = Field("", max_length=200)
    honeypot: str = Field("", alias="_honey")


class ContactResponse(BaseModel):
    success: bool
    message: str


def _build_contact_message(data: ContactRequest) -> MIMEMultipart:
    """Build the contact form email."""
    msg = MIMEMultipart("alternative")
    msg["From"] = f"Pinscope <{settings.email_sender}>"
    msg["To"] = settings.contact_recipient
    msg["Reply-To"] = data.email
    msg["Subject"] = f"[Pinscope Contact] {data.subject or 'New message'} from {data.name}"

    # Plain text
    lines = [
        f"Name: {data.name}",
        f"Email: {data.email}",
    ]
    if data.company:
        lines.append(f"Company: {data.company}")
    if data.subject:
        lines.append(f"Subject: {data.subject}")
    lines += ["", data.message, "", "— Sent from the Pinscope contact form"]
    msg.attach(MIMEText("\n".join(lines), "plain"))

    # HTML
    name = html.escape(data.name)
    email = html.escape(data.email)
    company = html.escape(data.company)
    subject = html.escape(data.subject)
    message = html.escape(data.message)

    rows = f"""\
        <tr>
          <td style="padding: 8px 12px; border: 1px solid #e5e5e5; font-weight: 600; width: 100px;">Name</td>
          <td style="padding: 8px 12px; border: 1px solid #e5e5e5;">{name}</td>
        </tr>
        <tr>
          <td style="padding: 8px 12px; border: 1px solid #e5e5e5; font-weight: 600;">Email</td>
          <td style="padding: 8px 12px; border: 1px solid #e5e5e5;"><a href="mailto:{email}">{email}</a></td>
        </tr>"""
    if data.company:
        rows += f"""\
        <tr>
          <td style="padding: 8px 12px; border: 1px solid #e5e5e5; font-weight: 600;">Company</td>
          <td style="padding: 8px 12px; border: 1px solid #e5e5e5;">{company}</td>
        </tr>"""
    if data.subject:
        rows += f"""\
        <tr>
          <td style="padding: 8px 12px; border: 1px solid #e5e5e5; font-weight: 600;">Subject</td>
          <td style="padding: 8px 12px; border: 1px solid #e5e5e5;">{subject}</td>
        </tr>"""

    html_body = f"""\
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 560px; margin: 0 auto; padding: 24px;">
      <h2 style="font-size: 18px; margin: 0 0 16px;">New contact form submission</h2>
      <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
        {rows}
      </table>
      <div style="margin-top: 16px; padding: 16px; background: #f9fafb; border-radius: 8px; font-size: 14px; line-height: 1.6; white-space: pre-wrap;">{message}</div>
      <p style="margin-top: 24px; font-size: 12px; color: #888;">Sent from the Pinscope contact form</p>
    </div>"""
    msg.attach(MIMEText(html_body, "html"))

    return msg


@router.post("/contact", response_model=ContactResponse)
async def submit_contact(data: ContactRequest, request: Request):
    # Honeypot check — bots fill hidden fields
    if data.honeypot:
        return ContactResponse(success=True, message="Message sent! We'll get back to you soon.")

    # Rate limiting by IP
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or request.client.host
    now = time.time()
    last = _recent.get(ip)
    if last and now - last < _RATE_LIMIT_SECONDS:
        return ContactResponse(success=False, message="Please wait a minute before submitting again.")
    _recent[ip] = now

    # Clean up old entries
    if len(_recent) > 1000:
        cutoff = now - _RATE_LIMIT_SECONDS
        for key in [k for k, v in _recent.items() if v < cutoff]:
            del _recent[key]

    # Check email is configured
    if not settings.use_email or not settings.contact_recipient:
        logger.warning("Contact form submitted but email is not configured")
        return ContactResponse(
            success=False,
            message="Email is not configured on this server.",
        )

    msg = _build_contact_message(data)
    await _send_raw(settings.contact_recipient, msg, "Contact form")

    return ContactResponse(success=True, message="Message sent! We'll get back to you soon.")
