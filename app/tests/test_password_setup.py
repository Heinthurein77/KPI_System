"""Tests for the one-time password-setup link (S-05, security review 2026-11-25).

The welcome email carries a signed setup token instead of a plaintext
initial password. The endpoint only honours the token while
must_change_password is set and the token_version snapshot still matches
(single-use + killed by any password change/reset); expired or
wrong-purpose tokens fail with the same generic 400.
"""

from app.core.security import (
    create_password_setup_token,
    create_totp_pending_token,
    decode_password_setup_token,
    verify_password,
)
from app.models.user import UserRole
from app.tests.factories import make_tenant, make_user


def _setup_user(db):
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    user.must_change_password = True
    db.commit()
    db.refresh(user)
    return tenant, user


def test_setup_password_happy_path(db, client):
    tenant, user = _setup_user(db)
    token = create_password_setup_token(user.id, user.token_version or 0)
    resp = client.post(
        "/api/auth/setup-password",
        json={"token": token, "new_password": "NewPass123!"},
    )
    assert resp.status_code == 204, resp.text
    db.refresh(user)
    assert user.must_change_password is False
    assert verify_password("NewPass123!", user.password_hash)
    # Login works with the new password.
    resp = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "NewPass123!"},
        headers={"X-Tenant-Slug": tenant.slug},
    )
    assert resp.status_code == 200, resp.text


def test_setup_password_link_is_single_use(db, client):
    tenant, user = _setup_user(db)
    token = create_password_setup_token(user.id, user.token_version or 0)
    assert client.post(
        "/api/auth/setup-password", json={"token": token, "new_password": "NewPass123!"}
    ).status_code == 204
    # Replaying the same link now fails — must_change_password is cleared.
    resp = client.post(
        "/api/auth/setup-password", json={"token": token, "new_password": "OtherPass123!"}
    )
    assert resp.status_code == 400
    assert "Invalid or expired" in resp.json()["detail"]


def test_setup_password_link_dies_on_password_change(db, client):
    tenant, user = _setup_user(db)
    token = create_password_setup_token(user.id, user.token_version or 0)
    # An admin reset (or any password change) bumps token_version, which
    # kills outstanding setup links.
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    resp = client.post(
        "/api/auth/setup-password", json={"token": token, "new_password": "NewPass123!"}
    )
    assert resp.status_code == 400
    assert "Invalid or expired" in resp.json()["detail"]


def test_setup_password_rejects_garbage_and_wrong_purpose_tokens(db, client):
    tenant, user = _setup_user(db)
    for bad in ("not-a-token", "", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig"):
        resp = client.post(
            "/api/auth/setup-password", json={"token": bad, "new_password": "NewPass123!"}
        )
        assert resp.status_code == 400
    # A valid JWT with the wrong purpose (a TOTP pending token) is not a
    # setup token.
    wrong = create_totp_pending_token(user.id, tenant.id)
    resp = client.post(
        "/api/auth/setup-password", json={"token": wrong, "new_password": "NewPass123!"}
    )
    assert resp.status_code == 400
    assert decode_password_setup_token(wrong) is None


def test_setup_password_rejects_weak_password(db, client):
    tenant, user = _setup_user(db)
    token = create_password_setup_token(user.id, user.token_version or 0)
    # No digit → complexity failure; link stays usable afterwards.
    resp = client.post(
        "/api/auth/setup-password", json={"token": token, "new_password": "NoDigitsHere!"}
    )
    assert resp.status_code == 400
    resp = client.post(
        "/api/auth/setup-password", json={"token": token, "new_password": "NewPass123!"}
    )
    assert resp.status_code == 204


def test_setup_password_rejects_unknown_user(db, client):
    # A token for a user id that does not exist answers the same generic
    # 400 — the endpoint cannot be used to probe accounts.
    token = create_password_setup_token(999999, 0)
    resp = client.post(
        "/api/auth/setup-password", json={"token": token, "new_password": "NewPass123!"}
    )
    assert resp.status_code == 400
    assert "Invalid or expired" in resp.json()["detail"]


def test_setup_password_requires_must_change_password(db, client):
    # A user who already set their password cannot use a (stale) setup
    # link, even if the token itself is fresh.
    tenant = make_tenant(db)
    user = make_user(db, tenant, UserRole.EMPLOYEE)
    db.refresh(user)
    token = create_password_setup_token(user.id, user.token_version or 0)
    resp = client.post(
        "/api/auth/setup-password", json={"token": token, "new_password": "NewPass123!"}
    )
    assert resp.status_code == 400
