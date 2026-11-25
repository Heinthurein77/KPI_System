import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import NamedTuple
from fastapi import Response
import bcrypt
import jwt
from app.core.config import settings

JWT_ALGORITHM = "HS256"

# ---------------------------------------------------------------------------
# S-03 (security review 2026-11-25): session lifetimes. These are
# module-level constants, NOT config.py settings — config.py is owned by
# another track in this pass and the task forbids touching it. The values
# can move to Settings later; the behaviour is what matters here.
#
# Access tokens used to live 24 h (settings.ACCESS_TOKEN_EXPIRE_MINUTES
# still defaults to 24 h in config.py — that default is now overridden
# here and no longer read for session tokens). 1 h bounds a stolen token,
# and the rotating refresh token below is what keeps a session alive.
# Tokens issued before this deploy keep their own exp — there is no mass
# logout; they simply stop being issued.
# ---------------------------------------------------------------------------
ACCESS_TOKEN_EXPIRE_MINUTES = 60  # 1h
# Refresh tokens: 7 days, rotating on every use (a used token is deleted
# and replaced, so a replayed/stolen refresh token is detected — the
# replay fails because the token is already gone).
REFRESH_TOKEN_EXPIRE_DAYS = 7
# Idle timeout, enforced on refresh (S-03): a session idle for more than
# 12 h cannot be silently revived — the user logs in again.
REFRESH_TOKEN_IDLE_TIMEOUT_HOURS = 12

# S-02: TOTP enforcement for admins is OFF by default. Enrolment is
# available now; flipping this to True later (via config) would require
# SUPER_ADMIN/TENANT_ADMIN accounts to enrol before login succeeds.
# Deliberately not enforced yet — forcing it on would lock out existing
# admins who have never enrolled.
TOTP_ENFORCE_FOR_ADMINS = False
TOTP_ISSUER = "abcMIB KPI"
# TOTP enrolment is offered to SUPER_ADMIN and TENANT_ADMIN first (S-02).
# Dept admins/employees may enrol later; the endpoint enforces this set.
TOTP_ELIGIBLE_ROLES = ("super_admin", "tenant_admin")

# Name of the httpOnly refresh-token cookie (rotating refresh token, S-03).
REFRESH_COOKIE_NAME = "refresh_token"
# Short-lived cookie holding the pending second-factor step: after the
# password step succeeds for a TOTP-enrolled user, this cookie (5 min,
# httpOnly) proves the password step already passed, so the TOTP step
# cannot be brute-forced by email alone.
TOTP_PENDING_COOKIE_NAME = "totp_pending"
TOTP_PENDING_EXPIRE_MINUTES = 5

# S-05 (security review 2026-11-25): one-time password-setup link for new
# users. The welcome email carries this signed token (72h) instead of a
# plaintext initial password. Single-use: the endpoint only honours it
# while must_change_password is set, and setting the password clears that
# flag, so a second use of the same link fails.
PASSWORD_SETUP_EXPIRE_HOURS = 72

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


# ---------------------------------------------------------------------------
# S-04 (security review 2026-11-25): password complexity. The old policy
# was length-only (min 8). New passwords must be at least 8 characters
# with at least one letter and one digit, and must not be on the bundled
# common-password blocklist. The blocklist is bundled, NOT checked against
# an external breach API: this runtime's egress is unreliable, and a
# login-time network call to a third party would also leak password
# hashes/identifiers out of the deployment.
# ---------------------------------------------------------------------------
COMMON_PASSWORDS = frozenset({
    "password", "password1", "password123", "password1234", "password12345",
    "passw0rd", "passw0rd123", "12345678", "123456789", "1234567890",
    "qwerty123", "qwertyuiop", "abc12345", "abcd1234", "letmein123",
    "letmein", "welcome123", "welcome1", "admin123", "admin1234",
    "changeme", "changeme123", "test1234", "test12345", "kpi12345",
    "kpi123456", "abcmib123", "monkey123", "dragon123", "football123",
    "iloveyou123", "sunshine123", "master123", "superman123", "michael123",
    "jesus123", "shadow123", "superman1", "1234qwer", "1qaz2wsx",
})


def validate_password_complexity(plain_password: str) -> None:
    """Raise ValueError when a new password fails the complexity policy.
    Handlers catch this and return a clear 400 (matching the existing
    length-check behaviour, not a 422). The 72-byte limit is checked
    first, so an over-long password reports the length limit rather than
    a misleading complexity message."""
    validate_password_length(plain_password)
    if len(plain_password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    if not any(c.isalpha() for c in plain_password):
        raise ValueError("Password must contain at least one letter.")
    if not any(c.isdigit() for c in plain_password):
        raise ValueError("Password must contain at least one digit.")
    if plain_password.lower() in COMMON_PASSWORDS:
        raise ValueError("That password is too common — choose a less predictable one.")


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
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
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

    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
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


# ---------------------------------------------------------------------------
# Refresh tokens (S-03) — rotating, stored hashed, idle-timeout enforced.
# The raw token value is only ever in the httpOnly cookie and in memory;
# the DB holds sha256(raw). Rotation = delete the used row, insert a new
# one: replaying an already-used refresh token finds no row and fails.
# ---------------------------------------------------------------------------

def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def new_refresh_token_value() -> str:
    return secrets.token_urlsafe(48)


def set_refresh_cookie(response: Response, token: str, secure: bool) -> None:
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        token,
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_refresh_cookie(response: Response, secure: bool = False) -> None:
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/", samesite="lax", secure=secure)


def set_totp_pending_cookie(response: Response, token: str, secure: bool) -> None:
    response.set_cookie(
        TOTP_PENDING_COOKIE_NAME,
        token,
        max_age=TOTP_PENDING_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_totp_pending_cookie(response: Response, secure: bool = False) -> None:
    response.delete_cookie(TOTP_PENDING_COOKIE_NAME, path="/", samesite="lax", secure=secure)


def create_totp_pending_token(user_id: int, tenant_id: int | None) -> str:
    """Short-lived JWT proving the password step passed, so the TOTP step
    endpoint can trust the caller without re-checking the password — and
    an attacker who only knows an email cannot guess TOTP codes at all."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=TOTP_PENDING_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "tenant_id": tenant_id,
        "purpose": "totp_pending",
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_totp_pending_token(token: str) -> SessionClaims | None:
    """Decode a pending-TOTP token; returns None unless purpose matches."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("purpose") != "totp_pending":
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
    return SessionClaims(user_id=user_id, tenant_id=tenant_id, token_version=0)


def create_password_setup_token(user_id: int, token_version: int = 0) -> str:
    """Signed one-time set-password token (S-05). Expires in 72h; the
    endpoint additionally requires must_change_password (single-use:
    setting the password clears the flag) and checks the token_version
    snapshot, so any password change/reset in the meantime kills the link.
    """
    expire = datetime.now(timezone.utc) + timedelta(hours=PASSWORD_SETUP_EXPIRE_HOURS)
    payload = {
        "sub": str(user_id),
        "purpose": "password_setup",
        "token_version": token_version,
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_password_setup_token(token: str) -> SessionClaims | None:
    """Decode a password-setup token; returns the claims, or None unless
    the token is valid, unexpired, and has the password_setup purpose."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None
    if payload.get("purpose") != "password_setup":
        return None
    sub = payload.get("sub")
    if sub is None:
        return None
    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        return None
    try:
        token_version = int(payload.get("token_version", 0))
    except (TypeError, ValueError):
        return None
    return SessionClaims(user_id=user_id, tenant_id=None, token_version=token_version)


# ---------------------------------------------------------------------------
# TOTP / MFA (S-02). Secrets are encrypted at rest with Fernet, keyed by
# a SHA-256 of SECRET_KEY — a DB leak alone does not yield usable TOTP
# secrets (rotating SECRET_KEY invalidates stored secrets, which is the
# intended trade-off: users re-enrol). Requires the `cryptography` and
# `pyotp` packages (pinned in requirements.txt by the coordinator).
# Recovery codes are single-use: sha256(SECRET_KEY || code) hashes stored
# as a JSON list on the user; using one removes its hash.
# ---------------------------------------------------------------------------

def _totp_fernet():
    from cryptography.fernet import Fernet

    key = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_totp_secret(secret: str) -> str:
    return _totp_fernet().encrypt(secret.encode("utf-8")).decode("utf-8")


def decrypt_totp_secret(encrypted: str) -> str:
    return _totp_fernet().decrypt(encrypted.encode("utf-8")).decode("utf-8")


def generate_totp_secret() -> str:
    import pyotp

    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, email: str) -> str:
    import pyotp

    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=TOTP_ISSUER)


def verify_totp_code(secret: str, code: str) -> bool:
    """Accept the current code plus one step either side (valid_window=1)
    to absorb clock skew between the server and the authenticator app."""
    import pyotp

    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    try:
        return bool(pyotp.TOTP(secret).verify(code, valid_window=1))
    except Exception:
        return False


def generate_recovery_codes(count: int = 8) -> list[str]:
    """Human-typeable single-use codes, e.g. 'xk3d-9q2m' — shown once at
    enrolment; only hashes are stored."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/O/1/l to avoid misreads
    return [
        "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2))
        for _ in range(count)
    ]


def _recovery_code_hash(code: str) -> str:
    normalised = code.strip().lower().replace(" ", "").replace("-", "")
    return hashlib.sha256((settings.SECRET_KEY + "|" + normalised).encode("utf-8")).hexdigest()


def hash_recovery_codes(codes: list[str]) -> str:
    return json.dumps([_recovery_code_hash(c) for c in codes])


def recovery_codes_remaining(user) -> int:
    try:
        stored = json.loads(user.totp_recovery_codes or "[]")
    except (ValueError, TypeError):
        stored = []
    return len(stored)


def consume_recovery_code(user, code: str) -> bool:
    """True (and the code's hash removed + persisted by the caller) when
    the code matches a stored hash. Single-use: the hash is dropped."""
    if not code or not user.totp_recovery_codes:
        return False
    try:
        stored = json.loads(user.totp_recovery_codes)
    except (ValueError, TypeError):
        return False
    digest = _recovery_code_hash(code)
    if digest not in stored:
        return False
    stored.remove(digest)
    user.totp_recovery_codes = json.dumps(stored)
    return True


# --- TOTP enrolment service (shared by the tenant + platform routers) ---

def start_totp_enrolment(db, user) -> tuple[str, str]:
    """Generate a fresh secret, store it encrypted, TOTP stays disabled
    until verify_totp_enrolment confirms a correct code. Returns
    (secret, otpauth:// provisioning URI) — the secret is shown once."""
    if user.role.value not in TOTP_ELIGIBLE_ROLES:
        raise ValueError("TOTP enrolment is currently available to super admins and tenant admins only.")
    secret = generate_totp_secret()
    user.totp_secret_encrypted = encrypt_totp_secret(secret)
    user.totp_enabled = False
    user.totp_recovery_codes = None
    db.commit()
    return secret, totp_provisioning_uri(secret, user.email)


def confirm_totp_enrolment(db, user, code: str) -> list[str]:
    """Verify a code against the enrolled-but-unconfirmed secret; on
    success enable TOTP and issue fresh recovery codes (returned once)."""
    if not user.totp_secret_encrypted:
        raise ValueError("No TOTP enrolment in progress — enrol first.")
    secret = decrypt_totp_secret(user.totp_secret_encrypted)
    if not verify_totp_code(secret, code):
        raise ValueError("Invalid authenticator code.")
    codes = generate_recovery_codes()
    user.totp_enabled = True
    user.totp_recovery_codes = hash_recovery_codes(codes)
    db.commit()
    return codes


def disable_totp(db, user) -> None:
    user.totp_secret_encrypted = None
    user.totp_enabled = False
    user.totp_recovery_codes = None
    db.commit()


def regenerate_recovery_codes(db, user) -> list[str]:
    if not user.totp_enabled:
        raise ValueError("TOTP is not enabled for this account.")
    codes = generate_recovery_codes()
    user.totp_recovery_codes = hash_recovery_codes(codes)
    db.commit()
    return codes
