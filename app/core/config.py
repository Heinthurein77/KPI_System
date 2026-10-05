from functools import lru_cache
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
