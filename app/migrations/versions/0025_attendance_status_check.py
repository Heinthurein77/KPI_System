"""Add a CHECK constraint on attendance_records.status (S-m8).

monthly_summary used to count any unrecognised status as absent, and
status was a free-text column — a corrupt or mistyped status was
silently reported as an unexplained absence. The code fix counts
unrecognised statuses separately as ``unknown``; this migration is the
DB-level backstop so such a value can never be written in the first
place: status must be 'present', 'half' or 'leave'.

Verified safe before adding: both local dev databases
(kpi_system.db, kpi-e2e.db) hold zero attendance_records rows, so no
existing row violates the constraint. The migration-chain test (which
runs the whole chain on a scratch DB) is the deploy-time guard — if a
production database ever held a violating row, the upgrade would fail
there instead of silently corrupting data.

Batch mode keeps the SQLite scratch-DB chain test working.

Revision ID: 0025_attendance_status_check
Revises: 0024_audit_log_platform_scope
Create Date: 2026-11-20
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0025_attendance_status_check"
down_revision: Union[str, Sequence[str], None] = "0024_audit_log_platform_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("attendance_records") as batch_op:
        batch_op.create_check_constraint(
            "ck_attendance_records_status",
            "status IN ('present', 'half', 'leave')",
        )


def downgrade() -> None:
    with op.batch_alter_table("attendance_records") as batch_op:
        batch_op.drop_constraint(
            "ck_attendance_records_status", type_="check"
        )
