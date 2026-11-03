from pydantic import BaseModel

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
    current_password: str
    new_password: str
