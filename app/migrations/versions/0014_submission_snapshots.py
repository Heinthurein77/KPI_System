"""snapshot weight/target onto kpi_submissions

Template weight/target edits must not retroactively rewrite already-created
submissions' scores (the documented "future months only" guarantee for weight
edits). Each submission now carries the weight and target in force when it was
created; all score math reads the snapshot, falling back to the live template
only for rows predating this migration (backfilled below).

Revision ID: 0014_submission_snapshots
Revises: 0013_add_token_version
Create Date: 2026-10-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014_submission_snapshots"
down_revision: Union[str, Sequence[str], None] = "0013_add_token_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "kpi_submissions",
        sa.Column("weight_snapshot", sa.Float(), nullable=True),
    )
    op.add_column(
        "kpi_submissions",
        sa.Column("target_snapshot", sa.Float(), nullable=True),
    )
    # Backfill existing rows from their templates' current values so old
    # submissions keep displaying exactly what they showed before.
    # Correlated subqueries (not UPDATE..FROM) so this runs on Postgres and
    # SQLite alike.
    op.execute(
        sa.text(
            """
            UPDATE kpi_submissions
            SET weight_snapshot = (
                    SELECT t.weight FROM kpi_templates t
                    WHERE t.id = kpi_submissions.kpi_template_id
                ),
                target_snapshot = (
                    SELECT t.target FROM kpi_templates t
                    WHERE t.id = kpi_submissions.kpi_template_id
                )
            WHERE weight_snapshot IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_column("kpi_submissions", "target_snapshot")
    op.drop_column("kpi_submissions", "weight_snapshot")
