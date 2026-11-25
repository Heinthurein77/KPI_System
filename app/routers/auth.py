from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.deps import _session_token, get_current_user, require_tenant_admin
from app.core.rate_limit import (
    LOGIN_RATE_LIMIT,
    PASSWORD_RATE_LIMIT,
    REFRESH_RATE_LIMIT,
    _authenticated_user_key,
    clear_failed_logins,
    is_login_locked,
    limiter,
    record_failed_login,
)
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    REFRESH_COOKIE_NAME,
    REFRESH_TOKEN_EXPIRE_DAYS,
    REFRESH_TOKEN_IDLE_TIMEOUT_HOURS,
    TOTP_ENFORCE_FOR_ADMINS,
    _request_is_secure,
    clear_refresh_cookie,
    clear_session_cookie,
    clear_totp_pending_cookie,
    confirm_totp_enrolment,
    consume_recovery_code,
    create_session_token,
    create_totp_pending_token,
    decode_session_token,
    decode_totp_pending_token,
    decrypt_totp_secret,
    disable_totp,
    hash_password,
    hash_refresh_token,
    new_refresh_token_value,
    recovery_codes_remaining,
    regenerate_recovery_codes,
    set_refresh_cookie,
    set_session_cookie,
    set_totp_pending_cookie,
    start_totp_enrolment,
    validate_password_complexity,
    validate_password_length,
    verify_password,
    verify_totp_code,
)
from app.core.tenant import get_current_tenant
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.refresh_token import RefreshToken
from app.models.revoked_token import RevokedToken
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    TotpDisableRequest,
    TotpEnrollResponse,
    TotpLoginRequest,
    TotpRecoveryCodesResponse,
    TotpRegenerateRecoveryCodesRequest,
    TotpStatusResponse,
    TotpVerifyEnrolmentRequest,
)
from app.schemas.user import UserOut
from app.services import audit_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _as_aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes for DateTime(timezone=True) columns
    in some configurations — normalise before comparing with aware now."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _log_unknown_email_login(db: Session, tenant_id: int | None, email: str) -> None:
    """M-07 (security review 2026-11-25): a failed login for an unknown
    email IS audited now — keyed by the email, not by a user row (there
    is none). actor_id is NULL, entity_id is the 0 sentinel (documented
    here, not a real user id), entity_label carries the email. Flood
    bound: the per-IP slowapi limit (10/minute) caps how fast these rows
    can be created, so an enumeration storm cannot fill the audit table."""
    db.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_id=None,
            actor_name=email,
            actor_role="unknown",
            action="login_failed",
            entity_type="user",
            entity_id=0,
            entity_label=email,
            summary="Failed login attempt for unknown email address.",
        )
    )


def _create_refresh_token_row(db: Session, user: User) -> str:
    """Insert a fresh rotating refresh token row; returns the raw value
    (only ever placed in the httpOnly cookie). Caller commits."""
    raw = new_refresh_token_value()
    now = datetime.now(timezone.utc)
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(raw),
            token_version=user.token_version or 0,
            expires_at=now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
            last_used_at=now,
        )
    )
    return raw


def _issue_session(db: Session, user: User, response: Response, request: Request) -> LoginResponse:
    """Mint a fresh access + refresh token pair after a completed login
    (password-only, or password+TOTP). Sets both httpOnly cookies."""
    secure = _request_is_secure(request)
    token = create_session_token(user.id, tenant_id=user.tenant_id, token_version=user.token_version or 0)
    set_session_cookie(response, token, secure=secure)
    set_refresh_cookie(response, _create_refresh_token_row(db, user), secure=secure)
    # The refresh row is a DB write — commit it here (callers already
    # committed their audit rows before calling this).
    db.commit()
    return LoginResponse(user=user)


def _revoke_all_sessions(db: Session, user: User) -> None:
    """S-03 revoke-all: bump token_version (kills every access token) and
    delete every refresh token row. Caller commits."""
    user.token_version = (user.token_version or 0) + 1
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id).delete(synchronize_session=False)


def _login_failure(db: Session, user: User | None, tenant_id: int | None, email: str, account_key: str) -> None:
    """Shared failure path for both login endpoints: record the lockout
    counter and audit the attempt (including unknown emails — M-07).
    Caller raises the 401 afterwards."""
    if user is not None:
        record_failed_login(account_key)
        audit_service.log(
            db, user, "login_failed", "user", user.id, user.name,
            "Failed login attempt.",
        )
    else:
        _log_unknown_email_login(db, tenant_id, email)
    db.commit()


@router.post("/login", response_model=LoginResponse)
@limiter.limit(LOGIN_RATE_LIMIT)
def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(get_current_tenant),
):
    user = db.scalar(
        select(User).where(User.tenant_id == tenant.id, User.email == payload.email.strip().lower())
    )
    email = payload.email.strip().lower()
    # Per-account limit (M8): the per-IP limit alone does not stop a
    # distributed attack (many IPs, one account). Only real accounts can
    # be locked — unknown emails are still covered by the IP limit, and
    # locking a nonexistent identifier would let anyone deny a future
    # account with that email.
    account_key = f"tenant:{tenant.id}:{email}"
    if user is not None and is_login_locked(account_key):
        # Already locked out: the 10 failures that caused the lock are
        # already audit-logged below, so a locked attempt adds no signal —
        # and logging it would let a locked account keep growing the log.
        # Lockout now escalates (S-04): 1m, then 5m, then 30m.
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts. Account temporarily locked — please wait and try again.")

    # Always run verify_password exactly once, against the real hash if a user
    # matched or the fixed dummy hash otherwise, so an unknown email or an
    # inactive account fails in the same amount of time as a wrong password —
    # see DUMMY_PASSWORD_HASH's comment for why this matters.
    password_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    password_ok = verify_password(payload.password, password_hash)

    if user is None or not user.is_active or not password_ok:
        _login_failure(db, user, tenant.id, email, account_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

    # S-02: TOTP-enrolled accounts stop here — the password step passed,
    # but no session exists yet. A short-lived pending token (cookie)
    # proves the password step for the second step; the response tells
    # the SPA to show the authenticator-code form. Not-enrolled accounts
    # (including admins who never enrolled — enforcement is off by
    # default, see TOTP_ENFORCE_FOR_ADMINS) log in password-only.
    if user.totp_enabled and user.totp_secret_encrypted:
        set_totp_pending_cookie(
            response, create_totp_pending_token(user.id, tenant.id), secure=_request_is_secure(request)
        )
        audit_service.log(
            db, user, "login_totp_required", "user", user.id, user.name,
            "Password accepted; TOTP verification required.",
        )
        db.commit()
        return LoginResponse(user=user, totp_required=True)

    clear_failed_logins(account_key)
    # M7: login success is an audit event. Login performs no other DB write,
    # so this commit persists only the audit row.
    audit_service.log(
        db, user, "login_success", "user", user.id, user.name,
        f"Successful login as {user.role.value}.",
    )
    db.commit()
    return _issue_session(db, user, response, request)


@router.post("/login/totp", response_model=LoginResponse)
@limiter.limit(LOGIN_RATE_LIMIT)
def login_totp(
    request: Request,
    response: Response,
    payload: TotpLoginRequest,
    db: Session = Depends(get_db),
):
    """Second step of login for TOTP-enrolled accounts (tenant + platform
    — the pending token carries the tenant, so one endpoint serves both).
    Accepts a 6-digit authenticator code or a single-use recovery code."""
    pending = request.cookies.get("totp_pending")
    claims = decode_totp_pending_token(pending) if pending else None
    if claims is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="TOTP session expired — please log in again.")
    user = db.get(User, claims.user_id)
    if user is None or not user.is_active or not user.totp_enabled or not user.totp_secret_encrypted:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="TOTP session expired — please log in again.")
    email = user.email
    account_key = f"tenant:{user.tenant_id}:{email}" if user.tenant_id is not None else f"platform:{email}"
    if is_login_locked(account_key):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts. Account temporarily locked — please wait and try again.")

    code_ok = False
    used_recovery = False
    if payload.recovery_code:
        used_recovery = consume_recovery_code(user, payload.recovery_code)
        code_ok = used_recovery
    elif payload.code:
        code_ok = verify_totp_code(decrypt_totp_secret(user.totp_secret_encrypted), payload.code)
    if not code_ok:
        record_failed_login(account_key)
        audit_service.log(
            db, user, "login_failed", "user", user.id, user.name,
            "Failed TOTP verification.",
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authenticator code.")

    clear_failed_logins(account_key)
    clear_totp_pending_cookie(response, secure=_request_is_secure(request))
    audit_service.log(
        db, user, "login_success", "user", user.id, user.name,
        f"Successful login as {user.role.value}"
        + (" (recovery code used)." if used_recovery else " (TOTP verified)."),
    )
    db.commit()
    return _issue_session(db, user, response, request)


@router.post("/refresh", response_model=LoginResponse)
@limiter.limit(REFRESH_RATE_LIMIT)
def refresh_session(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """S-03: rotating refresh. The refresh cookie's raw value is hashed
    and looked up; on success the used row is DELETED and a fresh pair
    (access + refresh) issued — replaying an old refresh token finds no
    row. Refused when the token expired, the session was idle longer
    than REFRESH_TOKEN_IDLE_TIMEOUT_HOURS, the user is inactive, or the
    token_version snapshot no longer matches (password changed/reset)."""
    raw = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No refresh token.")
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw)))
    now = datetime.now(timezone.utc)
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token.")
    user = db.get(User, row.user_id)
    if (
        user is None
        or not user.is_active
        or (row.token_version or 0) != (user.token_version or 0)
        or now > _as_aware(row.expires_at)
    ):
        db.delete(row)
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token no longer valid — please log in again.")
    if now - _as_aware(row.last_used_at) > timedelta(hours=REFRESH_TOKEN_IDLE_TIMEOUT_HOURS):
        db.delete(row)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session idle for too long — please log in again.",
        )
    # Rotate: the old token dies here, a new one is issued below.
    db.delete(row)
    db.commit()
    return _issue_session(db, user, response, request)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Clear the session cookie AND revoke the token server-side, so a
    replayed cookie/token stops working immediately instead of staying
    valid until its 1 h expiry.

    Deliberately unauthenticated: it must work for platform admins (whose
    sessions fail tenant-scoped auth) and for already-expired tokens —
    in all cases the cookie is cleared and the response is 204.
    """
    token = _session_token(request)
    claims = decode_session_token(token) if token else None
    secure = _request_is_secure(request)
    if claims is not None and claims.jti is not None:
        try:
            db.query(RevokedToken).filter(RevokedToken.expires_at < datetime.now(timezone.utc)).delete(
                synchronize_session=False
            )
            db.add(
                RevokedToken(
                    jti=claims.jti,
                    user_id=claims.user_id,
                    expires_at=claims.exp or datetime.now(timezone.utc),
                )
            )
            # S-09 (partial): logout is an audit event.
            user = db.get(User, claims.user_id)
            if user is not None:
                audit_service.log(
                    db, user, "logout", "user", user.id, user.name,
                    "Logged out; session token revoked.",
                )
            db.commit()
        except IntegrityError:
            # Double logout: the token is already revoked. Still 204.
            db.rollback()
    # The refresh token dies with the session too — otherwise logout
    # would leave a live 7-day token behind that silently revives it.
    raw_refresh = request.cookies.get(REFRESH_COOKIE_NAME)
    if raw_refresh:
        row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_refresh)))
        if row is not None:
            db.delete(row)
            db.commit()
    clear_session_cookie(response, secure=secure)
    clear_refresh_cookie(response, secure=secure)
    clear_totp_pending_cookie(response, secure=secure)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(PASSWORD_RATE_LIMIT, key_func=_authenticated_user_key)
def change_password(
    request: Request,
    payload: ChangePasswordRequest,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # M-03: this limit is keyed per authenticated user (not per IP), so a
    # shared office NAT cannot burn another user's budget — mirrors
    # admin.py's reset endpoint.
    # M-01 (self-change part): the current password IS checked here; the
    # admin PUT /users/{id} path that changes other users' passwords
    # lives in admin.py (another track owns it).
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    # S-04: complexity (letter + digit, common-password blocklist) plus
    # new≠current, both 400 like the existing length check. Length is
    # checked FIRST so an over-72-byte password still reports the 72-byte
    # limit instead of a misleading complexity message.
    try:
        validate_password_length(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if verify_password(payload.new_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "New password must differ from the current password.")
    try:
        validate_password_complexity(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.token_version = (user.token_version or 0) + 1
    # A password change kills refresh tokens too (the version bump alone
    # would refuse them at refresh time; deleting is the belt to the
    # braces, and revoke-all uses the same helper).
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id).delete(synchronize_session=False)
    # S-M5: a credential change plus forced session invalidation is
    # exactly the event the audit trail should capture — who changed it
    # and that all sessions were invalidated. The password itself is
    # never logged. The audit row shares the commit below.
    audit_service.log(
        db, user, "password_changed", "user", user.id, user.name,
        f"Password changed by {user.name} ({user.role.value}); "
        "all sessions invalidated (token_version bumped).",
        department_id=user.department_id,
    )
    db.commit()
    # The token_version bump invalidates this session — clear the now-dead
    # cookie so the SPA doesn't keep sending it. The user logs in again.
    clear_session_cookie(response, secure=_request_is_secure(request))
    clear_refresh_cookie(response, secure=_request_is_secure(request))

# ---------------------------------------------------------------------------
# TOTP enrolment + management (S-02). Enrolment is open to SUPER_ADMIN and
# TENANT_ADMIN first; the service layer enforces the role set.
# ---------------------------------------------------------------------------

@router.post("/totp/enroll", response_model=TotpEnrollResponse)
def totp_enroll(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        secret, otpauth_url = start_totp_enrolment(db, user)
    except ValueError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    audit_service.log(
        db, user, "totp_enrolment_started", "user", user.id, user.name,
        "TOTP enrolment started; secret stored encrypted, not yet confirmed.",
    )
    db.commit()
    return TotpEnrollResponse(secret=secret, otpauth_url=otpauth_url)


@router.post("/totp/verify-enrolment", response_model=TotpRecoveryCodesResponse)
def totp_verify_enrolment(
    payload: TotpVerifyEnrolmentRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        codes = confirm_totp_enrolment(db, user, payload.code)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    audit_service.log(
        db, user, "totp_enabled", "user", user.id, user.name,
        "TOTP enabled; recovery codes issued.",
    )
    db.commit()
    return TotpRecoveryCodesResponse(recovery_codes=codes)


@router.get("/totp/status", response_model=TotpStatusResponse)
def totp_status(user: User = Depends(get_current_user)):
    return TotpStatusResponse(
        enabled=bool(user.totp_enabled),
        enrolled=bool(user.totp_secret_encrypted),
        recovery_codes_remaining=recovery_codes_remaining(user),
        enforced_for_admins=TOTP_ENFORCE_FOR_ADMINS,
    )


@router.post("/totp/disable", status_code=status.HTTP_204_NO_CONTENT)
def totp_disable(
    payload: TotpDisableRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    disable_totp(db, user)
    audit_service.log(
        db, user, "totp_disabled", "user", user.id, user.name,
        "TOTP disabled for this account.",
    )
    db.commit()


@router.post("/totp/recovery-codes/regenerate", response_model=TotpRecoveryCodesResponse)
def totp_regenerate_recovery_codes(
    payload: TotpRegenerateRecoveryCodesRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    try:
        codes = regenerate_recovery_codes(db, user)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    audit_service.log(
        db, user, "totp_recovery_codes_regenerated", "user", user.id, user.name,
        "TOTP recovery codes regenerated; previous codes invalidated.",
    )
    db.commit()
    return TotpRecoveryCodesResponse(recovery_codes=codes)


# ---------------------------------------------------------------------------
# Revoke-all-sessions (S-03).
# ---------------------------------------------------------------------------

@router.post("/revoke-all-sessions", status_code=status.HTTP_204_NO_CONTENT)
def revoke_all_sessions(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Log out everywhere: every access token dies (token_version bump)
    and every refresh token row is deleted. The caller's own cookie is
    cleared too — they log in again."""
    _revoke_all_sessions(db, user)
    audit_service.log(
        db, user, "sessions_revoked", "user", user.id, user.name,
        "All sessions revoked by the user (token_version bumped, refresh tokens deleted).",
    )
    db.commit()
    secure = _request_is_secure(request)
    clear_session_cookie(response, secure=secure)
    clear_refresh_cookie(response, secure=secure)


@router.post("/revoke-user-sessions/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_user_sessions(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_tenant_admin),
):
    """Tenant admin revoking one of their own tenant's users' sessions
    (e.g. after a suspected compromise). Cross-tenant targets 404, like
    every other tenant-scoped lookup. Platform-wide revocation lives in
    platform.py (another endpoint in this track's partition)."""
    target = db.scalar(select(User).where(User.id == user_id, User.tenant_id == admin.tenant_id))
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    _revoke_all_sessions(db, target)
    audit_service.log(
        db, admin, "sessions_revoked", "user", target.id, target.name,
        f"All sessions revoked for {target.name} by tenant admin {admin.name}.",
        department_id=target.department_id,
    )
    db.commit()
