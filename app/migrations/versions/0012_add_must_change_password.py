"""add users.must_change_password flag

Admin-created, admin-reset and bulk-imported users must change their
password on first login.

Revision ID: 0012_add_must_change_password
Revises: 0011_drop_review_notes
Create Date: 2026-10-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_add_must_change_password"
down_revision: Union[str, Sequence[str], None] = "0011_drop_review_notes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
