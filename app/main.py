import fcntl
import hmac
import logging
import os
import secrets
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
from app.core.security import SESSION_COOKIE_NAME, _request_is_secure
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


# M-17c: the startup migration lock used to block forever
# (fcntl.flock with no timeout) if a previous worker died while holding
# it — every worker would hang at boot. Bound the wait; a worker that
# cannot get the lock in this long fails loudly instead of hanging.
MIGRATION_LOCK_TIMEOUT_SECONDS = 120


def _acquire_migration_lock(path: Path, timeout: float = MIGRATION_LOCK_TIMEOUT_SECONDS):
    """Open + exclusively lock the migration lock file, waiting at most
    `timeout` seconds. Returns the open file (caller must close it,
    which releases the lock). Raises TimeoutError when the lock is
    still held after the timeout."""
    lock_file = open(path, "a+")
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lock_file
        except BlockingIOError:
            if time.monotonic() >= deadline:
                lock_file.close()
                raise TimeoutError(
                    f"Could not acquire the migration lock at {path} within "
                    f"{timeout}s — another worker may have died while holding "
                    "it. Remove the stale lock file and restart."
                ) from None
            time.sleep(0.5)


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
        # everything already at head. The wait is bounded (M-17c): a
        # stale lock fails startup loudly instead of hanging it.
        lock_file = _acquire_migration_lock(_MIGRATION_LOCK_PATH)
        try:
            upgrade_with_retry(_ALEMBIC_CFG)
        finally:
            lock_file.close()
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

_STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# M-37: double-submit CSRF token. The existing Origin/Referer gate
# below blocks cross-site POSTs (browsers always send Origin on a
# cross-site POST), but a mutating request with NEITHER header used to
# be waved through — "API clients, tests, curl". A scripted client
# holding a stolen session cookie could use that gap, so headerless
# cookie-authenticated mutations now need the token as well:
#   1. any response mints a random `csrf_token` cookie (not HttpOnly —
#      JS must be able to read it to send it back);
#   2. a headerless mutating request carrying a session cookie must
#      send the same value back as an X-CSRF-Token header.
# Deliberately NOT breaking: requests that carry an Origin/Referer
# (every browser request, including the whole React frontend), Bearer
# API clients, and Starlette TestClient ("testclient" peer — the test
# suite has no browser and sends no Origin). API clients that use
# cookie auth from a script fetch a token from GET /api/csrf-token
# first (it returns the value and sets the cookie).
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"


def _csrf_token_valid(request: Request) -> bool:
    token = request.headers.get(CSRF_HEADER_NAME)
    cookie = request.cookies.get(CSRF_COOKIE_NAME)
    return bool(token and cookie and hmac.compare_digest(token, cookie))


def _headerless_mutation_rejected(request: Request) -> bool:
    """True when a headerless state-changing request must be rejected
    for lack of a CSRF token. Only applies to cookie-authenticated
    requests: no session cookie means there is no ambient credential
    to forge with, and Bearer clients are API clients by definition."""
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    if origin or referer:
        return False
    if not request.cookies.get(SESSION_COOKIE_NAME):
        return False
    if request.headers.get("authorization"):
        return False
    peer = request.client.host if request.client else ""
    if peer == "testclient":
        # Starlette TestClient's synthetic peer — the test suite.
        return False
    return not _csrf_token_valid(request)

_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; "
    "base-uri 'self'; form-action 'self'"
)
# M-17e: object-src 'none' added (no plugins/embeds are used anywhere).
# style-src keeps 'unsafe-inline': the frontend needs it — Recharts and
# Tailwind apply inline styles (style attributes) for charts and
# layout, and removing it breaks the dashboards. That is the documented
# trade-off; script-src stays 'self' with no unsafe-inline.


def _cross_site_origin(request: Request) -> bool:
    """True when a state-changing request carries an Origin (or Referer)
    from somewhere other than this app. SameSite=Lax already blocks most
    cross-site cookie POSTs, but this is the explicit second line of
    defence (M7). Allowed: the request's own Host (same-origin, including
    the deployed domain) and the configured CORS origins (the Vite dev
    server, which proxies /api to the backend), and any loopback origin
    (localhost / 127.0.0.1 — dev tooling on any port; a remote attacker's
    site can never carry a loopback origin). Requests with neither
    header (API clients, tests, curl) are allowed."""
    from urllib.parse import urlparse

    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    if not origin and not referer:
        return False
    allowed = {urlparse(o).netloc.lower() for o in settings.cors_origins_list if urlparse(o).netloc}
    host = request.headers.get("host", "")
    if host:
        allowed.add(host.lower())
    for value in (origin, referer):
        if value:
            parsed = urlparse(value)
            netloc = parsed.netloc.lower()
            if netloc and netloc not in allowed:
                # Loopback origins are local dev tooling, never a remote site.
                if parsed.hostname in ("localhost", "127.0.0.1", "::1"):
                    continue
                return True
    return False


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.method in _STATE_CHANGING_METHODS and _cross_site_origin(request):
        return JSONResponse(
            status_code=403,
            content={"detail": "Cross-site request rejected."},
        )
    if request.method in _STATE_CHANGING_METHODS and _headerless_mutation_rejected(request):
        return JSONResponse(
            status_code=403,
            content={"detail": "CSRF token missing or invalid. Fetch one from GET /api/csrf-token and send it as an X-CSRF-Token header."},
        )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = _CSP
    # M-37: mint the double-submit CSRF cookie when the client does not
    # have one yet (every response — a scripted client fetches it with
    # a plain GET before its first mutation). Skip when the handler
    # already set it (GET /api/csrf-token), so the two values cannot
    # disagree.
    already_set = any(
        h.startswith(CSRF_COOKIE_NAME + "=")
        for h in response.headers.getlist("set-cookie")
    )
    if CSRF_COOKIE_NAME not in request.cookies and not already_set:
        response.set_cookie(
            CSRF_COOKIE_NAME,
            secrets.token_urlsafe(32),
            httponly=False,  # JS must read it to send it back as a header
            secure=_request_is_secure(request),
            samesite="lax",
            path="/",
        )
    # HSTS only when this request actually arrived over HTTPS — sending
    # it over plain HTTP would be meaningless (and the header is also
    # set by Caddy in production).
    if _request_is_secure(request):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.get("/api/csrf-token")
def csrf_token(request: Request):
    """M-37: hand a double-submit CSRF token to a cookie-authenticated
    API client. Returns the value and sets the matching cookie; the
    client sends the value back as an X-CSRF-Token header on headerless
    mutating requests. Browsers never need this — they always send an
    Origin header, which the Origin gate already covers."""
    token = request.cookies.get(CSRF_COOKIE_NAME) or secrets.token_urlsafe(32)
    response = JSONResponse(content={"csrf_token": token})
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        httponly=False,
        secure=_request_is_secure(request),
        samesite="lax",
        path="/",
    )
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
        content={"detail": "Too many requests from this network (IP rate limit). Please wait a minute and try again."},
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
