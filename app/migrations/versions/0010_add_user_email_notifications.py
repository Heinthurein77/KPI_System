"""add users.email_notifications preference

Lets each user opt out of workflow notification emails (in-app notifications
are unaffected). Defaults to enabled for all existing rows.

Revision ID: 0010_add_user_email_notifications
Revises: 0009_add_review_notes
Create Date: 2026-10-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_add_user_email_notifications"
down_revision: Union[str, Sequence[str], None] = "0009_add_review_notes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_notifications", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("users", "email_notifications")
