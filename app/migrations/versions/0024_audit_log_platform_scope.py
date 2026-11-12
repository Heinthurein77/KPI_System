"""Allow platform-scoped audit rows: audit_logs.tenant_id nullable.

M7 (PM review 2026-11-12): tenant delete and platform login could not be
recorded with audit_service.log because audit_logs.tenant_id was NOT NULL
while the acting platform super-admin has tenant_id NULL. Making the
column nullable lets platform-scoped actions log with tenant_id NULL.

The FK stays ON DELETE CASCADE, deliberately: a row logged under a
tenant's own id dies with that tenant — its history belongs to the
tenant. Platform-scoped rows (tenant_id NULL) have no tenant FK target
and therefore survive a tenant delete, which is exactly why the tenant
delete itself is logged platform-scoped (actor = platform admin).

The tenant audit-log endpoint filters by the caller's tenant_id, so NULL
rows never leak into a tenant admin's view. Batch mode keeps the SQLite
scratch-DB chain test working.

Revision ID: 0024_audit_log_platform_scope
Revises: 0023_attendance_standalone
Create Date: 2026-11-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0024_audit_log_platform_scope"
down_revision: Union[str, Sequence[str], None] = "0023_attendance_standalone"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.alter_column(
            "tenant_id",
            type_=sa.Integer(),
            existing_type=sa.Integer(),
            nullable=True,
            existing_nullable=False,
        )


def downgrade() -> None:
    # Only works if no platform-scoped (NULL tenant_id) rows exist;
    # Postgres refuses to re-add NOT NULL over stored NULLs.
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.alter_column(
            "tenant_id",
            type_=sa.Integer(),
            existing_type=sa.Integer(),
            nullable=False,
            existing_nullable=True,
        )
