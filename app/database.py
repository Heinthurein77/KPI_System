import logging
import time
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

logger = logging.getLogger("kpi_system")

# M19 (pool-monitoring half): how long a pool checkout may take before
# it is worth a warning in the logs. Pool sizes / WEB_CONCURRENCY are
# deliberately NOT raised here — the Neon connection budget is fixed,
# so exhaustion must become visible instead of silently queuing
# requests until the 30s pool_timeout fails them.
POOL_CHECKOUT_WARN_SECONDS = 5.0


def install_pool_checkout_monitor(engine_, threshold_seconds: float = POOL_CHECKOUT_WARN_SECONDS):
    """Log a warning when acquiring a pooled connection takes longer
    than `threshold_seconds`.

    `pool.connect()` is the entry point for every checkout (Engine and
    Session both go through it), and the queue wait happens inside it
    (`_ConnectionRecord.checkout` → `pool._do_get`), so timing that one
    call measures the real wait — including time spent queued behind a
    burst — plus the pre-ping. The wrapper is idempotent per pool and
    re-raises any acquisition error unchanged; it only adds the timing
    and the warning. Deliberately no metrics/alerting sink yet — the log
    line is the failure signal a log-watch can alert on (see M20).
    """
    pool = engine_.pool
    if getattr(pool, "_checkout_monitor_installed", False):
        return
    original_connect = pool.connect

    def timed_connect():
        started = time.monotonic()
        try:
            return original_connect()
        finally:
            waited = time.monotonic() - started
            if waited > threshold_seconds:
                try:
                    pool_status = pool.status()
                except Exception:  # noqa: BLE001 — status is best-effort diagnostics
                    pool_status = "unknown"
                logger.warning(
                    "DB pool checkout waited %.1fs (threshold %.0fs) — pool "
                    "exhausted or database slow; %s",
                    waited, threshold_seconds, pool_status,
                )

    pool.connect = timed_connect  # type: ignore[method-assign]
    pool._checkout_monitor_installed = True  # type: ignore[attr-defined]

if settings.is_sqlite:
    _engine_options = {"connect_args": {"check_same_thread": False}}
else:
    _engine_options = {
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        "pool_recycle": settings.DB_POOL_RECYCLE,
        "pool_timeout": settings.DB_POOL_TIMEOUT,
        "connect_args": {
            # TLS itself (sslmode=require) comes from the DATABASE_URL query string.
            # Bounded so a cold Neon compute that is still waking up fails the
            # request instead of hanging a worker thread indefinitely.
            "connect_timeout": 10,
            # Neon suspends idle computes and silently drops their connections;
            # keepalives surface a dead socket in ~a minute instead of on first use.
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5,
            # psycopg3 otherwise switches to server-side prepared statements after
            # 5 executions, which breaks behind Neon's PgBouncer (-pooler) endpoint.
            "prepare_threshold": None,
        },
    }

# pool_pre_ping validates a connection on checkout, so the first request after a
# Neon suspend transparently reconnects (waking the compute) instead of erroring.
engine = create_engine(settings.sqlalchemy_database_url, pool_pre_ping=True, **_engine_options)

install_pool_checkout_monitor(engine)

if settings.is_sqlite:
    # SQLite ignores FOREIGN KEY ... ON DELETE CASCADE unless foreign key
    # enforcement is explicitly turned on per-connection — without this,
    # deleting a tenant (or department, or template) silently leaves its
    # users/departments/submissions behind as orphaned rows instead of
    # cascading, unlike Postgres (the production target), which enforces
    # this by default.
    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
