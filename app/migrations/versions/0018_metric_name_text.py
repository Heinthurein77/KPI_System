"""Widen kpi_templates.metric_name from VARCHAR(200) to TEXT.

Dept admins write metric names as full sentences — e.g. a title plus a
whole description paragraph in one cell — and the 200-character cap
crashed bulk import at Confirm Import: Validate passed (nothing
checked length), then the INSERT into kpi_templates died with
Postgres StringDataRightTruncation and the user saw only the generic
500 banner. TEXT has no such cap, so long names import as written.

Widening is safe: existing values are untouched, and no query, score,
or weight code reads the column with a length assumption. Batch mode
keeps the SQLite scratch-DB chain test working (SQLite cannot ALTER a
column type in place).

Revision ID: 0018_metric_name_text
Revises: 0017_diary_unique_day_revoked_tokens
Create Date: 2026-11-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0018_metric_name_text"
down_revision: Union[str, Sequence[str], None] = "0017_diary_unique_day_revoked_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("kpi_templates") as batch_op:
        batch_op.alter_column(
            "metric_name",
            type_=sa.Text(),
            existing_type=sa.String(length=200),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Only works if no metric name exceeds 200 characters; Postgres
    # refuses to narrow a VARCHAR over longer stored values.
    with op.batch_alter_table("kpi_templates") as batch_op:
        batch_op.alter_column(
            "metric_name",
            type_=sa.String(length=200),
            existing_type=sa.Text(),
            existing_nullable=False,
        )
