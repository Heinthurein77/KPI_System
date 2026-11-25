"""Security fix-all pass (review 2026-11-25) — schema for the new controls.

- S-02 (TOTP/MFA): users.totp_secret_encrypted (Fernet-encrypted secret),
  users.totp_enabled, users.totp_recovery_codes (JSON list of hashes).
- S-03 (rotating refresh tokens): new refresh_tokens table (SHA-256 hash,
  token_version snapshot, expiry + last-used for the idle timeout).
- S-10 (tamper-evident audit log): audit_logs.previous_hash / entry_hash /
  ip_address, and the tenant FK goes from ON DELETE CASCADE to SET NULL
  so deleting a tenant no longer erases its audit history.
- S-15 (leave approval): attendance_records.leave_approval (NULL for
  non-leave rows and legacy leave rows, which count as approved).

The audit FK swap is Postgres-only: the FK was created unnamed in 0005,
and SQLite batch mode cannot address an unnamed constraint (reflection
gives it no name). The chain test does not compare FK actions, and dev /
test SQLite databases are built from the models by create_all, so they
already carry SET NULL — only the Postgres deployment needs the swap,
and it is looked up dynamically (never assumed) below.

Revision ID: 0026_security_fix_all
Revises: 0025_attendance_status_check
Create Date: 2026-11-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "0026_security_fix_all"
down_revision: Union[str, Sequence[str], None] = "0025_attendance_status_check"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # S-02: TOTP columns on users.
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("totp_secret_encrypted", sa.String(255), nullable=True))
        batch_op.add_column(
            sa.Column("totp_enabled", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
        batch_op.add_column(sa.Column("totp_recovery_codes", sa.Text(), nullable=True))

    # S-03: rotating refresh tokens.
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("token_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_refresh_tokens_user_id"), "refresh_tokens", ["user_id"], unique=False)
    op.create_index(
        op.f("ix_refresh_tokens_expires_at"), "refresh_tokens", ["expires_at"], unique=False
    )

    # S-10: hash-chain + IP columns on audit_logs.
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.add_column(sa.Column("previous_hash", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("entry_hash", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("ip_address", sa.String(45), nullable=True))

    # S-10: tenant FK CASCADE -> SET NULL (Postgres only — see module docstring).
    if not is_sqlite:
        fk_names = [
            c["name"]
            for c in inspect(bind).get_foreign_keys("audit_logs")
            if c["constrained_columns"] == ["tenant_id"]
        ]
        if fk_names:
            with op.batch_alter_table("audit_logs") as batch_op:
                batch_op.drop_constraint(fk_names[0], type_="foreignkey")
                batch_op.create_foreign_key(
                    "fk_audit_logs_tenant_id_tenants",
                    "tenants",
                    ["tenant_id"],
                    ["id"],
                    ondelete="SET NULL",
                )

    # S-15: leave approval state on attendance records.
    with op.batch_alter_table("attendance_records") as batch_op:
        batch_op.add_column(sa.Column("leave_approval", sa.String(20), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    with op.batch_alter_table("attendance_records") as batch_op:
        batch_op.drop_column("leave_approval")

    if not is_sqlite:
        fk_names = [
            c["name"]
            for c in inspect(bind).get_foreign_keys("audit_logs")
            if c["constrained_columns"] == ["tenant_id"]
        ]
        if fk_names:
            with op.batch_alter_table("audit_logs") as batch_op:
                batch_op.drop_constraint(fk_names[0], type_="foreignkey")
                batch_op.create_foreign_key(
                    "fk_audit_logs_tenant_id_tenants",
                    "tenants",
                    ["tenant_id"],
                    ["id"],
                    ondelete="CASCADE",
                )

    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.drop_column("ip_address")
        batch_op.drop_column("entry_hash")
        batch_op.drop_column("previous_hash")

    op.drop_index(op.f("ix_refresh_tokens_expires_at"), table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("totp_recovery_codes")
        batch_op.drop_column("totp_enabled")
        batch_op.drop_column("totp_secret_encrypted")
