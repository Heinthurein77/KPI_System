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
    background thread with no database access. Fine for web requests (the
    process lives on), but NOT for cron jobs: a daemon thread can be
    killed when the short-lived job process exits, silently losing the
    email while the notification row already marks the user reminded.
    Cron paths must use send_for_notifications_sync instead.
    """
    if not settings.smtp_configured or not notifications:
        return
    payloads = _notification_payloads(db, notifications)
    if payloads:
        _send_in_background(payloads)


def _notification_payloads(db: Session, notifications: list[Notification]) -> list[tuple[str, str, str, str | None]]:
    """Extract (to_email, subject, body, link) for emailable notifications."""
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
        return []
    return payloads


def send_for_notifications_sync(db: Session, notifications: list[Notification]) -> list[str]:
    """Synchronously email each notification's recipient; returns the list
    of recipient emails that FAILED to send (empty when all succeeded).

    This is the cron path: the caller waits for delivery, so an email is
    never silently lost to a dying daemon thread, and the caller can
    retry the failures on the next run (e.g. by deleting the
    corresponding notification rows, which are the idempotency marker).
    Does nothing when SMTP is unconfigured (returns [] — nothing to retry).
    """
    if not settings.smtp_configured or not notifications:
        return []
    payloads = _notification_payloads(db, notifications)
    failed: list[str] = []
    for to_email, subject, body, link in payloads:
        try:
            _send_one(to_email, subject, body, link)
        except Exception:
            logger.exception("Failed to send notification email to %s", to_email)
            failed.append(to_email)
    return failed


def send_welcome_email(to_email: str, name: str, org_name: str | None = None) -> None:
    """Tell a new user their account is ready. Call AFTER the user row is committed.

    Best-effort: does nothing when SMTP is unconfigured, never raises.

    The email deliberately carries NO password: credentials must never
    travel by email (or in an API response). The admin who created the
    account hands the initial password to the user out of band (for
    manual creation they chose it; for bulk import they set one per user
    with Reset Password), and the account forces a password change on
    first login.
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
        f"Email: {to_email}\n\n"
        f"Please ask your department admin for your initial password, "
        f"then change it after your first login "
        f"(Account -> Change Password)."
    )
    _send_in_background([(to_email, "Your KPI account is ready", body, None)])
