"""add position column to users

Adds an optional `position` (job title / role designation, e.g. "Accountant",
"HR Manager") string column to the users table.

Nullable so every existing row is unaffected — zero change in behaviour
for all current data.

Revision ID: 0007_add_user_position
Revises: 0006_add_recurring_flag
Create Date: 2026-10-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_add_user_position"
down_revision: Union[str, Sequence[str], None] = "0006_add_recurring_flag"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("position", sa.String(120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("position")
