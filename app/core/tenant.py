from __future__ import annotations

from typing import TypeVar

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response

from app.core.config import settings
from app.database import SessionLocal
from app.models.tenant import Tenant, TenantStatus

# Routes that never need a resolved tenant — platform-level auth/management,
# infra checks, and API docs.
PLATFORM_PATH_PREFIXES = ("/api/platform", "/healthz", "/docs", "/redoc", "/openapi.json")


def _slug_from_hostname(hostname: str | None) -> str | None:
    """Subdomain-based resolution — inert today (no wildcard DNS/custom domain
    configured), activates automatically once TENANT_BASE_DOMAIN is set."""
    if not hostname or not settings.TENANT_BASE_DOMAIN:
        return None
    base = settings.TENANT_BASE_DOMAIN.lower()
    hostname = hostname.lower()
    if hostname == base or not hostname.endswith("." + base):
        return None
    subdomain = hostname[: -(len(base) + 1)]
    if not subdomain or "." in subdomain or subdomain == "www":
        return None
    return subdomain


class TenantResolutionMiddleware(BaseHTTPMiddleware):
    """Resolves request.state.tenant from (a) hostname subdomain, (b) the
    X-Tenant-Slug header. Never blocks the request itself — a missing/unknown/
    suspended tenant is enforced downstream by get_current_tenant (for the
    pre-auth login endpoint) and get_current_user's cross-check (for every
    authenticated route), not here.
    """

    async def dispatch(self, request: StarletteRequest, call_next) -> Response:
        request.state.tenant = None
        # Positively marks routes that never need a resolved tenant, instead of
        # get_current_user inferring "platform context" from resolution simply
        # failing (e.g. a tenant-scoped request sent with no X-Tenant-Slug header)
        # — the two are different things and were previously conflated, which let
        # a platform-level account authenticate against a tenant-scoped route
        # whenever the caller omitted the header.
        request.state.tenant_exempt = request.url.path.startswith(PLATFORM_PATH_PREFIXES)

        if request.state.tenant_exempt:
            return await call_next(request)

        slug = _slug_from_hostname(request.url.hostname) or request.headers.get("X-Tenant-Slug")
        if slug:
            slug = slug.strip().lower()
            db = SessionLocal()
            try:
                tenant = db.scalar(select(Tenant).where(Tenant.slug == slug))
            finally:
                db.close()
            if tenant is not None:
                request.state.tenant = tenant

        return await call_next(request)


def get_current_tenant(request: Request) -> Tenant:
    """Dependency for routes that need a resolved tenant before an authenticated
    user exists (tenant login). Authenticated routes rely on user.tenant_id via
    deps.get_current_user's cross-check instead, not this dependency."""
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No organization could be resolved for this request.")
    if tenant.status != TenantStatus.ACTIVE:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This organization's account is suspended.")
    return tenant


ModelT = TypeVar("ModelT")


def get_tenant_scoped_or_404(
    db: Session, model: type[ModelT], obj_id: int, tenant_id: int | None, not_found_msg: str = "Not found."
) -> ModelT:
    """Replaces a bare db.get(Model, id) at every tenant-scoped call site. 404s
    (not 403s) on a cross-tenant id so another tenant's row existence is never leaked."""
    obj = db.get(model, obj_id)
    if obj is None or getattr(obj, "tenant_id", None) != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, not_found_msg)
    return obj
