import difflib
import logging
import os
from functools import lru_cache
from math import log2
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("kpi_system.config")

# Substrings that show up in placeholder/example secrets people forget to
# replace (including this file's own default below). Checked case-insensitively
# against SECRET_KEY in production — see Settings._reject_insecure_secret_key.
_INSECURE_SECRET_KEY_MARKERS = (
    "change", "secret-key", "dev-only", "development", "example",
    "insecure", "placeholder", "please", "default", "sample", "test-secret",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "abcMIB KPI Approval System"
    ENVIRONMENT: str = "development"
    SECRET_KEY: str = "change-this-secret-key-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24h

    # Business timezone for ALL "today" computations (period math, grace
    # windows, rollover days, reminders). Never use date.today() or UTC
    # dates for these — see app/core/timezone.py.
    TIMEZONE: str = "Asia/Yangon"

    # Comma-separated list of origins allowed to call this API (the deployed React app's
    # URL(s)). Defaults to the local Vite dev server.
    CORS_ORIGINS: str = "http://localhost:5173"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    # Falls back to local SQLite when DATABASE_URL is not set (e.g. Neon.tech Postgres URL)
    DATABASE_URL: str | None = None

    # SQLAlchemy connection pool (Postgres only). Every gunicorn worker owns its own
    # pool, so the ceiling on connections to the database is
    # WEB_CONCURRENCY * (DB_POOL_SIZE + DB_MAX_OVERFLOW). Neon's compute suspends
    # after ~5 idle minutes and drops every connection with it, so connections are
    # recycled on that same cadence rather than trusted to outlive a suspend.
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_RECYCLE: int = 300
    DB_POOL_TIMEOUT: int = 30

    # Base domain for subdomain-based tenant resolution (e.g. "kpiapp.com" so
    # "acme.kpiapp.com" resolves to tenant slug "acme"). Unset until wildcard
    # DNS/custom-domain routing is actually configured — X-Tenant-Slug header
    # resolution works regardless of this setting.
    TENANT_BASE_DOMAIN: str | None = None

    # Outgoing email (SMTP) for notification emails. When unset, only in-app
    # notifications are delivered — email sending is skipped silently.
    # For Gmail: host=smtp.gmail.com, port=587, username=your Gmail address,
    # password=a Gmail App Password (NOT your regular password).
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_USE_TLS: bool = True
    # Public base URL of the app, used to build clickable links in emails
    # (e.g. "https://kpi.example.com"). Unset → emails omit the link.
    APP_BASE_URL: str | None = None

    # Deadline reminders: nudge employees with DRAFT/REJECTED submissions when
    # the end of the current month is this many days away (or less). The
    # reminder job is meant to run daily (server cron); it sends at most one
    # reminder per user per period.
    DEADLINE_REMINDER_ENABLED: bool = True
    DEADLINE_REMINDER_DAYS_BEFORE: int = 7

    # Submission grace period: after a month ends, employees may still enter
    # scores for that month for this many days (see submission_grace.py).
    # Applies to employee save/submit and to the dashboard edit window;
    # rejected resubmissions and admin approvals are unaffected.
    SUBMISSION_GRACE_DAYS: int = 7

    @property
    def smtp_configured(self) -> bool:
        return bool(self.SMTP_HOST and self.SMTP_USERNAME and self.SMTP_PASSWORD)

    @property
    def smtp_from(self) -> str | None:
        return self.SMTP_USERNAME

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.DATABASE_URL:
            url = self.DATABASE_URL
            # Normalize legacy postgres:// scheme and force psycopg driver
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql+psycopg://", 1)
            elif url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+psycopg://", 1)
            return url
        return "sqlite:///./kpi_system.db"

    @property
    def is_sqlite(self) -> bool:
        return self.sqlalchemy_database_url.startswith("sqlite")

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @model_validator(mode="after")
    def _reject_insecure_secret_key(self) -> "Settings":
        """SECRET_KEY signs every JWT session (see core/security.py) — anyone
        who can guess or read it can forge a valid token for any user, in any
        tenant, including a platform Super Admin. app/main.py already refuses
        to start in production against the exact literal default above, but
        that only ever catches that one string. This is a broader, pattern-
        based backstop so an edited-but-still-a-placeholder value (e.g.
        "dev-only-secret-change-me" — short, and clearly never rotated to a
        real random secret) doesn't slip through. Only enforced in production;
        dev/test always use throwaway values (e.g. app/tests/conftest.py)."""
        if not self.is_production:
            return self
        lowered = self.SECRET_KEY.lower()
        if len(self.SECRET_KEY) < 32 or any(marker in lowered for marker in _INSECURE_SECRET_KEY_MARKERS):
            raise ValueError(
                "SECRET_KEY is too short or looks like a placeholder value while "
                "ENVIRONMENT=production. Every JWT session is signed with this key - "
                "set a long, randomly-generated SECRET_KEY (e.g. `openssl rand -hex 32`) "
                "before running in production."
            )
        # A long string is not a random one: 32x"a" (or 64x"f") passes the
        # length check above but has zero entropy — anyone guessing the
        # pattern forges every session. Require at least 128 bits of
        # Shannon entropy in total (a 64-hex-char `openssl rand -hex 32`
        # key carries ~256 bits; a repeated character carries 0).
        counts: dict[str, int] = {}
        for ch in self.SECRET_KEY:
            counts[ch] = counts.get(ch, 0) + 1
        total_bits = -sum(
            (c / len(self.SECRET_KEY)) * log2(c / len(self.SECRET_KEY))
            for c in counts.values()
        ) * len(self.SECRET_KEY)
        if total_bits < 128:
            raise ValueError(
                "SECRET_KEY looks non-random while ENVIRONMENT=production "
                f"(~{max(total_bits, 0):.0f} bits of entropy; need at least 128). "
                "Set a long, randomly-generated SECRET_KEY "
                "(e.g. `openssl rand -hex 32`) before running in production."
            )
        return self

    @model_validator(mode="after")
    def _validate_timezone(self) -> "Settings":
        """S-m7: TIMEZONE must be a loadable IANA zone — fail fast at
        startup with a clear error instead of a ZoneInfo exception on
        the first request that calls timezone.today()."""
        try:
            ZoneInfo(self.TIMEZONE)
        except Exception as exc:
            raise ValueError(
                f"TIMEZONE {self.TIMEZONE!r} is not a valid IANA timezone "
                "(e.g. 'Asia/Yangon'). Fix the TIMEZONE env var — every "
                "period/grace/reminder calculation depends on it."
            ) from exc
        return self

    @model_validator(mode="after")
    def _require_database_in_production(self) -> "Settings":
        """Without DATABASE_URL the app silently falls back to a SQLite file
        inside the container — which is wiped on every rebuild/redeploy, so a
        typo'd or missing .env line would quietly lose all data. Refuse to start."""
        if self.is_production and not self.DATABASE_URL:
            raise ValueError(
                "DATABASE_URL is not set while ENVIRONMENT=production. Without it the app would "
                "use a container-local SQLite file that is lost on every rebuild. Set DATABASE_URL "
                "to your Postgres (Neon) connection string."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


# ---------------------------------------------------------------------------
# S-m7: surface silently ignored env vars.
#
# ``extra="ignore"`` stays — switching to ``extra="forbid"`` would crash
# startup on any harmless extra var a deployment happens to set (e.g. a
# Docker or platform variable). Instead, at import time we log a warning
# listing env vars that LOOK like they were meant for this app but match
# no setting: anything under the ``KPI_`` prefix, anything sharing the
# first segment of a real setting name (``SUBMISSION_GRACE_DAY`` vs the
# real ``SUBMISSION_GRACE_DAYS``), and near-miss spellings of a real
# setting. A typo'd var used to run silently on the default with no
# trace at all; now it shows up in the startup log. Vars from the
# ``.env`` file are checked too (pydantic-settings reads that file
# itself — those keys never reach ``os.environ``).
# ---------------------------------------------------------------------------

def _env_file_keys() -> set[str]:
    keys: set[str] = set()
    try:
        for line in Path(".env").read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            keys.add(line.split("=", 1)[0].strip())
    except OSError:
        pass
    return keys


def ignored_env_vars(environ: dict | None = None) -> list[str]:
    """Env/.env keys that look app-related but match no Settings field."""
    known = set(Settings.model_fields)
    prefixes = {"KPI"}
    for field in known:
        prefixes.add(field.split("_", 1)[0])
    candidates = set(os.environ) if environ is None else set(environ)
    if environ is None:
        candidates |= _env_file_keys()
    ignored: list[str] = []
    for key in sorted(candidates):
        if key in known:
            continue
        if any(key.startswith(prefix + "_") for prefix in prefixes):
            ignored.append(key)
        elif difflib.get_close_matches(key, known, n=1, cutoff=0.85):
            ignored.append(key)
    return ignored


def log_ignored_env_vars() -> None:
    ignored = ignored_env_vars()
    if ignored:
        logger.warning(
            "Ignoring unrecognised environment variable(s) that look like "
            "KPI settings (check for typos — they have no effect): %s",
            ", ".join(ignored),
        )


log_ignored_env_vars()
