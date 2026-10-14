"""Outgoing email for notification delivery.

Sends a plain-text email for each in-app notification. Delivery is
best-effort by design:

- If SMTP is not configured (no SMTP_HOST/USERNAME/PASSWORD), sending is
  skipped silently — in-app notifications still work.
- Sending happens in a daemon thread so the HTTP request that triggered the
  workflow never waits on the mail server.
- Any failure (bad credentials, network, invalid address) is logged and
  swallowed — email must never break a KPI workflow.

For Gmail: SMTP_HOST=smtp.gmail.com, SMTP_PORT=587, SMTP_USERNAME=your Gmail
address, SMTP_PASSWORD=a Gmail App Password (created at Google Account →
Security → 2-Step Verification → App passwords).
"""

import logging
import smtplib
import threading
from email.message import EmailMessage

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.notification import Notification
from app.models.user import User

logger = logging.getLogger("kpi_system")


def _build_message(to_email: str, subject: str, body: str, link: str | None) -> EmailMessage:
    full_body = body
    if link and settings.APP_BASE_URL:
        full_body += f"\n\nView: {settings.APP_BASE_URL.rstrip('/')}{link}"
    elif link:
        full_body += f"\n\nView: {link}"
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to_email
    msg["Subject"] = f"[KPI] {subject}"
    msg.set_content(full_body)
    return msg


def _send_one(to_email: str, subject: str, body: str, link: str | None) -> None:
    msg = _build_message(to_email, subject, body, link)
    if settings.SMTP_USE_TLS:
        smtp: smtplib.SMTP = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
        smtp.starttls()
    else:
        smtp = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
    try:
        smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        smtp.send_message(msg)
    finally:
        try:
            smtp.quit()
        except Exception:
            pass


def _run_sender(payloads: list[tuple[str, str, str, str | None]]) -> None:
    for to_email, subject, body, link in payloads:
        try:
            _send_one(to_email, subject, body, link)
        except Exception:
            logger.exception("Failed to send notification email to %s", to_email)


def _send_in_background(payloads: list[tuple[str, str, str, str | None]]) -> None:
    """payloads: list of (to_email, subject, body, link). Runs in a daemon thread."""
    thread = threading.Thread(target=_run_sender, args=(payloads,), daemon=True, name="kpi-email-sender")
    thread.start()


def send_for_notifications(db: Session, notifications: list[Notification]) -> None:
    """Email each notification's recipient. Call AFTER the workflow's db.commit().

    Best-effort: does nothing when SMTP is unconfigured, never raises.
    Only plain data is extracted here — the actual SMTP work happens in a
    background thread with no database access.
    """
    if not settings.smtp_configured or not notifications:
        return
    try:
        payloads: list[tuple[str, str, str, str | None]] = []
        for n in notifications:
            user = db.get(User, n.user_id)
            if user is None or not user.email or not user.is_active:
                continue
            if not user.email_notifications:
                # User opted out of notification emails; in-app copy is unaffected.
                continue
            payloads.append((user.email, n.title, n.message, n.link))
    except Exception:
        logger.exception("Failed to prepare notification emails")
        return
    if payloads:
        _send_in_background(payloads)


def send_welcome_email(to_email: str, name: str, password: str, org_name: str | None = None) -> None:
    """Send a new user their login details. Call AFTER the user row is committed.

    Best-effort: does nothing when SMTP is unconfigured, never raises.
    """
    if not settings.smtp_configured:
        return
    if settings.APP_BASE_URL:
        login_url = f"{settings.APP_BASE_URL.rstrip('/')}/login"
    else:
        login_url = "the KPI login page"
    org_line = f"Organization: {org_name}\n" if org_name else ""
    body = (
        f"Hi {name},\n\n"
        f"Your KPI account has been created.\n\n"
        f"{org_line}"
        f"Login: {login_url}\n"
        f"Email: {to_email}\n"
        f"Password: {password}\n\n"
        f"Please change your password after your first login "
        f"(Account -> Change Password)."
    )
    _send_in_background([(to_email, "Your KPI account is ready", body, None)])
