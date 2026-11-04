"""Widen audit_logs.entity_label from VARCHAR(255) to TEXT.

Follow-up to 0018: widening kpi_templates.metric_name to TEXT moved the
crash one step down the same transaction. Creating a custom KPI audits
with entity_label f"{metric_name} — {employee.name}", and a long metric
name plus the employee name exceeds 255 characters — so the INSERT into
audit_logs died with Postgres StringDataRightTruncation and the whole
creation (template included) rolled back, again behind the generic 500
banner. The same label is used for template create/delete/weight/rename
audits, so any of them could hit the cap once names are long.

TEXT has no cap, and the label is display-only (audit list + detail),
so widening is safe: existing values untouched, no length assumption
anywhere reads it. Batch mode keeps the SQLite scratch-DB chain test
working.

Revision ID: 0019_audit_label_text
Revises: 0018_metric_name_text
Create Date: 2026-11-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0019_audit_label_text"
down_revision: Union[str, Sequence[str], None] = "0018_metric_name_text"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.alter_column(
            "entity_label",
            type_=sa.Text(),
            existing_type=sa.String(length=255),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Only works if no audit label exceeds 255 characters; Postgres
    # refuses to narrow a VARCHAR over longer stored values.
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.alter_column(
            "entity_label",
            type_=sa.String(length=255),
            existing_type=sa.Text(),
            existing_nullable=False,
        )
