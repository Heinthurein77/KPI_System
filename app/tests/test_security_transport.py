"""Security-review (2026-11-25) transport tests — Track 3.

M-12 (trusted-proxy rule), M-29 (forged X-Forwarded-For — the
end-to-end negative test already exists in test_security_fixes.py; here
the rule itself is covered, including the RFC-1918 narrowing),
M-13 (DATABASE_URL sslmode enforcement), M-37 (double-submit CSRF
token), M-17c (migration-lock timeout), M-17e (CSP object-src +
Caddy HSTS includeSubDomains).
"""

import fcntl
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.core.security import SESSION_COOKIE_NAME
from app.core.proxy import is_trusted_proxy_peer, trusted_client_ip
from app.database import ensure_db_sslmode
from app.main import (
    _acquire_migration_lock,
    _csrf_token_valid,
    _headerless_mutation_rejected,
    _CSP,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _stub_request(
    *,
    peer="testclient",
    headers=None,
    cookies=None,
    scheme="http",
):
    return SimpleNamespace(
        client=SimpleNamespace(host=peer) if peer is not None else None,
        headers=headers or {},
        cookies=cookies or {},
        url=SimpleNamespace(scheme=scheme),
    )


# ------------------------------------------------------------------ M-12


def test_trusted_proxy_peer_loopback_and_docker_space_only():
    assert is_trusted_proxy_peer("127.0.0.1") is True
    assert is_trusted_proxy_peer("::1") is True
    assert is_trusted_proxy_peer("172.18.0.5") is True  # compose network
    assert is_trusted_proxy_peer("172.16.0.1") is True
    # M-12: 10/8 and 192.168/16 are RFC-1918 but NOT Caddy's network —
    # they used to be trusted and are no longer.
    assert is_trusted_proxy_peer("10.0.0.5") is False
    assert is_trusted_proxy_peer("192.168.1.1") is False
    assert is_trusted_proxy_peer("8.8.8.8") is False
    assert is_trusted_proxy_peer("203.0.113.10") is False
    assert is_trusted_proxy_peer(None) is False
    assert is_trusted_proxy_peer("not-an-ip") is False


def test_trusted_proxy_cidrs_are_configurable(monkeypatch):
    monkeypatch.setattr(settings, "TRUSTED_PROXY_CIDRS", "10.9.0.0/16")
    assert is_trusted_proxy_peer("10.9.0.5") is True
    assert is_trusted_proxy_peer("172.18.0.5") is False
    assert is_trusted_proxy_peer("127.0.0.1") is False


def test_forged_xff_from_untrusted_peer_is_ignored():
    # M-29 (rule level; the end-to-end check-in test lives in
    # test_security_fixes.py::test_forged_xff_from_non_proxy_peer_is_ignored).
    req = _stub_request(
        peer="10.0.0.5", headers={"x-forwarded-for": "203.0.113.10"}
    )
    assert trusted_client_ip(req) == "10.0.0.5"


def test_xff_from_trusted_peer_is_honoured():
    req = _stub_request(
        peer="172.18.0.5", headers={"x-forwarded-for": "203.0.113.10"}
    )
    assert trusted_client_ip(req) == "203.0.113.10"


# ------------------------------------------------------------------ M-13


def test_sslmode_required_for_remote_hosts():
    url = "postgresql://kpi:pw@myproj.neon.tech:5432/kpi_system"
    assert "sslmode=require" in ensure_db_sslmode(url)


def test_sslmode_explicit_value_is_honoured():
    url = "postgresql://kpi:pw@myproj.neon.tech:5432/kpi_system?sslmode=verify-full"
    assert ensure_db_sslmode(url) == url


def test_sslmode_not_added_for_local_db():
    # The local-DB deployment (2026-11-25 migration): the `db` service
    # over the private compose network must pass unchanged.
    assert ensure_db_sslmode("postgresql://kpi:pw@db:5432/kpi_system") == (
        "postgresql://kpi:pw@db:5432/kpi_system"
    )
    assert ensure_db_sslmode(
        "postgresql://kpi:pw@localhost:5432/kpi_system"
    ) == "postgresql://kpi:pw@localhost:5432/kpi_system"
    assert ensure_db_sslmode(
        "postgresql://kpi:pw@10.1.2.3:5432/kpi_system"
    ) == "postgresql://kpi:pw@10.1.2.3:5432/kpi_system"
    assert ensure_db_sslmode("sqlite:///./kpi_system.db") == (
        "sqlite:///./kpi_system.db"
    )


# ------------------------------------------------------------------ M-37


def test_csrf_token_must_match_cookie():
    req = _stub_request(
        cookies={"csrf_token": "abc", "session": "x"},
        headers={"x-csrf-token": "abc"},
    )
    assert _csrf_token_valid(req) is True
    req.headers = {"x-csrf-token": "wrong"}
    assert _csrf_token_valid(req) is False
    req.headers = {}
    assert _csrf_token_valid(req) is False


def test_headerless_cookie_mutation_without_token_is_rejected():
    req = _stub_request(
        peer="203.0.113.5",
        cookies={SESSION_COOKIE_NAME: "x"},
    )
    assert _headerless_mutation_rejected(req) is True


def test_headerless_mutation_with_matching_token_is_allowed():
    req = _stub_request(
        peer="203.0.113.5",
        cookies={SESSION_COOKIE_NAME: "x", "csrf_token": "tok"},
        headers={"x-csrf-token": "tok"},
    )
    assert _headerless_mutation_rejected(req) is False


def test_headerless_mutation_with_origin_is_not_a_csrf_case():
    # A request carrying Origin goes through the Origin gate instead;
    # this helper only covers the headerless case.
    req = _stub_request(
        peer="203.0.113.5",
        cookies={SESSION_COOKIE_NAME: "x"},
        headers={"origin": "https://evil.example.com"},
    )
    assert _headerless_mutation_rejected(req) is False


def test_headerless_bearer_request_is_allowed():
    req = _stub_request(
        peer="203.0.113.5",
        cookies={SESSION_COOKIE_NAME: "x"},
        headers={"authorization": "Bearer abc"},
    )
    assert _headerless_mutation_rejected(req) is False


def test_headerless_mutation_without_session_cookie_is_allowed():
    req = _stub_request(peer="203.0.113.5", cookies={})
    assert _headerless_mutation_rejected(req) is False


def test_csrf_token_endpoint_mints_token_and_cookie(client):
    resp = client.get("/api/csrf-token")
    assert resp.status_code == 200, resp.text
    token = resp.json()["csrf_token"]
    assert token
    assert resp.cookies.get("csrf_token") == token


# ----------------------------------------------------------------- M-17c


def test_migration_lock_times_out_when_held(tmp_path):
    lock_path = tmp_path / "kpi-migration.lock"
    # Hold the lock the old-fashioned way, like a dead worker would.
    held = open(lock_path, "a+")
    fcntl.flock(held, fcntl.LOCK_EX)
    try:
        with pytest.raises(TimeoutError):
            _acquire_migration_lock(lock_path, timeout=0.2)
    finally:
        held.close()
    # Once released, the helper acquires it.
    acquired = _acquire_migration_lock(lock_path, timeout=1)
    acquired.close()


# ----------------------------------------------------------------- M-17e


def test_csp_has_object_src_none():
    assert "object-src 'none'" in _CSP
    assert "style-src 'self' 'unsafe-inline'" in _CSP  # documented: Recharts


def test_caddy_hsts_includes_subdomains():
    caddyfile = (REPO_ROOT / "Caddyfile").read_text()
    assert 'Strict-Transport-Security "max-age=31536000; includeSubDomains"' in caddyfile
