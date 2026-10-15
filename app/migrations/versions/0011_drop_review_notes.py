"""drop review_notes table

The reviewer-notes feature was removed; drop its table. The 0009 migration
is left untouched so existing databases upgrade cleanly through the chain.

Revision ID: 0011_drop_review_notes
Revises: 0010_add_user_email_notifications
Create Date: 2026-10-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_drop_review_notes"
down_revision: Union[str, Sequence[str], None] = "0010_add_user_email_notifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f("ix_review_notes_submission_id"), table_name="review_notes")
    op.drop_index(op.f("ix_review_notes_tenant_id"), table_name="review_notes")
    op.drop_table("review_notes")


def downgrade() -> None:
    op.create_table(
        "review_notes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["submission_id"], ["kpi_submissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index(op.f("ix_review_notes_tenant_id"), "review_notes", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_review_notes_submission_id"), "review_notes", ["submission_id"], unique=False)
