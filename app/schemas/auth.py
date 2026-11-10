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


class ChangePasswordRequest(BaseModel):
    # max_length 128 matches create/reset password schemas; the 8-char
    # minimum is enforced in the handlers (400, not 422) as before.
    # bcrypt also rejects passwords over 72 bytes (see security.py).
    current_password: str = Field(max_length=128)
    new_password: str = Field(max_length=128)
