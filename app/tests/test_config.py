"""Production-mode guards in app/core/config.py: the app must refuse to start
with a placeholder SECRET_KEY or without a DATABASE_URL, since either silently
turns a deployment into an insecure or data-losing one."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

STRONG_KEY = "f" * 64
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


@pytest.mark.parametrize("key", ["dev-only-secret-change-me", "short", "please-change-this-to-something-long-and-random"])
def test_production_with_placeholder_or_short_secret_key_is_rejected(monkeypatch, key):
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        build(monkeypatch, ENVIRONMENT="production", SECRET_KEY=key, DATABASE_URL=NEON_URL)


def test_development_needs_neither_a_strong_key_nor_a_database(monkeypatch):
    s = build(monkeypatch, ENVIRONMENT="development")
    assert s.is_sqlite and not s.is_production
