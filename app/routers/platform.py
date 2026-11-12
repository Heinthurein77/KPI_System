from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import require_platform_super_admin
from app.core.rate_limit import (
    LOGIN_RATE_LIMIT,
    PASSWORD_RATE_LIMIT,
    clear_failed_logins,
    is_login_locked,
    limiter,
    record_failed_login,
)
from app.core.security import DUMMY_PASSWORD_HASH, _request_is_secure, clear_session_cookie, create_session_token, hash_password, set_session_cookie, validate_password_length, verify_password
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.tenant import Tenant, TenantStatus
from app.models.user import User, UserRole
from app.schemas.audit_log import AuditLogOut
from app.schemas.auth import ChangePasswordRequest, LoginRequest, LoginResponse
from app.schemas.platform import CreateTenantRequest, TenantOut
from app.schemas.user import UserOut
from app.services import audit_service

router = APIRouter(prefix="/api/platform", tags=["platform"])


@router.post("/auth/login", response_model=LoginResponse)
@limiter.limit(LOGIN_RATE_LIMIT)
def platform_login(request: Request, response: Response, payload: LoginRequest, db: Session = Depends(get_db)):
    """Platform Super Admin login — not tenant-scoped (tenant_id IS NULL), so no
    organization slug is required. Distinct endpoint from /api/auth/login."""
    user = db.scalar(
        select(User).where(User.tenant_id.is_(None), User.email == payload.email.strip().lower())
    )
    email = payload.email.strip().lower()
    account_key = f"platform:{email}"
    if user is not None and is_login_locked(account_key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Please wait a minute and try again.")
    # Same timing-safety reasoning as app/routers/auth.py's login(): always run
    # verify_password once, against DUMMY_PASSWORD_HASH when no row matched,
    # so a nonexistent/tenant-scoped email doesn't fail measurably faster than
    # a real platform account with a wrong password.
    password_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    password_ok = verify_password(payload.password, password_hash)
    if user is None or not user.is_active or not password_ok:
        if user is not None:
            record_failed_login(account_key)
            # M7 (PM review 2026-11-12): platform login failures are audit
            # events, logged platform-scoped (tenant_id NULL — migration
            # 0024). Flood bound: the per-account lock caps logged failures
            # at 10/minute per account, and the per-IP slowapi limit caps
            # it per IP. Unknown emails have no user row to be the actor,
            # so they are not logged.
            audit_service.log(
                db, user, "login_failed", "user", user.id, user.name,
                "Failed platform login attempt.",
            )
            db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")

    clear_failed_logins(account_key)
    # M7: platform login success is an audit event. Login performs no other
    # DB write, so this commit persists only the audit row.
    audit_service.log(
        db, user, "login_success", "user", user.id, user.name,
        f"Successful login as {user.role.value}.",
    )
    db.commit()
    token = create_session_token(user.id, tenant_id=None, token_version=user.token_version or 0)
    set_session_cookie(response, token, secure=_request_is_secure(request))
    return LoginResponse(user=user)


@router.get("/auth/me", response_model=UserOut)
def platform_me(user: User = Depends(require_platform_super_admin)):
    """Session check for platform super admins. The tenant-scoped
    /api/auth/me always 401s for tenant-less accounts (by design), so the
    SPA calls this endpoint instead when no tenant slug is stored."""
    return user


@router.post("/auth/change-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(PASSWORD_RATE_LIMIT)
def platform_change_password(
    request: Request,
    payload: ChangePasswordRequest,
    response: Response,
    db: Session = Depends(get_db),
    admin: User = Depends(require_platform_super_admin),
):
    """Password change for platform super admins. The tenant-scoped
    /api/auth/change-password 401s for tenant-less accounts, which would
    trip the SPA's global 401 handler and bounce them to /login."""
    if not verify_password(payload.current_password, admin.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    if len(payload.new_password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "New password must be at least 8 characters.")
    try:
        validate_password_length(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    admin.password_hash = hash_password(payload.new_password)
    admin.must_change_password = False
    admin.token_version = (admin.token_version or 0) + 1
    db.commit()
    # The token_version bump invalidates this session — clear the now-dead
    # cookie so the SPA doesn't keep sending it. The admin logs in again.
    clear_session_cookie(response, secure=_request_is_secure(request))


@router.get("/audit-log", response_model=list[AuditLogOut])
def list_platform_audit_log(
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_platform_super_admin),
):
    """Platform-scoped audit log — ONLY rows with tenant_id IS NULL
    (tenant deletes, platform logins). Same shape and paging as the
    tenant endpoint (GET /api/admin/audit-log, limit default 200 / max
    500, newest first), but this endpoint never returns tenant-scoped
    rows: tenant admins read their own tenant's rows via the tenant
    endpoint instead. (N3, PM follow-up review 2026-11-12: before this,
    platform-scoped rows had no reader at all — only a raw SQL query.)"""
    query = (
        select(AuditLog)
        .where(AuditLog.tenant_id.is_(None))
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(min(max(limit, 1), 500))
        .offset(max(offset, 0))
    )
    entries = db.scalars(query).all()
    return [AuditLogOut.model_validate(e) for e in entries]


@router.get("/tenants", response_model=list[TenantOut])
def list_tenants(db: Session = Depends(get_db), _admin: User = Depends(require_platform_super_admin)):
    return db.scalars(select(Tenant).order_by(Tenant.created_at.desc())).all()


@router.post("/tenants", response_model=TenantOut)
def create_tenant(
    payload: CreateTenantRequest, db: Session = Depends(get_db), _admin: User = Depends(require_platform_super_admin)
):
    slug = payload.slug.strip().lower()
    if db.scalar(select(Tenant).where(Tenant.slug == slug)) is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f'A tenant with slug "{slug}" already exists.')
    # Mirrors the minimum-length check in auth.change_password — CreateTenantRequest
    # itself places no constraint on admin_password, and this is the one place a
    # brand-new tenant admin credential gets set.
    if len(payload.admin_password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Admin password must be at least 8 characters.")
    try:
        validate_password_length(payload.admin_password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    tenant = Tenant(name=payload.name.strip(), slug=slug, status=TenantStatus.ACTIVE)
    db.add(tenant)
    db.flush()  # assigns tenant.id for use below, without committing yet

    db.add(
        User(
            tenant_id=tenant.id,
            name=payload.admin_name.strip(),
            email=payload.admin_email.strip().lower(),
            password_hash=hash_password(payload.admin_password),
            role=UserRole.TENANT_ADMIN,
            department_id=None,
            must_change_password=True,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not create tenant — check the slug and admin email.")
    db.refresh(tenant)
    return tenant


@router.post("/tenants/{tenant_id}/suspend", response_model=TenantOut)
def suspend_tenant(
    tenant_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_platform_super_admin)
):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found.")
    tenant.status = TenantStatus.SUSPENDED
    db.commit()
    return tenant


@router.post("/tenants/{tenant_id}/activate", response_model=TenantOut)
def activate_tenant(
    tenant_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_platform_super_admin)
):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found.")
    tenant.status = TenantStatus.ACTIVE
    db.commit()
    return tenant


@router.delete("/tenants/{tenant_id}")
def delete_tenant(
    tenant_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_platform_super_admin)
):
    """Permanently deletes a tenant and everything under it (departments, users,
    KPI templates, submissions — all cascade via FK). Only allowed once the
    tenant is already suspended, forcing a deliberate two-step action before
    this deliberately irreversible data loss."""
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found.")
    if tenant.status != TenantStatus.SUSPENDED:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Suspend this tenant before deleting it."
        )
    # M7 (PM review 2026-11-12): tenant delete is an audit event. It is
    # logged platform-scoped (the acting admin has tenant_id NULL, so the
    # row's tenant_id is NULL — migration 0024), which is what lets the
    # record survive the tenant it describes: the FK stays ON DELETE
    # CASCADE, so a row logged under the tenant's own id would die with
    # it (pinned by test below). Logged in the same commit as the delete,
    # so the two never diverge; entity_label is a plain string captured
    # before the delete, so no expired-ORM-instance issue.
    audit_service.log(
        db, _admin, "tenant_deleted", "tenant", tenant.id, tenant.name,
        f'Tenant "{tenant.name}" (slug={tenant.slug}) deleted by platform admin.',
    )
    db.delete(tenant)
    db.commit()
    return {"status": "ok"}
