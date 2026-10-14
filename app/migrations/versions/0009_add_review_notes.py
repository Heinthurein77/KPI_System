"""add review_notes table

Two-way discussion thread on KPI submissions between employees and reviewers.
New table only — no existing data touched.

Revision ID: 0009_add_review_notes
Revises: 0008_add_notifications
Create Date: 2026-10-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_add_review_notes"
down_revision: Union[str, Sequence[str], None] = "0008_add_notifications"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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


def downgrade() -> None:
    op.drop_index(op.f("ix_review_notes_submission_id"), table_name="review_notes")
    op.drop_index(op.f("ix_review_notes_tenant_id"), table_name="review_notes")
    op.drop_table("review_notes")
