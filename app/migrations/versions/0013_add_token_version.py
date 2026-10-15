"""add users.token_version

Bumped on every password change/reset; session JWTs carry the version they
were issued at, so a password change immediately invalidates all existing
sessions (fixes stolen-token survival).

Revision ID: 0013_add_token_version
Revises: 0012_add_must_change_password
Create Date: 2026-10-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_add_token_version"
down_revision: Union[str, Sequence[str], None] = "0012_add_must_change_password"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
