"""add tenancy

Creates the tenants table, backfills every existing row into one "Default
Organization" tenant, and converts tenant_id to a required column everywhere
except users (platform Super Admins have tenant_id IS NULL). Safe to run
against a live database with existing data — columns are added nullable first,
backfilled, then tightened.

Revision ID: 0002_add_tenancy
Revises: 0001_baseline
Create Date: 2026-09-17
"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, table, column


# revision identifiers, used by Alembic.
revision: str = "0002_add_tenancy"
down_revision: Union[str, Sequence[str], None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_TENANT_SLUG = "default"
DEFAULT_TENANT_NAME = "Default Organization"


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    # --- 1. tenants table -------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("status", sa.Enum("ACTIVE", "SUSPENDED", name="tenantstatus"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain"),
    )
    op.create_index(op.f("ix_tenants_slug"), "tenants", ["slug"], unique=True)

    tenants_t = table(
        "tenants",
        column("id", sa.Integer),
        column("name", sa.String),
        column("slug", sa.String),
        # Must match the real column type — Postgres has a native `tenantstatus`
        # enum here, and binding it as plain String causes a DatatypeMismatch.
        column("status", sa.Enum("ACTIVE", "SUSPENDED", name="tenantstatus")),
        column("created_at", sa.DateTime),
    )
    op.bulk_insert(
        tenants_t,
        [
            {
                "name": DEFAULT_TENANT_NAME,
                "slug": DEFAULT_TENANT_SLUG,
                "status": "ACTIVE",
                "created_at": datetime.now(timezone.utc),
            }
        ],
    )
    default_tenant_id = bind.execute(
        sa.text("SELECT id FROM tenants WHERE slug = :slug"), {"slug": DEFAULT_TENANT_SLUG}
    ).scalar_one()

    # --- 2. add nullable tenant_id columns ---------------------------------
    op.add_column("departments", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.add_column("kpi_templates", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.add_column("kpi_submissions", sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("tenant_id", sa.Integer(), nullable=True))

    # --- 3. backfill everything to the default tenant ----------------------
    # SUPER_ADMIN rows are deliberately excluded: under the new role model
    # Super Admin is platform-only (tenant_id IS NULL). The one pre-existing
    # bootstrap Super Admin is handled explicitly by 0004, not swept in here.
    for tbl in ("departments", "kpi_templates", "kpi_submissions"):
        op.execute(sa.text(f"UPDATE {tbl} SET tenant_id = :tid").bindparams(tid=default_tenant_id))
    op.execute(
        sa.text("UPDATE users SET tenant_id = :tid WHERE role != 'SUPER_ADMIN'").bindparams(
            tid=default_tenant_id
        )
    )

    # --- 4. tighten constraints (batch mode: required for SQLite ALTER, a
    # transparent no-op wrapper on Postgres) --------------------------------
    # The pre-tenancy unique-on-name constraint was declared as a bare
    # `unique=True` column (no explicit name) and reflects with no name on
    # SQLite. A naming_convention makes it addressable so it can actually be
    # dropped, rather than silently surviving the batch recreate.
    dept_naming_convention = {"uq": "uq_%(table_name)s_%(column_0_name)s"}
    with op.batch_alter_table(
        "departments",
        recreate="always" if is_sqlite else "auto",
        naming_convention=dept_naming_convention,
    ) as batch_op:
        batch_op.alter_column("tenant_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_foreign_key(
            "fk_departments_tenant_id_tenants", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.create_index(op.f("ix_departments_tenant_id"), ["tenant_id"], unique=False)
        # Replace the old bare unique-on-name with a tenant-scoped one. Looked
        # up dynamically rather than assumed, since it was declared without an
        # explicit name and each dialect auto-names it differently.
        if is_sqlite:
            constraint_name = "uq_departments_name"  # guaranteed by naming_convention above
        else:
            [constraint_name] = [
                c["name"]
                for c in inspect(bind).get_unique_constraints("departments")
                if c["column_names"] == ["name"]
            ]
        batch_op.drop_constraint(constraint_name, type_="unique")
        batch_op.create_unique_constraint("uq_departments_tenant_name", ["tenant_id", "name"])

    with op.batch_alter_table("kpi_templates", recreate="always" if is_sqlite else "auto") as batch_op:
        batch_op.alter_column("tenant_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_foreign_key(
            "fk_kpi_templates_tenant_id_tenants", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.create_index(op.f("ix_kpi_templates_tenant_id"), ["tenant_id"], unique=False)

    with op.batch_alter_table("kpi_submissions", recreate="always" if is_sqlite else "auto") as batch_op:
        batch_op.alter_column("tenant_id", existing_type=sa.Integer(), nullable=False)
        batch_op.create_foreign_key(
            "fk_kpi_submissions_tenant_id_tenants", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.create_index(op.f("ix_kpi_submissions_tenant_id"), ["tenant_id"], unique=False)

    with op.batch_alter_table("users", recreate="always" if is_sqlite else "auto") as batch_op:
        # tenant_id stays nullable here — platform Super Admins have none.
        batch_op.create_foreign_key(
            "fk_users_tenant_id_tenants", "tenants", ["tenant_id"], ["id"], ondelete="CASCADE"
        )
        batch_op.create_index(op.f("ix_users_tenant_id"), ["tenant_id"], unique=False)
        # Old single-column unique index on email is replaced by two narrower ones.
        batch_op.drop_index("ix_users_email")
        batch_op.create_index("ix_users_email", ["email"], unique=False)
        batch_op.create_unique_constraint("uq_users_tenant_email", ["tenant_id", "email"])
        batch_op.create_index(
            "uq_users_platform_email",
            ["email"],
            unique=True,
            postgresql_where=sa.text("tenant_id IS NULL"),
            sqlite_where=sa.text("tenant_id IS NULL"),
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("uq_users_platform_email")
        batch_op.drop_constraint("uq_users_tenant_email", type_="unique")
        batch_op.drop_index("ix_users_email")
        batch_op.create_index("ix_users_email", ["email"], unique=True)
        batch_op.drop_index(op.f("ix_users_tenant_id"))
        batch_op.drop_constraint("fk_users_tenant_id_tenants", type_="foreignkey")
        batch_op.drop_column("tenant_id")

    with op.batch_alter_table("kpi_submissions") as batch_op:
        batch_op.drop_index(op.f("ix_kpi_submissions_tenant_id"))
        batch_op.drop_constraint("fk_kpi_submissions_tenant_id_tenants", type_="foreignkey")
        batch_op.drop_column("tenant_id")

    with op.batch_alter_table("kpi_templates") as batch_op:
        batch_op.drop_index(op.f("ix_kpi_templates_tenant_id"))
        batch_op.drop_constraint("fk_kpi_templates_tenant_id_tenants", type_="foreignkey")
        batch_op.drop_column("tenant_id")

    with op.batch_alter_table("departments") as batch_op:
        batch_op.drop_constraint("uq_departments_tenant_name", type_="unique")
        batch_op.create_unique_constraint("departments_name_key", ["name"])
        batch_op.drop_index(op.f("ix_departments_tenant_id"))
        batch_op.drop_constraint("fk_departments_tenant_id_tenants", type_="foreignkey")
        batch_op.drop_column("tenant_id")

    op.drop_index(op.f("ix_tenants_slug"), table_name="tenants")
    op.drop_table("tenants")
