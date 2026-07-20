import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.database import Base, engine
from app.routers import admin, auth, dashboard, kpi

logger = logging.getLogger("kpi_system")

app = FastAPI(
    title=settings.APP_NAME,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=False,  # Bearer-token auth, not cookies — no credentials needed cross-origin
    allow_methods=["*"],
    allow_headers=["*"],
)

# Arbitrary fixed id for the schema-creation advisory lock — any int64 works,
# it just needs to be the same value everywhere this runs.
_SCHEMA_LOCK_ID = 727271


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


app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(kpi.router)
app.include_router(admin.router)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# Single-deployment mode: the React app is built into ./frontend_dist alongside this
# file (see the root Dockerfile) and served directly, so the whole app — API and UI —
# lives at one URL, the way the old Jinja2 templates did. Registered last so it never
# shadows the API routes/healthz above: Starlette matches routes in registration order.
FRONTEND_DIST = (Path(__file__).resolve().parent.parent / "frontend_dist").resolve()

if FRONTEND_DIST.is_dir():

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str):
        if full_path.startswith(("api/", "docs", "redoc", "openapi.json", "healthz")):
            raise HTTPException(status_code=404, detail="Not Found")

        candidate = (FRONTEND_DIST / full_path).resolve()
        if full_path and candidate.is_file() and FRONTEND_DIST in candidate.parents:
            return FileResponse(candidate)

        # Anything else (including client-side routes like /admin/users) falls
        # back to index.html so React Router can take over.
        return FileResponse(FRONTEND_DIST / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
