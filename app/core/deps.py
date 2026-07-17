from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decode_session_token
from app.database import get_db
from app.models.user import User, UserRole


class NotAuthenticated(HTTPException):
    """Raised for unauthenticated access; routers redirect this to /login."""

    def __init__(self) -> None:
        super().__init__(status_code=status.HTTP_303_SEE_OTHER, detail="Not authenticated")


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if not token:
        raise NotAuthenticated()

    user_id = decode_session_token(token)
    if user_id is None:
        raise NotAuthenticated()

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise NotAuthenticated()

    return user


def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> User | None:
    try:
        return get_current_user(request, db)
    except NotAuthenticated:
        return None


class RoleChecker:
    """Dependency factory restricting a route to a set of roles."""

    def __init__(self, allowed_roles: list[UserRole]) -> None:
        self.allowed_roles = set(allowed_roles)

    def __call__(self, user: User = Depends(get_current_user)) -> User:
        if user.role not in self.allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to perform this action.",
            )
        return user


require_super_admin = RoleChecker([UserRole.SUPER_ADMIN])
require_dept_admin = RoleChecker([UserRole.SUPER_ADMIN, UserRole.DEPT_ADMIN])
require_any_role = RoleChecker([UserRole.SUPER_ADMIN, UserRole.DEPT_ADMIN, UserRole.EMPLOYEE])
