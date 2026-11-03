"""Unique diary day per user + server-side session revocation table.

- daily_task_entries gets a unique constraint on (tenant_id, user_id,
  task_date): one diary row per user per day. A double-submit/retry
  previously created two rows for the same day and the monthly summary
  double-counted tasks. Existing duplicates (if any) are de-duplicated
  first, keeping the earliest row — the constraint would otherwise fail
  on databases that already contain them.
- revoked_tokens backs logout revocation: a logged-out session's token
  id (jti) is recorded here until its expiry, and get_current_user
  rejects revoked tokens. Rows are pruned lazily (expired rows are
  deleted on logout).

Revision ID: 0017_diary_unique_day_revoked_tokens
Revises: 0016_add_daily_tasks
Create Date: 2026-11-03
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017_diary_unique_day_revoked_tokens"
down_revision: Union[str, Sequence[str], None] = "0016_add_daily_tasks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # De-duplicate first: keep the earliest row per (tenant, user, day).
    # Plain SQL works identically on SQLite and Postgres.
    op.execute(
        "DELETE FROM daily_task_entries WHERE id NOT IN ("
        "SELECT MIN(id) FROM daily_task_entries "
        "GROUP BY tenant_id, user_id, task_date)"
    )
    # Batch mode: SQLite cannot ALTER constraints in place, so the
    # constraint is created via the copy-and-move strategy there (on
    # Postgres batch mode is a plain ALTER). Without this, `upgrade
    # head` on a scratch SQLite DB — and any fresh SQLite deploy —
    # dies at this revision.
    with op.batch_alter_table("daily_task_entries") as batch_op:
        batch_op.create_unique_constraint(
            "uq_daily_entry_tenant_user_day",
            ["tenant_id", "user_id", "task_date"],
        )
    op.create_table(
        "revoked_tokens",
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("jti"),
    )
    op.create_index("ix_revoked_tokens_expires", "revoked_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_revoked_tokens_expires", table_name="revoked_tokens")
    op.drop_table("revoked_tokens")
    # Batch mode so the drop works on SQLite as well as Postgres.
    with op.batch_alter_table("daily_task_entries") as batch_op:
        batch_op.drop_constraint("uq_daily_entry_tenant_user_day", type_="unique")
