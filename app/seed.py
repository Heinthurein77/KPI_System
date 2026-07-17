"""Seed the database with a single Super Admin account — no sample departments,
employees, or KPI metrics. Everything else is created from the admin UI.

Run with: python -m app.seed

Override the default bootstrap account via env vars (recommended for production):
  SUPER_ADMIN_NAME, SUPER_ADMIN_EMAIL, SUPER_ADMIN_PASSWORD
"""

import os

from sqlalchemy import select

from app.core.security import hash_password
from app.database import Base, SessionLocal, engine
from app.models.user import User, UserRole

SUPER_ADMIN_NAME = os.getenv("SUPER_ADMIN_NAME", "Super Admin")
SUPER_ADMIN_EMAIL = os.getenv("SUPER_ADMIN_EMAIL", "admin@kpi.com")
SUPER_ADMIN_PASSWORD = os.getenv("SUPER_ADMIN_PASSWORD", "Password123!")


def run() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.scalar(select(User).limit(1)) is not None:
            print("Database already has users — skipping seed.")
            return

        db.add(
            User(
                name=SUPER_ADMIN_NAME,
                email=SUPER_ADMIN_EMAIL,
                password_hash=hash_password(SUPER_ADMIN_PASSWORD),
                role=UserRole.SUPER_ADMIN,
                department_id=None,
            )
        )
        db.commit()

        print("Seed complete — Super Admin account created:")
        print(f"  Email:    {SUPER_ADMIN_EMAIL}")
        print(f"  Password: {SUPER_ADMIN_PASSWORD}")
        if SUPER_ADMIN_PASSWORD == "Password123!":
            print("  WARNING: change this password after first login in production.")
        print("Sign in and create departments, users, and KPI metrics from the admin UI.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
