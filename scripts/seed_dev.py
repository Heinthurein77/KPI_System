"""Seed the local dev tenant + users the Playwright e2e suite logs in as.

The e2e specs default to tenant slug ``abcmib`` with
``admin@local.test`` (tenant admin), ``manager@local.test`` (dept admin)
and ``aung@local.test`` (employee) — but until now nothing in the repo
created those accounts (T-M1), so a fresh checkout could not run e2e
at all. This script creates them, plus the department and the one
department KPI template the workflow spec uses.

Usage (from the repo root; the Playwright config runs it for you via
``scripts/e2e-backend.sh`` before booting the backend)::

    SEED_PASSWORD=... DATABASE_URL=sqlite:///./kpi-e2e.db python scripts/seed_dev.py

Idempotent: re-running it finds the existing tenant/users/template by
slug/email/metric name and leaves them in place (resetting the users'
passwords to the seed password, so a re-run always restores the
documented login). It never touches any other tenant, user, or KPI
data, and it must never be pointed at production: the default
password below is for local dev only.

Environment:
  SEED_PASSWORD   password for all three seed users. Default
                  ``TestPass123!`` — local dev only; set it explicitly
                  when the seed DB is shared with anyone else.
  DATABASE_URL    which database to seed (default: the app's own
                  default local SQLite file). The script runs
                  ``alembic upgrade head`` first so a fresh file has
                  its tables before any rows are inserted.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Dev-only defaults for the settings the app requires at import time.
# Real deployments set these for real; the seed script must not demand
# production secrets just to create local dev accounts.
os.environ.setdefault("SECRET_KEY", "local-development-only-not-a-real-secret")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("CORS_ORIGINS", "http://testserver")

TENANT_SLUG = "abcmib"
TENANT_NAME = "ABC MIB"
DEPARTMENT_NAME = "General"
TEMPLATE_METRIC = "E2E Workflow KPI"

# (email, name, role, in-department?)
USERS = [
    ("admin@local.test", "Admin", "tenant_admin", False),
    ("manager@local.test", "Manager", "dept_admin", True),
    ("aung@local.test", "Aung", "employee", True),
]


def _upgrade_head() -> None:
    """Create the schema on a fresh database (no-op when already at head)."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"),
         "upgrade", "head"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise SystemExit(f"seed_dev: alembic upgrade head failed:\n{result.stdout}{result.stderr}")


def run() -> dict[str, object]:
    _upgrade_head()

    from sqlalchemy import select

    from app.core.security import hash_password
    from app.database import SessionLocal
    from app.models.department import Department
    from app.models.kpi_template import KPITemplate
    from app.models.tenant import Tenant, TenantStatus
    from app.models.user import User, UserRole

    password = os.environ.get("SEED_PASSWORD", "TestPass123!")
    db = SessionLocal()
    try:
        tenant = db.scalar(select(Tenant).where(Tenant.slug == TENANT_SLUG))
        if tenant is None:
            tenant = Tenant(name=TENANT_NAME, slug=TENANT_SLUG, status=TenantStatus.ACTIVE)
            db.add(tenant)
            db.commit()
            db.refresh(tenant)
        elif tenant.status != TenantStatus.ACTIVE:
            tenant.status = TenantStatus.ACTIVE
            db.commit()

        department = db.scalar(
            select(Department).where(
                Department.tenant_id == tenant.id, Department.name == DEPARTMENT_NAME
            )
        )
        if department is None:
            department = Department(tenant_id=tenant.id, name=DEPARTMENT_NAME)
            db.add(department)
            db.commit()
            db.refresh(department)

        created_users: list[str] = []
        for email, name, role, in_dept in USERS:
            user = db.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(
                    tenant_id=tenant.id,
                    name=name,
                    email=email,
                    password_hash=hash_password(password),
                    role=UserRole(role),
                    department_id=department.id if in_dept else None,
                    is_active=True,
                )
                db.add(user)
                created_users.append(email)
            else:
                # Re-runs restore the documented login even if the
                # password was changed in the UI meanwhile.
                user.tenant_id = tenant.id
                user.password_hash = hash_password(password)
                user.role = UserRole(role)
                user.department_id = department.id if in_dept else None
                user.is_active = True
                user.must_change_password = False
            db.commit()

        template = db.scalar(
            select(KPITemplate).where(
                KPITemplate.tenant_id == tenant.id,
                KPITemplate.metric_name == TEMPLATE_METRIC,
            )
        )
        if template is None:
            template = KPITemplate(
                tenant_id=tenant.id,
                metric_name=TEMPLATE_METRIC,
                target=100.0,
                weight=100.0,
                department_id=department.id,
                employee_id=None,
                is_recurring=False,
            )
            db.add(template)
            db.commit()

        return {
            "tenant": TENANT_SLUG,
            "department": DEPARTMENT_NAME,
            "users": [email for email, _n, _r, _d in USERS],
            "created_users": created_users,
            "template": TEMPLATE_METRIC,
        }
    finally:
        db.close()


if __name__ == "__main__":
    summary = run()
    print(f"seed_dev: tenant {summary['tenant']!r} ready "
          f"({summary['department']!r} department, template {summary['template']!r})")
    print(f"seed_dev: users: {', '.join(summary['users'])}")
    if summary["created_users"]:
        print(f"seed_dev: created: {', '.join(summary['created_users'])}")
    print("seed_dev: password is SEED_PASSWORD (default TestPass123!, local dev only).")
