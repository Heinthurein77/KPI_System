"""Re-add the Attendance module tables as a standalone dashboard.

Recreates attendance_settings and attendance_records exactly as 0020
defined them. Unlike 0020, this module has NO KPI linkage: the monthly
attendance % is display-only and is never written to any KPI
submission or template (the old /calc endpoint is gone).

Revision ID: 0023_attendance_standalone
Revises: 0022_remove_attendance
Create Date: 2026-11-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0023_attendance_standalone"
down_revision: Union[str, Sequence[str], None] = "0022_remove_attendance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "attendance_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("work_days_json", sa.Text(), nullable=False, server_default="[0, 1, 2, 3, 4]"),
        sa.Column("start_time", sa.String(length=5), nullable=False, server_default="09:00"),
        sa.Column("end_time", sa.String(length=5), nullable=False, server_default="17:00"),
        sa.Column("office_ips_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("holidays_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_attendance_settings_tenant"),
    )
    op.create_index("ix_attendance_settings_tenant_id", "attendance_settings", ["tenant_id"])

    op.create_table(
        "attendance_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("record_date", sa.Date(), nullable=False),
        sa.Column("check_in", sa.Time(), nullable=True),
        sa.Column("check_out", sa.Time(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="present"),
        sa.Column("late_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("check_in_ip", sa.String(length=45), nullable=True),
        sa.Column("check_out_ip", sa.String(length=45), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employee_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "employee_id", "record_date",
            name="uq_attendance_tenant_employee_day",
        ),
    )
    op.create_index("ix_attendance_records_tenant_id", "attendance_records", ["tenant_id"])
    op.create_index("ix_attendance_records_employee_id", "attendance_records", ["employee_id"])
    op.create_index("ix_attendance_records_department_id", "attendance_records", ["department_id"])
    op.create_index("ix_attendance_records_record_date", "attendance_records", ["record_date"])


def downgrade() -> None:
    op.drop_table("attendance_records")
    op.drop_table("attendance_settings")
