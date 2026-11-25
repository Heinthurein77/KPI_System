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
    # M-39: a notification title (admin/user supplied) reaches the Subject
    # header — strip CR/LF so a crafted title can never inject extra
    # headers (header injection). The body is set via set_content, which
    # already handles newlines safely.
    subject = subject.replace("\r", " ").replace("\n", " ")
    full_body = body
    # S-m2: config documents "Unset → emails omit the link" — only append
    # the View link when a base URL is configured. A bare relative path
    # ("View: /dashboard?...") is unclickable in an email.
    if link and settings.APP_BASE_URL:
        full_body += f"\n\nView: {settings.APP_BASE_URL.rstrip('/')}{link}"
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


def _welcome_body(to_email: str, name: str, org_name: str | None, setup_token: str | None) -> str:
    """Body for a new-user welcome email.

    S-05 (security review 2026-11-25): the email NEVER carries a
    password. Instead it carries a one-time set-password link (valid 72h)
    when both a token and APP_BASE_URL are configured — the link is
    single-use: setting the password clears must_change_password, which
    invalidates the token. Without APP_BASE_URL there is no link to give,
    so the admin hands the user their sign-in details out of band (same
    as the reset flow); the password is still never written here.
    """
    org_line = f"Organization: {org_name}\n" if org_name else ""
    if setup_token and settings.APP_BASE_URL:
        setup_line = (
            f"Set your password: {settings.APP_BASE_URL.rstrip('/')}/setup-password?token={setup_token}\n"
            f"(This link expires in 72 hours and works once. After setting your password, sign in at the link below.)\n"
        )
    else:
        setup_line = "Your admin will give you your sign-in details.\n"
    login_line = (
        f"Login: {settings.APP_BASE_URL.rstrip('/')}/login\n"
        if settings.APP_BASE_URL
        else ""
    )
    return (
        f"Hi {name},\n\n"
        f"Your KPI account has been created.\n\n"
        f"{org_line}"
        f"{setup_line}\n"
        f"{login_line}"
        f"Email: {to_email}\n\n"
        f"Please change your password after your first login "
        f"(Account -> Change Password) if your admin gave you one."
    )


def send_welcome_email(
    to_email: str, name: str, org_name: str | None = None, setup_token: str | None = None
) -> None:
    """Tell a new user their account is ready. Call AFTER the user row is committed.

    Best-effort: does nothing when SMTP is unconfigured, never raises.

    S-05: the email carries a one-time set-password link, never a
    password — see _welcome_body. Callers that need to know whether
    delivery succeeded must use send_welcome_email_sync instead — this
    background variant swallows failures by design.
    """
    if not settings.smtp_configured:
        return
    body = _welcome_body(to_email, name, org_name, setup_token)
    _send_in_background([(to_email, "Your KPI account is ready", body, None)])


def send_welcome_email_sync(
    to_email: str, name: str, org_name: str | None = None, setup_token: str | None = None
) -> bool:
    """Synchronously send a new-user welcome email; returns True on success.

    Returns False (and logs) when SMTP is unconfigured or delivery fails —
    it never raises, so a failed welcome email can never roll back the
    user creation that already committed. Callers surface the False
    result to the admin (bulk import reports it per user in the result;
    manual creation logs it).
    """
    if not settings.smtp_configured:
        logger.warning("Welcome email to %s skipped: SMTP is not configured", to_email)
        return False
    try:
        _send_one(
            to_email,
            "Your KPI account is ready",
            _welcome_body(to_email, name, org_name, setup_token),
            None,
        )
    except Exception:
        logger.exception("Failed to send welcome email to %s", to_email)
        return False
    return True
