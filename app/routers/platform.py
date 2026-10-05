from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import require_platform_super_admin
from app.core.security import DUMMY_PASSWORD_HASH, create_session_token, hash_password, verify_password
from app.database import get_db
from app.models.tenant import Tenant, TenantStatus
from app.models.user import User, UserRole
from app.schemas.auth import LoginRequest, LoginResponse
from app.schemas.platform import CreateTenantRequest, TenantOut

router = APIRouter(prefix="/api/platform", tags=["platform"])


@router.post("/auth/login", response_model=LoginResponse)
def platform_login(payload: LoginRequest, db: Session = Depends(get_db)):
    """Platform Super Admin login — not tenant-scoped (tenant_id IS NULL), so no
    organization slug is required. Distinct endpoint from /api/auth/login."""
    user = db.scalar(
        select(User).where(User.tenant_id.is_(None), User.email == payload.email.strip().lower())
    )
    # Same timing-safety reasoning as app/routers/auth.py's login(): always run
    # verify_password once, against DUMMY_PASSWORD_HASH when no row matched,
    # so a nonexistent/tenant-scoped email doesn't fail measurably faster than
    # a real platform account with a wrong password.
    password_hash = user.password_hash if user is not None else DUMMY_PASSWORD_HASH
    password_ok = verify_password(payload.password, password_hash)
    if user is None or not user.is_active or not password_ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password.")

    token = create_session_token(user.id, tenant_id=None)
    return LoginResponse(access_token=token, user=user)


@router.get("/tenants", response_model=list[TenantOut])
def list_tenants(db: Session = Depends(get_db), _admin: User = Depends(require_platform_super_admin)):
    return db.scalars(select(Tenant).order_by(Tenant.created_at.desc())).all()


@router.post("/tenants", response_model=TenantOut)
def create_tenant(
    payload: CreateTenantRequest, db: Session = Depends(get_db), _admin: User = Depends(require_platform_super_admin)
):
    slug = payload.slug.strip().lower()
    if db.scalar(select(Tenant).where(Tenant.slug == slug)) is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f'A tenant with slug "{slug}" already exists.')
    # Mirrors the minimum-length check in auth.change_password — CreateTenantRequest
    # itself places no constraint on admin_password, and this is the one place a
    # brand-new tenant admin credential gets set.
    if len(payload.admin_password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Admin password must be at least 8 characters.")

    tenant = Tenant(name=payload.name.strip(), slug=slug, status=TenantStatus.ACTIVE)
    db.add(tenant)
    db.flush()  # assigns tenant.id for use below, without committing yet

    db.add(
        User(
            tenant_id=tenant.id,
            name=payload.admin_name.strip(),
            email=payload.admin_email.strip().lower(),
            password_hash=hash_password(payload.admin_password),
            role=UserRole.TENANT_ADMIN,
            department_id=None,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Could not create tenant — check the slug and admin email.")
    db.refresh(tenant)
    return tenant


@router.post("/tenants/{tenant_id}/suspend", response_model=TenantOut)
def suspend_tenant(
    tenant_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_platform_super_admin)
):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found.")
    tenant.status = TenantStatus.SUSPENDED
    db.commit()
    return tenant


@router.post("/tenants/{tenant_id}/activate", response_model=TenantOut)
def activate_tenant(
    tenant_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_platform_super_admin)
):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found.")
    tenant.status = TenantStatus.ACTIVE
    db.commit()
    return tenant


@router.delete("/tenants/{tenant_id}")
def delete_tenant(
    tenant_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_platform_super_admin)
):
    """Permanently deletes a tenant and everything under it (departments, users,
    KPI templates, submissions — all cascade via FK). Only allowed once the
    tenant is already suspended, forcing a deliberate two-step action before
    this deliberately irreversible data loss."""
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found.")
    if tenant.status != TenantStatus.SUSPENDED:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Suspend this tenant before deleting it."
        )
    db.delete(tenant)
    db.commit()
    return {"status": "ok"}
