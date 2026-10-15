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
    password_bytes = plain_password.encode("utf-8")[:_MAX_PASSWORD_BYTES]
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    password_bytes = plain_password.encode("utf-8")[:_MAX_PASSWORD_BYTES]
    return bcrypt.checkpw(password_bytes, password_hash.encode("utf-8"))


class SessionClaims(NamedTuple):
    user_id: int
    tenant_id: int | None
    token_version: int


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
    """True when the login request arrived over HTTPS, honoring the
    X-Forwarded-Proto header set by the reverse proxy. Only affects the
    caller's own session cookie, so trusting the header is safe."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.split(",")[0].strip().lower() == "https"


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", samesite="lax")


def create_session_token(user_id: int, tenant_id: int | None, token_version: int = 0) -> str:
    """tenant_id is None for platform Super Admins, the tenant's id otherwise —
    carried in the token so a request's resolved tenant can be cross-checked
    against it (see deps.get_current_user) without an extra DB round trip.

    token_version is bumped whenever the password changes; get_current_user
    rejects tokens whose version no longer matches the user row, so a
    password change immediately invalidates all existing sessions.
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "tenant_id": tenant_id, "ver": token_version, "exp": expire}
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

    return SessionClaims(user_id=user_id, tenant_id=tenant_id, token_version=token_version)
