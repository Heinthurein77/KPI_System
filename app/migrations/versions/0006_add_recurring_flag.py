"""add is_recurring flag to kpi_templates

Adds an opt-in `is_recurring` boolean column to kpi_templates.
When True on a custom (employee-scoped) template, the recurrence
service will automatically carry the template forward to every new
month without requiring the admin to recreate it manually.

Defaults to False so every existing row stays a plain one-off — zero
change in behaviour for all current data.

Revision ID: 0006_add_recurring_flag
Revises: 0005_add_audit_log
Create Date: 2026-10-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_add_recurring_flag"
down_revision: Union[str, Sequence[str], None] = "0005_add_audit_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("kpi_templates") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_recurring",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("kpi_templates") as batch_op:
        batch_op.drop_column("is_recurring")
