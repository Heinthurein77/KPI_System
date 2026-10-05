from datetime import datetime, timedelta, timezone
from typing import NamedTuple
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


def create_session_token(user_id: int, tenant_id: int | None) -> str:
    """tenant_id is None for platform Super Admins, the tenant's id otherwise —
    carried in the token so a request's resolved tenant can be cross-checked
    against it (see deps.get_current_user) without an extra DB round trip."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "tenant_id": tenant_id, "exp": expire}
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

    return SessionClaims(user_id=user_id, tenant_id=tenant_id)
