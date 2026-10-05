from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.deps import get_current_user
from app.core.security import DUMMY_PASSWORD_HASH, create_session_token, hash_password, verify_password
from app.core.tenant import get_current_tenant
from app.database import get_db
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.auth import ChangePasswordRequest, LoginRequest, LoginResponse
from app.schemas.user import UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db), tenant: Tenant = Depends(get_current_tenant)):
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

    token = create_session_token(user.id, tenant_id=tenant.id)
    return LoginResponse(access_token=token, user=user)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    if len(payload.new_password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "New password must be at least 8 characters.")

    user.password_hash = hash_password(payload.new_password)
    db.commit()
