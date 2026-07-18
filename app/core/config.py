from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "abcMIB KPI Approval System"
    ENVIRONMENT: str = "development"
    SECRET_KEY: str = "change-this-secret-key-in-production"
    SESSION_COOKIE_NAME: str = "kpi_session"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24h

    # Falls back to local SQLite when DATABASE_URL is not set (e.g. Neon.tech Postgres URL)
    DATABASE_URL: str | None = None

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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
