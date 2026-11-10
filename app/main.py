import fcntl
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from alembic import command
from alembic.config import Config
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.core.config import settings
from app.core.rate_limit import limiter
from app.core.tenant import TenantResolutionMiddleware
from app.database import engine
from app.routers import admin, analytics, attendance, auth, dashboard, importer, kpi, notifications, platform, reminders, reports
logger = logging.getLogger("kpi_system")

if settings.is_production and settings.SECRET_KEY == "change-this-secret-key-in-production":
    raise RuntimeError(
        "SECRET_KEY is still set to its insecure default. Set a long random SECRET_KEY "
        "env var before running with ENVIRONMENT=production — every JWT session is "
        "forgeable by anyone who knows this default otherwise."
    )

# Inter-process file lock for schema migrations (see lifespan). Workers
# in one container share the filesystem, so this serializes them; the
# deploy is a single container (docker compose up -d --build), so one
# lock file is sufficient. Override via env for tests.
_MIGRATION_LOCK_PATH = Path(os.environ.get("KPI_MIGRATION_LOCK_FILE", "/tmp/kpi-migration.lock"))

_ALEMBIC_CFG = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))

# M15: startup migration retries — a Neon blip at boot must be a slow
# start, not a crash loop. 5 attempts, ~50s of backoff between them
# (plus the time each attempt itself takes) before giving up.
MIGRATION_RETRY_ATTEMPTS = 5
MIGRATION_RETRY_DELAYS = (5, 10, 15, 20)  # seconds between attempts


def upgrade_with_retry(
    alembic_cfg=_ALEMBIC_CFG,
    attempts: int = MIGRATION_RETRY_ATTEMPTS,
    delays: tuple[int, ...] = MIGRATION_RETRY_DELAYS,
    upgrade_fn=None,
    sleep=time.sleep,
) -> None:
    """Run `alembic upgrade head`, retrying with backoff on failure.

    `upgrade_fn` (config, revision) and `sleep` are injectable so unit
    tests can drive the retry loop without a real database or waiting.
    Raises the last error after the final attempt.
    """
    upgrade = upgrade_fn or command.upgrade
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            logger.info("Running database migrations (attempt %d/%d)...", attempt, attempts)
            upgrade(alembic_cfg, "head")
            logger.info("Database migrations complete (attempt %d/%d).", attempt, attempts)
            return
        except Exception as exc:  # noqa: BLE001 — any DB/network failure retries
            last_error = exc
            if attempt == attempts:
                logger.error(
                    "Database migrations failed after %d attempts; giving up.",
                    attempts, exc_info=True,
                )
                raise
            delay = delays[attempt - 1] if attempt - 1 < len(delays) else delays[-1]
            logger.warning(
                "Database migration attempt %d/%d failed (%s); retrying in %ss.",
                attempt, attempts, exc, delay,
            )
            sleep(delay)
    if last_error is not None:  # pragma: no cover — unreachable with attempts >= 1
        raise last_error


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # m28: @app.on_event("startup") is deprecated — startup work lives
    # here now. Migration behaviour is unchanged: SQLite upgrades
    # directly; Postgres upgrades under the file lock (see the comment
    # in the old on_startup — the fcntl lock is held for the whole
    # upgrade call, retries included, so concurrent gunicorn workers
    # still serialize and the rest find everything already at head).
    if settings.is_sqlite:
        upgrade_with_retry(_ALEMBIC_CFG)
    else:
        # Production runs multiple gunicorn workers that each boot this
        # startup hook concurrently. Alembic's DDL isn't safe run
        # concurrently from multiple processes (e.g. two workers racing
        # to create the same enum type crash with a UniqueViolation).
        # The previous pg advisory lock did NOT protect anything: it
        # was held on a connection from `engine`, while Alembic opens
        # its own engine/connection in env.py, so the lock and the DDL
        # never shared a session. The file lock below is held for the
        # whole upgrade call itself, so whichever worker gets there
        # first runs the migrations and the rest block, then find
        # everything already at head.
        with open(_MIGRATION_LOCK_PATH, "a+") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                upgrade_with_retry(_ALEMBIC_CFG)
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)
    yield


app = FastAPI(
    title=settings.APP_NAME,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,  # cookie-based session auth needs credentials cross-origin (local dev)
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(TenantResolutionMiddleware)

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def handle_unexpected_exception(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "An unexpected error occurred. Please try again or contact your administrator."},
    )


# Rate limiting (slowapi) — 429s return the same {detail} shape as other errors.
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def handle_rate_limit_exceeded(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many attempts. Please wait a minute and try again."},
    )


app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(kpi.router)
app.include_router(admin.router)
app.include_router(platform.router)
app.include_router(notifications.router)
app.include_router(reminders.router)
app.include_router(analytics.router)
app.include_router(importer.router)
app.include_router(reports.router)
app.include_router(attendance.router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    """Readiness: /healthz stays light (never touches the DB, so probing
    it never wakes a suspended Neon compute); /readyz proves the app can
    actually serve traffic by running SELECT 1 against the database.
    The Dockerfile HEALTHCHECK and Caddy's service_healthy gate use this
    endpoint, so a dead/suspended database stops getting traffic instead
    of reporting healthy and 500ing every API call. The DB connect
    timeout (10s, app/database.py) bounds how long a check can hang."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        logger.warning("readyz: database check failed", exc_info=True)
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ok"}


# Single-deployment mode: the React app is built into ./frontend_dist alongside this
# file (see the root Dockerfile) and served directly, so the whole app — API and UI —
# lives at one URL, the way the old Jinja2 templates did. Registered last so it never
# shadows the API routes/healthz above: Starlette matches routes in registration order.
FRONTEND_DIST = (Path(__file__).resolve().parent.parent / "frontend_dist").resolve()

if FRONTEND_DIST.is_dir():

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str):
        if full_path.startswith(("api/", "docs", "redoc", "openapi.json", "healthz", "readyz")):
            raise HTTPException(status_code=404, detail="Not Found")

        candidate = (FRONTEND_DIST / full_path).resolve()
        if full_path and candidate.is_file() and FRONTEND_DIST in candidate.parents:
            if candidate.name == "index.html":
                # The entry point references the hashed bundles — never let the
                # browser keep booting an old app version after a deploy.
                return FileResponse(candidate, headers={"Cache-Control": "no-cache"})
            return FileResponse(candidate)

        # Anything else (including client-side routes like /admin/users) falls
        # back to index.html so React Router can take over.
        return FileResponse(
            FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-cache"}
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
