import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.deps import NotAuthenticated, get_current_user_optional
from app.database import Base, SessionLocal, engine
from app.routers import admin, auth, dashboard, kpi

logger = logging.getLogger("kpi_system")

app = FastAPI(
    title=settings.APP_NAME,
    # This is a server-rendered app, not a public JSON API — no reason to expose
    # route/schema introspection in production.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

templates = Jinja2Templates(directory="app/templates")

# Arbitrary fixed id for the schema-creation advisory lock — any int64 works,
# it just needs to be the same value everywhere this runs.
_SCHEMA_LOCK_ID = 727271

_ERROR_HEADINGS = {
    400: "Bad Request",
    403: "Access Denied",
    404: "Page Not Found",
    405: "Method Not Allowed",
    422: "Invalid Request",
}


@app.on_event("startup")
def on_startup() -> None:
    if settings.is_sqlite:
        Base.metadata.create_all(bind=engine)
        return

    # Production runs multiple gunicorn workers that each boot this startup hook
    # concurrently. SQLAlchemy's create_all() is safe for tables (CREATE TABLE IF
    # NOT EXISTS) but Postgres ENUM types have no such atomic guard, so two workers
    # racing to create the same enum type crash with a UniqueViolation. A session-level
    # advisory lock serializes DDL across worker processes: whoever gets there first
    # creates the schema; the rest block, then find everything already exists.
    with engine.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:id)"), {"id": _SCHEMA_LOCK_ID})
        try:
            Base.metadata.create_all(bind=engine)
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": _SCHEMA_LOCK_ID})
            conn.commit()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def _current_user_for_error_page(request: Request):
    """Best-effort lookup so error pages render inside the sidebar shell when logged in."""
    db = SessionLocal()
    try:
        user = get_current_user_optional(request, db)
        if user is not None:
            _ = user.department  # force-load before the session closes below, or
            # base.html's lazy access to it raises DetachedInstanceError post-close.
        return user
    except Exception:
        return None
    finally:
        db.close()


@app.exception_handler(NotAuthenticated)
def handle_not_authenticated(request: Request, exc: NotAuthenticated) -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=303)


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(request: Request, exc: StarletteHTTPException):
    heading = _ERROR_HEADINGS.get(exc.status_code, "Something Went Wrong")
    message = exc.detail if isinstance(exc.detail, str) and exc.detail else "Please try again."
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": exc.status_code,
            "heading": heading,
            "message": message,
            "user": _current_user_for_error_page(request),
        },
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def handle_unexpected_exception(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": 500,
            "heading": "Something Went Wrong",
            "message": "An unexpected error occurred. Please try again or contact your administrator.",
            "user": _current_user_for_error_page(request),
        },
        status_code=500,
    )


app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(kpi.router)
app.include_router(admin.router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
