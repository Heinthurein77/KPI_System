"""Shared pytest fixtures for the backend test suite.

Test isolation: DATABASE_URL is forced to a throwaway temp-file SQLite
database *before* anything under app/ is imported, so every module-level
engine (app.database.engine/SessionLocal, and anything that imports them,
e.g. app.core.tenant's TenantResolutionMiddleware) binds to that file —
never to the developer's local kpi_system.db or a real DATABASE_URL a .env
might point at (e.g. a live Neon database). Schema is created directly from
the SQLAlchemy models (Base.metadata.create_all), not via Alembic, since
that's what the models — the source of truth Alembic's migrations were
authored against — already give us for free and it's far faster per test.
"""

import os
import tempfile
import uuid
from pathlib import Path

_TEST_DB_PATH = Path(tempfile.gettempdir()) / f"kpi_dashboard_test_{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH.as_posix()}"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production-use"
os.environ["ENVIRONMENT"] = "development"
os.environ["CORS_ORIGINS"] = "http://testserver"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    _TEST_DB_PATH.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _clean_tables():
    """Every test starts against an empty database."""
    yield
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


@pytest.fixture()
def db():
    """A raw session for test setup (building fixtures directly), separate
    from the sessions the app itself opens per-request via get_db."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client():
    # Not entered as `with TestClient(app) as c:` — that would run app.main's
    # real startup event (an Alembic `upgrade head`), which is redundant here
    # (schema is already created above) and unnecessarily couples every test
    # run to the migration chain.
    return TestClient(app)
