"""Production-mode guards in app/core/config.py: the app must refuse to start
with a placeholder SECRET_KEY or without a DATABASE_URL, since either silently
turns a deployment into an insecure or data-losing one."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

STRONG_KEY = "9f2c7a1e4b8d03f6a5c1e7b9d2f4a6c8e0b3d5f7a1c9e4b6d8f0a2c4e6b8d0f"
NEON_URL = "postgresql://user:pw@ep-cool-123.us-east-2.aws.neon.tech/db?sslmode=require"


def build(monkeypatch, **env) -> Settings:
    for name in ("ENVIRONMENT", "SECRET_KEY", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def test_production_with_real_key_and_database_is_accepted(monkeypatch):
    s = build(monkeypatch, ENVIRONMENT="production", SECRET_KEY=STRONG_KEY, DATABASE_URL=NEON_URL)
    assert s.is_production and not s.is_sqlite


def test_production_without_database_url_is_rejected(monkeypatch):
    with pytest.raises(ValidationError, match="DATABASE_URL is not set"):
        build(monkeypatch, ENVIRONMENT="production", SECRET_KEY=STRONG_KEY)


@pytest.mark.parametrize("key", ["dev-only-secret-change-me", "short", "please-change-this-to-something-long-and-random", "a" * 32, "f" * 64])
def test_production_with_placeholder_or_short_secret_key_is_rejected(monkeypatch, key):
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        build(monkeypatch, ENVIRONMENT="production", SECRET_KEY=key, DATABASE_URL=NEON_URL)


def test_development_needs_neither_a_strong_key_nor_a_database(monkeypatch):
    s = build(monkeypatch, ENVIRONMENT="development")
    assert s.is_sqlite and not s.is_production


def test_invalid_timezone_is_rejected_at_startup(monkeypatch):
    """S-m7: TIMEZONE must be a loadable IANA zone — fail fast with a
    clear error instead of a ZoneInfo exception mid-request."""
    with pytest.raises(ValidationError, match="TIMEZONE"):
        build(monkeypatch, TIMEZONE="Not/AZone")


def test_valid_timezone_is_accepted(monkeypatch):
    s = build(monkeypatch, TIMEZONE="Asia/Bangkok")
    assert s.TIMEZONE == "Asia/Bangkok"


def test_ignored_env_vars_are_surfaced(monkeypatch):
    """S-m7: a typo'd setting name (SUBMISSION_GRACE_DAY) or a KPI_*
    var is reported instead of silently running on the default."""
    from app.core.config import ignored_env_vars

    flagged = ignored_env_vars(
        {
            "SUBMISSION_GRACE_DAY": "3",
            "KPI_FOO": "1",
            "SUBMISSION_GRACE_DAYS": "7",
            "SECRET_KEY": "x",
            "HOME": "/root",
            "PATH": "/usr/bin",
        }
    )
    assert "SUBMISSION_GRACE_DAY" in flagged
    assert "KPI_FOO" in flagged
    assert "SUBMISSION_GRACE_DAYS" not in flagged
    assert "SECRET_KEY" not in flagged
    assert "HOME" not in flagged
    assert "PATH" not in flagged


# --------------------------------------------------------------------- M-08


def test_cors_wildcard_is_rejected(monkeypatch):
    # M-08: "*" with allow_credentials=True would let ANY site call the
    # API with the user's session cookies — rejected at startup.
    with pytest.raises(ValidationError, match="must not contain"):
        build(monkeypatch, CORS_ORIGINS="*")
    with pytest.raises(ValidationError, match="must not contain"):
        build(monkeypatch, CORS_ORIGINS="http://localhost:5173, *")


def test_cors_named_origins_are_accepted(monkeypatch):
    s = build(monkeypatch, CORS_ORIGINS="http://localhost:5173, https://kpi.example.com")
    assert s.cors_origins_list == ["http://localhost:5173", "https://kpi.example.com"]
