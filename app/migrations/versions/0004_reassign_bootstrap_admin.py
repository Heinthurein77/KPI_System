"""reassign bootstrap admin to the default tenant

The pre-multi-tenancy bootstrap Super Admin (admin@kpi.com) was doing real
day-to-day tenant-level KPI admin work (final approvals, admin CRUD). Under
the new role model Super Admin is platform-only with no KPI involvement, so
this account becomes the Default Organization's Tenant Admin instead. A
fresh platform-level Super Admin is provisioned separately post-migration via
`python -m app.seed` (see app/seed.py) — not created here, to avoid baking a
bootstrap password into migration history.

No-op if the row doesn't exist (fresh installs never had this account).

Revision ID: 0004_reassign_bootstrap_admin
Revises: 0003_add_tenant_admin_role
Create Date: 2026-09-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004_reassign_bootstrap_admin"
down_revision: Union[str, Sequence[str], None] = "0003_add_tenant_admin_role"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BOOTSTRAP_ADMIN_EMAIL = "admin@kpi.com"
DEFAULT_TENANT_SLUG = "default"


def upgrade() -> None:
    bind = op.get_bind()
    default_tenant_id = bind.execute(
        sa.text("SELECT id FROM tenants WHERE slug = :slug"), {"slug": DEFAULT_TENANT_SLUG}
    ).scalar_one_or_none()
    if default_tenant_id is None:
        return

    bind.execute(
        sa.text(
            "UPDATE users SET role = 'TENANT_ADMIN', tenant_id = :tid "
            "WHERE email = :email AND tenant_id IS NULL"
        ),
        {"tid": default_tenant_id, "email": BOOTSTRAP_ADMIN_EMAIL},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE users SET role = 'SUPER_ADMIN', tenant_id = NULL WHERE email = :email"
        ),
        {"email": BOOTSTRAP_ADMIN_EMAIL},
    )
