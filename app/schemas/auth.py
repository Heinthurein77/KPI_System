from pydantic import BaseModel, Field

from app.schemas.user import UserOut


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    # Cookie-only sessions: the token is set as an httpOnly cookie and is
    # deliberately NOT returned in the body — a token in the response body
    # can leak through logs/proxies and stays usable after logout.
    user: UserOut
    # S-02: True when the password step passed but the account has TOTP
    # enrolled — no session cookie is set yet; the client must complete
    # POST /api/auth/login/totp (same endpoint for tenant + platform
    # logins) before the session exists.
    totp_required: bool = False


class TotpLoginRequest(BaseModel):
    # 6-digit authenticator code, or a single-use recovery code.
    code: str | None = Field(default=None, max_length=32)
    recovery_code: str | None = Field(default=None, max_length=32)


class TotpEnrollResponse(BaseModel):
    # Shown ONCE at enrolment: the SPA displays the otpauth:// URL as a
    # QR code (qrcode.react) with the raw secret alongside it for manual
    # entry.
    secret: str
    otpauth_url: str


class TotpVerifyEnrolmentRequest(BaseModel):
    code: str = Field(max_length=32)


class TotpRecoveryCodesResponse(BaseModel):
    # Recovery codes, shown ONCE — only hashes are stored server-side.
    recovery_codes: list[str]


class TotpStatusResponse(BaseModel):
    enabled: bool
    enrolled: bool  # a secret is stored but not yet confirmed
    recovery_codes_remaining: int
    # S-02: enforcement for admins is off by default (see security.py
    # TOTP_ENFORCE_FOR_ADMINS); surfaced so the UI can explain why
    # enrolment is optional today.
    enforced_for_admins: bool


class TotpDisableRequest(BaseModel):
    password: str = Field(max_length=128)


class TotpRegenerateRecoveryCodesRequest(BaseModel):
    password: str = Field(max_length=128)


class ChangePasswordRequest(BaseModel):
    # max_length 128 matches create/reset password schemas; the 8-char
    # minimum is enforced in the handlers (400, not 422) as before.
    # bcrypt also rejects passwords over 72 bytes (see security.py).
    # Complexity (letter + digit, common-password blocklist) and
    # new≠current are enforced in the handlers too, via
    # validate_password_complexity — schema-level validators would turn
    # those into 422s and break the existing 400 contract the tests pin.
    current_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)

