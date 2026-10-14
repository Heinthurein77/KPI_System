"""Tests for email notification delivery.

Email is best-effort: skipped silently when SMTP is unconfigured, and never
raises — a mail failure must not break a KPI workflow.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.models.notification import Notification
from app.models.user import UserRole
from app.services import email_service
from app.tests.factories import make_department, make_tenant, make_user


def _make_notification(db, tenant, user):
    n = Notification(
        tenant_id=tenant.id,
        user_id=user.id,
        type="kpi_submitted",
        title="Test title",
        message="Test message",
        link="/dashboard?year=2026&period=October",
    )
    db.add(n)
    db.commit()
    return n


def _configured_settings(**overrides):
    s = MagicMock()
    s.smtp_configured = True
    s.SMTP_HOST = "smtp.gmail.com"
    s.SMTP_PORT = 587
    s.SMTP_USERNAME = "sender@gmail.com"
    s.SMTP_PASSWORD = "app-password"
    s.SMTP_USE_TLS = True
    s.smtp_from = "sender@gmail.com"
    s.APP_BASE_URL = "https://kpi.example.com"
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def test_send_skipped_when_smtp_not_configured(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    user = make_user(db, tenant, UserRole.EMPLOYEE, department=dept, email="real@example.com")
    n = _make_notification(db, tenant, user)

    s = _configured_settings()
    s.smtp_configured = False
    with patch.object(email_service, "settings", s):
        with patch("smtplib.SMTP") as mock_smtp:
            email_service.send_for_notifications(db, [n])  # must not raise
            mock_smtp.assert_not_called()


def test_send_email_via_smtp(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    user = make_user(db, tenant, UserRole.EMPLOYEE, department=dept, email="real@example.com")
    n = _make_notification(db, tenant, user)

    with patch.object(email_service, "settings", _configured_settings()):
        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp = mock_smtp_cls.return_value
            # Run the sender inline instead of in a daemon thread.
            with patch.object(
                email_service,
                "_send_in_background",
                side_effect=lambda payloads: email_service._run_sender(payloads),
            ):
                email_service.send_for_notifications(db, [n])

            mock_smtp_cls.assert_called_once_with("smtp.gmail.com", 587, timeout=15)
            mock_smtp.starttls.assert_called_once()
            mock_smtp.login.assert_called_once_with("sender@gmail.com", "app-password")
            mock_smtp.send_message.assert_called_once()
            sent = mock_smtp.send_message.call_args[0][0]
            assert sent["To"] == "real@example.com"
            assert sent["Subject"] == "[KPI] Test title"
            assert "Test message" in sent.get_content()
            assert "https://kpi.example.com/dashboard?year=2026&period=October" in sent.get_content()


def test_send_failure_never_raises(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    user = make_user(db, tenant, UserRole.EMPLOYEE, department=dept, email="real@example.com")
    n = _make_notification(db, tenant, user)

    with patch.object(email_service, "settings", _configured_settings()):
        with patch("smtplib.SMTP", side_effect=OSError("network down")):
            # Run the sender inline so the exception path is exercised.
            with patch.object(
                email_service,
                "_send_in_background",
                side_effect=lambda payloads: email_service._run_sender(payloads),
            ):
                email_service.send_for_notifications(db, [n])  # must not raise


def test_inactive_user_gets_no_email(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    user = make_user(db, tenant, UserRole.EMPLOYEE, department=dept, email="real@example.com", is_active=False)
    n = _make_notification(db, tenant, user)

    with patch.object(email_service, "settings", _configured_settings()):
        with patch.object(email_service, "_send_in_background") as mock_bg:
            email_service.send_for_notifications(db, [n])
            mock_bg.assert_not_called()


def test_send_welcome_email_content(db):
    with patch.object(email_service, "settings", _configured_settings()):
        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp = mock_smtp_cls.return_value
            with patch.object(
                email_service,
                "_send_in_background",
                side_effect=lambda payloads: email_service._run_sender(payloads),
            ):
                email_service.send_welcome_email("new@example.com", "New Hire", "Secret123!")

            mock_smtp.send_message.assert_called_once()
            sent = mock_smtp.send_message.call_args[0][0]
            assert sent["To"] == "new@example.com"
            assert sent["Subject"] == "[KPI] Your KPI account is ready"
            body = sent.get_content()
            assert "https://kpi.example.com/login" in body
            assert "new@example.com" in body
            assert "Secret123!" in body
            assert "New Hire" in body


def test_send_welcome_email_skipped_when_unconfigured():
    s = _configured_settings()
    s.smtp_configured = False
    with patch.object(email_service, "settings", s):
        with patch("smtplib.SMTP") as mock_smtp:
            email_service.send_welcome_email("new@example.com", "New Hire", "Secret123!")
            mock_smtp.assert_not_called()
