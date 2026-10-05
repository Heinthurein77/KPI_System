"""Seed a platform-level Super Admin account (tenant_id IS NULL) — no tenants,
departments, employees, or KPI metrics. Tenants are created from the Platform
portal after logging in as this account.

Run with: python -m app.seed

Override the default bootstrap account via env vars (recommended for production):
  PLATFORM_SUPER_ADMIN_NAME, PLATFORM_SUPER_ADMIN_EMAIL, PLATFORM_SUPER_ADMIN_PASSWORD
"""

import os
from sqlalchemy import select
from app.core.security import hash_password
from app.database import SessionLocal
from app.models.user import User, UserRole

SUPER_ADMIN_NAME = os.getenv("PLATFORM_SUPER_ADMIN_NAME", "Platform Super Admin")
SUPER_ADMIN_EMAIL = os.getenv("PLATFORM_SUPER_ADMIN_EMAIL", "platform-admin@kpi.com")
SUPER_ADMIN_PASSWORD = os.getenv("PLATFORM_SUPER_ADMIN_PASSWORD", "Password123!")


def run() -> None:
    # Schema is managed by Alembic migrations (see app/migrations/), applied at
    # app startup — this script only seeds data, on an already-migrated DB.
    db = SessionLocal()
    try:
        existing = db.scalar(select(User).where(User.tenant_id.is_(None)).limit(1))
        if existing is not None:
            print("A platform Super Admin already exists — skipping seed.")
            return

        db.add(
            User(
                tenant_id=None,
                name=SUPER_ADMIN_NAME,
                email=SUPER_ADMIN_EMAIL,
                password_hash=hash_password(SUPER_ADMIN_PASSWORD),
                role=UserRole.SUPER_ADMIN,
                department_id=None,
            )
        )
        db.commit()

        print("Seed complete — platform Super Admin account created:")
        print(f"  Email:    {SUPER_ADMIN_EMAIL}")
        print(f"  Password: {SUPER_ADMIN_PASSWORD}")
        if SUPER_ADMIN_PASSWORD == "Password123!":
            print("  WARNING: change this password after first login in production.")
        print("Sign in at /platform/login and create tenants from the Platform portal.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
