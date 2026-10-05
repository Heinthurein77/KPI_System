"""add tenant_admin enum value

Postgres requires a new enum value to be committed before it can be used by
any row in the same session, so this is its own migration/transaction.
SQLite renders Enum as VARCHAR (no native type), so this is a no-op there.

Revision ID: 0003_add_tenant_admin_role
Revises: 0002_add_tenancy
Create Date: 2026-09-17
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_add_tenant_admin_role"
down_revision: Union[str, Sequence[str], None] = "0002_add_tenancy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'TENANT_ADMIN'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for enums; downgrading this cleanly would
    # require rebuilding the type, which isn't worth it for a role label.
    pass
