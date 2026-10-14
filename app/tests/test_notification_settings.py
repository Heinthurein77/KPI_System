"""Tests for notification preferences: users can opt out of notification emails.

In-app notifications are always kept; only the SMTP email copy is affected.
"""

from unittest.mock import MagicMock, patch

from app.models.notification import Notification
from app.models.user import UserRole
from app.services import email_service
from app.tests.factories import (
    auth_headers,
    make_department,
    make_tenant,
    make_user,
)


def _make_notification(db, tenant, user):
    n = Notification(
        tenant_id=tenant.id,
        user_id=user.id,
        type="kpi_submitted",
        title="Test title",
        message="Test message",
        link=None,
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


def _setup(db):
    tenant = make_tenant(db)
    dept = make_department(db, tenant)
    user = make_user(db, tenant, UserRole.EMPLOYEE, name="Employee", department=dept)
    return tenant, user


def test_settings_default_to_email_enabled(client, db):
    tenant, user = _setup(db)

    resp = client.get("/api/notifications/settings", headers=auth_headers(user, tenant))
    assert resp.status_code == 200
    assert resp.json() == {"email_enabled": True}


def test_update_settings_toggles_preference(client, db):
    tenant, user = _setup(db)

    resp = client.put(
        "/api/notifications/settings",
        json={"email_enabled": False},
        headers=auth_headers(user, tenant),
    )
    assert resp.status_code == 200
    assert resp.json() == {"email_enabled": False}

    resp = client.get("/api/notifications/settings", headers=auth_headers(user, tenant))
    assert resp.json() == {"email_enabled": False}

    resp = client.put(
        "/api/notifications/settings",
        json={"email_enabled": True},
        headers=auth_headers(user, tenant),
    )
    assert resp.json() == {"email_enabled": True}


def test_settings_require_auth(client, db):
    assert client.get("/api/notifications/settings").status_code == 401
    assert client.put("/api/notifications/settings", json={"email_enabled": False}).status_code == 401


def test_email_skipped_when_user_opted_out(db):
    tenant, user = _setup(db)
    user.email_notifications = False
    db.commit()
    n = _make_notification(db, tenant, user)

    s = _configured_settings()
    with patch.object(email_service, "settings", s):
        with patch("smtplib.SMTP") as mock_smtp:
            email_service.send_for_notifications(db, [n])
            mock_smtp.assert_not_called()


def test_email_sent_when_user_opted_in(db):
    tenant, user = _setup(db)
    assert user.email_notifications is True
    n = _make_notification(db, tenant, user)

    s = _configured_settings()
    with patch.object(email_service, "settings", s):
        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp = mock_smtp_cls.return_value
            # Run the sender inline instead of in a daemon thread.
            with patch.object(
                email_service,
                "_send_in_background",
                side_effect=lambda payloads: email_service._run_sender(payloads),
            ):
                email_service.send_for_notifications(db, [n])
                mock_smtp.send_message.assert_called_once()


def test_welcome_email_ignores_preference(db):
    """Account credentials must always reach the user, even with emails off."""
    tenant, user = _setup(db)
    user.email_notifications = False
    db.commit()

    s = _configured_settings()
    with patch.object(email_service, "settings", s):
        with patch("smtplib.SMTP") as mock_smtp_cls:
            mock_smtp = mock_smtp_cls.return_value
            # Run the sender inline instead of in a daemon thread.
            with patch.object(
                email_service,
                "_send_in_background",
                side_effect=lambda payloads: email_service._run_sender(payloads),
            ):
                email_service.send_welcome_email(user.email, user.name, "Secret123!", org_name="Acme")
                mock_smtp.send_message.assert_called_once()
