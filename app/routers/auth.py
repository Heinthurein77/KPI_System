from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.deps import _session_token, get_current_user
from app.core.rate_limit import LOGIN_RATE_LIMIT, limiter
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    _request_is_secure,
    clear_session_cookie,
    create_session_token,
    decode_session_token,
    hash_password,
    set_session_cookie,
    verify_password,
)
from app.core.tenant import get_current_tenant
from app.database import get_db
from app.models.revoked_token import RevokedToken
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.auth import ChangePasswordRequest, LoginRequest, LoginResponse
from app.schemas.user import UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


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

    # Always run verify_password exactly once, against the real hash if a user
    # matched or the fixed dummy hash otherwise, so an unknown email or an
    # inactive account fails in the same amount of time as a wrong password —
    # see DUMMY_PASSWORD_HASH's comment for why this matters.
    password_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    password_ok = verify_password(payload.password, password_hash)

    if user is None or not user.is_active or not password_ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password.")

    token = create_session_token(user.id, tenant_id=tenant.id, token_version=user.token_version or 0)
    set_session_cookie(response, token, secure=_request_is_secure(request))
    return LoginResponse(user=user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    """Clear the session cookie AND revoke the token server-side, so a
    replayed cookie/token stops working immediately instead of staying
    valid until its 24 h expiry.

    Deliberately unauthenticated: it must work for platform admins (whose
    sessions fail tenant-scoped auth) and for already-expired tokens —
    in all cases the cookie is cleared and the response is 204.
    """
    token = _session_token(request)
    claims = decode_session_token(token) if token else None
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
            db.commit()
        except IntegrityError:
            # Double logout: the token is already revoked. Still 204.
            db.rollback()
    clear_session_cookie(response)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    if len(payload.new_password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "New password must be at least 8 characters.")

    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    # The token_version bump invalidates this session — clear the now-dead
    # cookie so the SPA doesn't keep sending it. The user logs in again.
    clear_session_cookie(response)
