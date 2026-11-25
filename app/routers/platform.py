from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import require_platform_super_admin
from app.core.rate_limit import (
    LOGIN_RATE_LIMIT,
    PASSWORD_RATE_LIMIT,
    _authenticated_user_key,
    clear_failed_logins,
    is_login_locked,
    limiter,
)
from app.core.security import DUMMY_PASSWORD_HASH, TOTP_ENFORCE_FOR_ADMINS, _request_is_secure, clear_refresh_cookie, clear_session_cookie, confirm_totp_enrolment, create_totp_pending_token, disable_totp, hash_password, recovery_codes_remaining, regenerate_recovery_codes, set_totp_pending_cookie, start_totp_enrolment, validate_password_complexity, validate_password_length, verify_password
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.refresh_token import RefreshToken
from app.models.tenant import Tenant, TenantStatus
from app.models.user import User, UserRole
from app.routers.auth import _issue_session, _login_failure, _revoke_all_sessions
from app.schemas.audit_log import AuditLogOut
from app.schemas.auth import ChangePasswordRequest, LoginRequest, LoginResponse, TotpDisableRequest, TotpEnrollResponse, TotpRecoveryCodesResponse, TotpRegenerateRecoveryCodesRequest, TotpStatusResponse, TotpVerifyEnrolmentRequest
from app.schemas.platform import CreateTenantRequest, TenantOut
from app.schemas.user import ResetPasswordRequest, UserOut
from app.services import audit_service, email_service, notification_service

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
    # M-05 (security review 2026-11-25): this endpoint mints a platform
    # token — assert role == SUPER_ADMIN before minting. The query above
    # already restricts to tenant_id IS NULL rows, so today only super
    # admins can match; the explicit check is defence-in-depth against a
    # future tenant-less non-super-admin row ever being minted a platform
    # token. A wrong-role row fails exactly like a wrong password (same
    # 401, same audit, same lockout accounting) so the failure mode does
    # not reveal which part was wrong.
    role_ok = user is not None and user.role == UserRole.SUPER_ADMIN
    if user is None or not user.is_active or not password_ok or not role_ok:
        _login_failure(db, user, None, email, account_key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")

    # S-02: TOTP second step, same as the tenant login — the shared
    # POST /api/auth/login/totp endpoint completes it (the pending token
    # carries tenant_id None for platform accounts).
    if user.totp_enabled and user.totp_secret_encrypted:
        set_totp_pending_cookie(
            response, create_totp_pending_token(user.id, None), secure=_request_is_secure(request)
        )
        audit_service.log(
            db, user, "login_totp_required", "user", user.id, user.name,
            "Password accepted; TOTP verification required.",
        )
        db.commit()
        return LoginResponse(user=user, totp_required=True)

    clear_failed_logins(account_key)
    # M7: platform login success is an audit event. Login performs no other
    # DB write, so this commit persists only the audit row.
    audit_service.log(
        db, user, "login_success", "user", user.id, user.name,
        f"Successful login as {user.role.value}.",
    )
    db.commit()
    return _issue_session(db, user, response, request)


@router.get("/auth/me", response_model=UserOut)
def platform_me(user: User = Depends(require_platform_super_admin)):
    """Session check for platform super admins. The tenant-scoped
    /api/auth/me always 401s for tenant-less accounts (by design), so the
    SPA calls this endpoint instead when no tenant slug is stored."""
    return user


@router.post("/auth/change-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(PASSWORD_RATE_LIMIT, key_func=_authenticated_user_key)
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
    # M-03: per-authenticated-user limit key (mirrors the tenant endpoint).
    if not verify_password(payload.current_password, admin.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    # S-04: new≠current + complexity, 400 like the tenant endpoint.
    # Length first, so an over-72-byte password reports the 72-byte limit.
    try:
        validate_password_length(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if verify_password(payload.new_password, admin.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "New password must differ from the current password.")
    try:
        validate_password_complexity(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    admin.password_hash = hash_password(payload.new_password)
    admin.must_change_password = False
    admin.token_version = (admin.token_version or 0) + 1
    db.query(RefreshToken).filter(RefreshToken.user_id == admin.id).delete(synchronize_session=False)
    # S-M5: platform password changes are audited like tenant ones —
    # who changed it and that all sessions were invalidated. The
    # password itself is never logged. Platform-scoped (tenant_id NULL)
    # audit rows are supported since 0024.
    audit_service.log(
        db, admin, "password_changed", "user", admin.id, admin.name,
        f"Platform password changed by {admin.name} ({admin.role.value}); "
        "all sessions invalidated (token_version bumped).",
    )
    db.commit()
    # The token_version bump invalidates this session — clear the now-dead
    # cookie so the SPA doesn't keep sending it. The admin logs in again.
    clear_session_cookie(response, secure=_request_is_secure(request))
    clear_refresh_cookie(response, secure=_request_is_secure(request))


# ---------------------------------------------------------------------------
# TOTP enrolment + management for the platform super admin (S-02).
# The tenant-scoped /api/auth/totp/* endpoints 401 for tenant-less
# accounts by design, so the platform admin manages TOTP here. The
# login second step is shared: POST /api/auth/login/totp.
# ---------------------------------------------------------------------------

@router.post("/auth/totp/enroll", response_model=TotpEnrollResponse)
def platform_totp_enroll(db: Session = Depends(get_db), admin: User = Depends(require_platform_super_admin)):
    try:
        secret, otpauth_url = start_totp_enrolment(db, admin)
    except ValueError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    audit_service.log(
        db, admin, "totp_enrolment_started", "user", admin.id, admin.name,
        "Platform TOTP enrolment started; secret stored encrypted, not yet confirmed.",
    )
    db.commit()
    return TotpEnrollResponse(secret=secret, otpauth_url=otpauth_url)


@router.post("/auth/totp/verify-enrolment", response_model=TotpRecoveryCodesResponse)
def platform_totp_verify_enrolment(
    payload: TotpVerifyEnrolmentRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_platform_super_admin),
):
    try:
        codes = confirm_totp_enrolment(db, admin, payload.code)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    audit_service.log(
        db, admin, "totp_enabled", "user", admin.id, admin.name,
        "Platform TOTP enabled; recovery codes issued.",
    )
    db.commit()
    return TotpRecoveryCodesResponse(recovery_codes=codes)


@router.get("/auth/totp/status", response_model=TotpStatusResponse)
def platform_totp_status(admin: User = Depends(require_platform_super_admin)):
    return TotpStatusResponse(
        enabled=bool(admin.totp_enabled),
        enrolled=bool(admin.totp_secret_encrypted),
        recovery_codes_remaining=recovery_codes_remaining(admin),
        enforced_for_admins=TOTP_ENFORCE_FOR_ADMINS,
    )


@router.post("/auth/totp/disable", status_code=status.HTTP_204_NO_CONTENT)
def platform_totp_disable(
    payload: TotpDisableRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_platform_super_admin),
):
    if not verify_password(payload.password, admin.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    disable_totp(db, admin)
    audit_service.log(
        db, admin, "totp_disabled", "user", admin.id, admin.name,
        "Platform TOTP disabled for this account.",
    )
    db.commit()


@router.post("/auth/totp/recovery-codes/regenerate", response_model=TotpRecoveryCodesResponse)
def platform_totp_regenerate_recovery_codes(
    payload: TotpRegenerateRecoveryCodesRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_platform_super_admin),
):
    if not verify_password(payload.password, admin.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    try:
        codes = regenerate_recovery_codes(db, admin)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    audit_service.log(
        db, admin, "totp_recovery_codes_regenerated", "user", admin.id, admin.name,
        "Platform TOTP recovery codes regenerated; previous codes invalidated.",
    )
    db.commit()
    return TotpRecoveryCodesResponse(recovery_codes=codes)


@router.post("/auth/revoke-user-sessions/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def platform_revoke_user_sessions(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_platform_super_admin),
):
    """S-03: the platform admin can revoke any user's sessions (all
    tenants) — token_version bump + refresh-token delete."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    _revoke_all_sessions(db, target)
    audit_service.log(
        db, admin, "sessions_revoked", "user", target.id, target.name,
        f"All sessions revoked for {target.name} by platform admin {admin.name}.",
        department_id=target.department_id,
    )
    db.commit()


@router.post("/users/{user_id}/reset-password", response_model=UserOut)
@limiter.limit(PASSWORD_RATE_LIMIT, key_func=_authenticated_user_key)
def platform_reset_user_password(
    request: Request,
    user_id: int,
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_platform_super_admin),
):
    """M-02 remainder: the platform admin resets a TENANT ADMIN's
    password. The tenant-scoped endpoint deliberately 403s a fellow
    tenant admin's reset ("Only the platform admin can reset another
    tenant admin's password"), and until this endpoint existed there was
    no path at all — a locked-out tenant admin could not be recovered.
    Scoped to tenant admins only: every other role is reset through the
    tenant endpoint by that tenant's own admin. Mirrors its contract
    (length check, must_change_password, token_version bump, user
    notified in-app + by email)."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    if target.role != UserRole.TENANT_ADMIN:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only tenant admins are reset from the platform — other roles "
            "are reset by their tenant admin.",
        )
    try:
        validate_password_length(payload.password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    target.password_hash = hash_password(payload.password)
    target.must_change_password = True
    target.token_version = (target.token_version or 0) + 1
    audit_service.log(
        db, admin, "user_password_reset", "user", target.id, target.name,
        f"Password reset by platform admin {admin.name}.",
        department_id=target.department_id,
    )
    emailed = notification_service.notify_user(
        db,
        tenant_id=target.tenant_id,
        user_id=target.id,
        type="password_reset",
        title="Your password was reset",
        message=(
            "Your KPI account password was reset by the platform admin. "
            "Sign in with the new password — you will be asked to change it."
        ),
        link="/login",
    )
    db.commit()
    email_service.send_for_notifications(db, emailed)
    return target


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
    # brand-new tenant admin credential gets set. S-04: the same complexity
    # policy as change-password (letter + digit, common-password blocklist).
    # Length first, so an over-72-byte password reports the 72-byte limit.
    try:
        validate_password_length(payload.admin_password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    try:
        validate_password_complexity(payload.admin_password)
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
        # B-m4: tenant creation is an audit event (was unaudited; only
        # tenant delete logged). Platform-scoped: the acting admin has
        # tenant_id NULL, so the row survives the tenant it describes.
        # Logged in the same commit as the create, so the two never
        # diverge — and a failed create rolls the audit row back too.
        audit_service.log(
            db, _admin, "tenant_created", "tenant", tenant.id, tenant.name,
            f'Tenant "{tenant.name}" (slug={tenant.slug}) created by platform admin.',
        )
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
    # B-m4: tenant suspension is an audit event (was unaudited).
    audit_service.log(
        db, _admin, "tenant_suspended", "tenant", tenant.id, tenant.name,
        f'Tenant "{tenant.name}" (slug={tenant.slug}) suspended by platform admin.',
    )
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
    # B-m4: tenant activation is an audit event (was unaudited).
    audit_service.log(
        db, _admin, "tenant_activated", "tenant", tenant.id, tenant.name,
        f'Tenant "{tenant.name}" (slug={tenant.slug}) re-activated by platform admin.',
    )
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
