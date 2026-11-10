from datetime import datetime, timedelta, timezone
from typing import NamedTuple
from fastapi import Response
import bcrypt
import jwt
from app.core.config import settings

JWT_ALGORITHM = "HS256"

# bcrypt only uses the first 72 bytes of the input.
_MAX_PASSWORD_BYTES = 72

# A fixed bcrypt hash with no corresponding plaintext (not a real credential —
# just bcrypt.hashpw of an arbitrary throwaway string, computed once offline).
# Login endpoints must run verify_password() against *something* even when no
# user row matches the submitted email, and against this same hash whether the
# account is active or not — otherwise the "no such user" / "inactive user"
# paths short-circuit before bcrypt ever runs while a real "wrong password"
# check does not, and the latency difference lets an attacker enumerate valid
# emails/active accounts by timing alone.
DUMMY_PASSWORD_HASH = "$2b$12$MmMYB4CC7fyouhVAesuW3e9TPIoyhPcVNISX.WMpDDqjHCkUU3IFC"


def hash_password(plain_password: str) -> str:
    # bcrypt silently truncates past 72 bytes — a 100-char password would
    # then be equivalent to its first 72 bytes. Refuse instead of silently
    # weakening the credential; callers validate up front and return 400.
    validate_password_length(plain_password)
    password_bytes = plain_password.encode("utf-8")
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    # A password longer than 72 bytes can never match a hash created by
    # hash_password (which refuses them), so it is simply a wrong password —
    # return False rather than truncating and comparing a prefix.
    if len(plain_password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        return False
    password_bytes = plain_password.encode("utf-8")
    return bcrypt.checkpw(password_bytes, password_hash.encode("utf-8"))


def validate_password_length(plain_password: str) -> None:
    """Raise ValueError when a password exceeds bcrypt's 72-byte limit.
    Handlers catch this and return a clear 400."""
    if len(plain_password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        raise ValueError("Password must be at most 72 bytes long.")


class SessionClaims(NamedTuple):
    user_id: int
    tenant_id: int | None
    token_version: int
    # Unique token id: logout records it in revoked_tokens so the token
    # stops working immediately. Tokens without a jti are rejected by
    # the auth dependency (every current token carries one).
    jti: str | None = None
    exp: datetime | None = None


#: Name of the httpOnly session cookie. The JWT is readable by JS nowhere —
#: it lives only in this cookie, so XSS cannot steal sessions.
SESSION_COOKIE_NAME = "access_token"


def set_session_cookie(response: Response, token: str, secure: bool) -> None:
    """Attach the session JWT as an httpOnly cookie.

    ``secure`` must reflect the scheme the login request arrived on: a Secure
    cookie set over plain HTTP is silently dropped by the browser, which would
    make login impossible on HTTP-only deployments. Callers derive it from
    the request (see _request_is_secure).
    """
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _request_is_secure(request) -> bool:
    """True when the login request arrived over HTTPS. Delegates to the
    trusted-proxy rule (app/core/proxy.py): X-Forwarded-Proto is honoured
    only from a trusted proxy peer, so a direct client cannot claim HTTPS
    (which would put the Secure flag — and HSTS — on a plain-HTTP response).
    Only affects the caller's own session cookie, so trusting the header
    from the proxy is safe."""
    from app.core.proxy import is_secure_request

    return is_secure_request(request)


def clear_session_cookie(response: Response, secure: bool = False) -> None:
    """Delete the session cookie. ``secure`` must mirror how the cookie was
    set (see set_session_cookie): a non-secure Set-Cookie cannot overwrite
    a Secure cookie, so on HTTPS deployments logout would otherwise leave
    a stale cookie behind. Callers pass secure=_request_is_secure(request);
    on plain HTTP this is False, identical to the old behaviour."""
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", samesite="lax", secure=secure)


def create_session_token(user_id: int, tenant_id: int | None, token_version: int = 0) -> str:
    """tenant_id is None for platform Super Admins, the tenant's id otherwise —
    carried in the token so a request's resolved tenant can be cross-checked
    against it (see deps.get_current_user) without an extra DB round trip.

    token_version is bumped whenever the password changes; get_current_user
    rejects tokens whose version no longer matches the user row, so a
    password change immediately invalidates all existing sessions.

    jti is a per-token id: logout records it in revoked_tokens, so logout
    actually kills that token instead of only clearing the browser cookie.
    """
    import uuid

    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "tenant_id": tenant_id, "ver": token_version, "exp": expire, "jti": uuid.uuid4().hex}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_session_token(token: str) -> SessionClaims | None:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None

    sub = payload.get("sub")
    if sub is None:
        return None
    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        return None

    tenant_id = payload.get("tenant_id")
    if tenant_id is not None:
        try:
            tenant_id = int(tenant_id)
        except (TypeError, ValueError):
            return None

    try:
        token_version = int(payload.get("ver", 0))
    except (TypeError, ValueError):
        return None

    exp = payload.get("exp")
    exp_dt = None
    if isinstance(exp, (int, float)):
        exp_dt = datetime.fromtimestamp(exp, tz=timezone.utc)
    jti = payload.get("jti")
    if jti is not None and not isinstance(jti, str):
        return None

    return SessionClaims(user_id=user_id, tenant_id=tenant_id, token_version=token_version, jti=jti, exp=exp_dt)
