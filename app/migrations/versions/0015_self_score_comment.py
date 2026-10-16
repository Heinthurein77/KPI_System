"""add self_score_comment to kpi_submissions

Employees can attach a short justification ("why I gave this score") when
entering their self score. Additive-only: nullable column, no backfill
(old submissions simply show no comment).

Revision ID: 0015_self_score_comment
Revises: 0014_submission_snapshots
Create Date: 2026-10-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015_self_score_comment"
down_revision: Union[str, Sequence[str], None] = "0014_submission_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "kpi_submissions",
        sa.Column("self_score_comment", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("kpi_submissions", "self_score_comment")
