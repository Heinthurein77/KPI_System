"""add daily task diary tables

Daily Task dashboard (additive-only): diary rows plus a month-end review
row per user. No existing table is touched.

Revision ID: 0016_add_daily_tasks
Revises: 0015_self_score_comment
Create Date: 2026-11-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0016_add_daily_tasks"
down_revision: Union[str, Sequence[str], None] = "0015_self_score_comment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_task_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("task_date", sa.Date(), nullable=False),
        sa.Column("plan_linkage", sa.String(length=300), nullable=True),
        sa.Column("work_done", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("figure_output", sa.String(length=300), nullable=True),
        sa.Column("evidence_link", sa.String(length=500), nullable=True),
        sa.Column("evidence_file", sa.String(length=300), nullable=True),
        sa.Column("difficulties", sa.Text(), nullable=True),
        sa.Column("support_needed", sa.Text(), nullable=True),
        sa.Column("self_reflection", sa.Text(), nullable=True),
        sa.Column("tomorrow_plan_1", sa.String(length=300), nullable=True),
        sa.Column("tomorrow_plan_2", sa.String(length=300), nullable=True),
        sa.Column("tomorrow_plan_3", sa.String(length=300), nullable=True),
        sa.Column("supervisor_feedback", sa.Text(), nullable=True),
        sa.Column("feedback_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["feedback_by_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_daily_task_entries_tenant", "daily_task_entries", ["tenant_id"])
    op.create_index("ix_daily_task_entries_user", "daily_task_entries", ["user_id"])
    op.create_index("ix_daily_task_entries_date", "daily_task_entries", ["task_date"])

    op.create_table(
        "daily_task_monthly_reviews",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("achievements", sa.Text(), nullable=True),
        sa.Column("challenges_solutions", sa.Text(), nullable=True),
        sa.Column("improvements_next_focus", sa.Text(), nullable=True),
        sa.Column("hod_evaluation", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "user_id", "year", "month",
            name="uq_daily_review_tenant_user_month",
        ),
    )


def downgrade() -> None:
    op.drop_table("daily_task_monthly_reviews")
    op.drop_index("ix_daily_task_entries_date", table_name="daily_task_entries")
    op.drop_index("ix_daily_task_entries_user", table_name="daily_task_entries")
    op.drop_index("ix_daily_task_entries_tenant", table_name="daily_task_entries")
    op.drop_table("daily_task_entries")
