"""Seed a platform-level Super Admin account (tenant_id IS NULL) — no tenants,
departments, employees, or KPI metrics. Tenants are created from the Platform
portal after logging in as this account.

Run with: python -m app.seed

Override the default bootstrap account via env vars (recommended for production):
  PLATFORM_SUPER_ADMIN_NAME, PLATFORM_SUPER_ADMIN_EMAIL, PLATFORM_SUPER_ADMIN_PASSWORD

M-41 (security review 2026-11-25): the seeded account must change its
password on first login (must_change_password=True), and the password is
NEVER printed to stdout — stdout ends up in deploy logs, where a printed
credential lives forever. When no password is supplied via env, a random
one is generated and written to a 0600 temp file whose PATH is printed
instead; the operator reads the file once, signs in, and changes the
password (forced on first login).
"""

import os
import secrets
import tempfile
from sqlalchemy import select
from app.core.security import hash_password
from app.database import SessionLocal
from app.models.user import User, UserRole

SUPER_ADMIN_NAME = os.getenv("PLATFORM_SUPER_ADMIN_NAME", "Platform Super Admin")
SUPER_ADMIN_EMAIL = os.getenv("PLATFORM_SUPER_ADMIN_EMAIL", "platform-admin@kpi.com")
# Never fall back to a well-known default password: if the env var is not set,
# generate a one-time random password. It is NEVER printed (see module
# docstring) — see run() for where it goes.
if "PLATFORM_SUPER_ADMIN_PASSWORD" in os.environ:
    SUPER_ADMIN_PASSWORD = os.environ["PLATFORM_SUPER_ADMIN_PASSWORD"]
    _generated_password = False
else:
    SUPER_ADMIN_PASSWORD = secrets.token_urlsafe(24)
    _generated_password = True


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
                # M-41: a seeded bootstrap account must change its password
                # on first login — the initial credential is never a
                # standing password.
                must_change_password=True,
            )
        )
        db.commit()

        print("Seed complete — platform Super Admin account created:")
        print(f"  Email:    {SUPER_ADMIN_EMAIL}")
        # The password itself is never printed (M-41).
        if _generated_password:
            fd, path = tempfile.mkstemp(prefix="platform-admin-password-")
            with os.fdopen(fd, "w") as f:
                f.write(SUPER_ADMIN_PASSWORD)
            os.chmod(path, 0o600)
            print(f"  Password: randomly generated — read it once from {path}")
            print("  (file is mode 0600; delete it after signing in. You will be")
            print("  forced to change the password on first login.)")
        else:
            print("  Password: the one supplied via PLATFORM_SUPER_ADMIN_PASSWORD.")
            print("  You will be forced to change it on first login.")
        print("Sign in at /platform/login and create tenants from the Platform portal.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
