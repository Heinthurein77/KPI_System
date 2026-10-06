"""Test data builders. Each function commits and returns the created row(s)
using the session passed in (normally the `db` fixture from conftest.py).

Auth headers are built directly from create_session_token rather than
through a real POST /api/auth/login call in most tests, so a test can set up
a specific user/role/tenant combination in one step; a handful of dedicated
auth tests exercise the real login endpoints end-to-end separately.
"""

from app.core.security import create_session_token, hash_password
from app.models.department import Department
from app.models.kpi_submission import KPISubmission, KPIStatus
from app.models.kpi_template import KPITemplate
from app.models.tenant import Tenant, TenantStatus
from app.models.user import User, UserRole

DEFAULT_PASSWORD = "Password123!"


def make_tenant(db, name="Acme", slug="acme", status=TenantStatus.ACTIVE) -> Tenant:
    tenant = Tenant(name=name, slug=slug, status=status)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


def make_department(db, tenant: Tenant, name="Engineering") -> Department:
    dept = Department(tenant_id=tenant.id, name=name)
    db.add(dept)
    db.commit()
    db.refresh(dept)
    return dept

def make_user(
    db,
    tenant: Tenant | None,
    role: UserRole,
    name="Test User",
    email=None,
    department: Department | None = None,
    is_active=True,
    password=DEFAULT_PASSWORD,
) -> User:
    email = email or f"{name.lower().replace(' ', '.')}.{role.value}@example.com"
    user = User(
        tenant_id=tenant.id if tenant else None,
        name=name,
        email=email,
        password_hash=hash_password(password),
        role=role,
        department_id=department.id if department else None,
        is_active=is_active,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_template(
    db,
    tenant: Tenant,
    metric_name="Revenue",
    target=100.0,
    weight=1.0,
    department: Department | None = None,
    employee: User | None = None,
    locked_year: int | None = None,
    locked_period: str | None = None,
    is_recurring: bool = False,
) -> KPITemplate:
    template = KPITemplate(
        tenant_id=tenant.id,
        metric_name=metric_name,
        target=target,
        weight=weight,
        department_id=department.id if department else None,
        employee_id=employee.id if employee else None,
        locked_year=locked_year,
        locked_period=locked_period,
        is_recurring=is_recurring,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


def make_submission(
    db,
    tenant: Tenant,
    employee: User,
    template: KPITemplate,
    year=2026,
    period="January",
    status=KPIStatus.DRAFT,
    self_score=None,
    dept_score=None,
    final_score=None,
    department: Department | None = None,
) -> KPISubmission:
    submission = KPISubmission(
        tenant_id=tenant.id,
        employee_id=employee.id,
        department_id=department.id if department else employee.department_id,
        kpi_template_id=template.id,
        year=year,
        month_or_quarter=period,
        status=status,
        self_score=self_score,
        dept_score=dept_score,
        final_score=final_score,
    )
    db.add(submission)
    db.commit()
    db.refresh(submission)
    return submission


def auth_headers(user: User, tenant: Tenant | None = None) -> dict[str, str]:
    """Bearer + X-Tenant-Slug headers a real client would send for this user.
    For a platform-level user (tenant_id None) no X-Tenant-Slug is sent."""
    token = create_session_token(user.id, user.tenant_id)
    headers = {"Authorization": f"Bearer {token}"}
    if tenant is not None:
        headers["X-Tenant-Slug"] = tenant.slug
    return headers
