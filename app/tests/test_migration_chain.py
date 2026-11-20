"""Scratch-DB migration-chain test: the whole Alembic chain must apply
cleanly to an empty database and reach head, and the migrated schema must
match the SQLAlchemy models (table/column parity).

This is the test the review asked for: migrations were never tested, so
a broken migration (e.g. a Postgres-only statement, or a constraint
that fails on existing data) would only surface on deploy. The chain
runs in a subprocess against a throwaway SQLite file — never against
the test suite's own database — with DATABASE_URL pointed at the
scratch file.

Covers: upgrade head from empty, the head downgrade (batch mode) and
re-upgrade, and the artifacts the Major fixes depend on (the
revoked_tokens table). The Daily Task tables were removed by 0021 and
must NOT exist at head; the Attendance tables were removed by 0022 and
re-created by 0023 as a standalone dashboard (no KPI linkage).

Parity (T-M2): after upgrade, the migrated schema is compared against
``Base.metadata`` (what ``create_all`` would build) — table names,
column names, nullability, primary keys, and column type family.
Column *order* is deliberately not compared (migrations append new
columns where the model lists them inline), and VARCHAR lengths are
ignored: SQLite renders ``sa.Enum`` as ``VARCHAR(longest-label)``, so
the migrated ``users.role`` reads ``VARCHAR(11)`` (longest label in
the 0001 enum) while the model renders ``VARCHAR(12)`` (``tenant_admin``,
added later) — a rendering artifact, not drift; Postgres uses a real
enum type with no length, so the same comparison is exact there.
"""

import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from app.database import Base
import app.models  # noqa: F401 — populate Base.metadata

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run_alembic(database_url: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DATABASE_URL"] = database_url
    env["SECRET_KEY"] = "test-secret-key-not-for-production-use"
    env["ENVIRONMENT"] = "development"
    env["CORS_ORIGINS"] = "http://testserver"
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(REPO_ROOT / "alembic.ini"), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _type_family(type_: object) -> str:
    """Column type with lengths stripped, so VARCHAR(11) == VARCHAR(12).

    Enum types collapse to ENUM on both dialects: SQLite renders them as
    VARCHAR, Postgres keeps the named enum type.
    """
    name = re.sub(r"\(.*\)", "", str(type_).upper()).strip()
    if name in ("USERROLE", "KPISTATUS", "TENANTSTATUS"):
        return "ENUM"
    return name


def _inspected_schema(engine) -> dict[str, dict[str, tuple[str, bool, bool]]]:
    """{table: {column: (type family, nullable, is_pk)}} for a live DB."""
    inspector = inspect(engine)
    schema: dict[str, dict[str, tuple[str, bool, bool]]] = {}
    for table in inspector.get_table_names():
        if table == "alembic_version":
            continue
        pk = set(inspector.get_pk_constraint(table)["constrained_columns"])
        schema[table] = {
            col["name"]: (
                _type_family(col["type"]),
                bool(col["nullable"]),
                col["name"] in pk,
            )
            for col in inspector.get_columns(table)
        }
    return schema


def _metadata_schema() -> dict[str, dict[str, tuple[str, bool, bool]]]:
    """The same shape, straight from the SQLAlchemy models."""
    return {
        table.name: {
            column.name: (
                _type_family(column.type),
                bool(column.nullable),
                bool(column.primary_key),
            )
            for column in table.columns
        }
        for table in Base.metadata.tables.values()
    }


def assert_schema_matches_models(engine, *, label: str) -> None:
    """Fail with a readable diff when the migrated schema drifts from the
    models — e.g. a model change shipped without its migration, or a
    migration that creates a different column than the model declares."""
    migrated = _inspected_schema(engine)
    expected = _metadata_schema()
    assert set(migrated) == set(expected), (
        f"{label}: table mismatch — only migrated: {sorted(set(migrated) - set(expected))}, "
        f"only in models: {sorted(set(expected) - set(migrated))}"
    )
    for table in sorted(expected):
        assert migrated[table] == expected[table], (
            f"{label}: table {table!r} differs between migrated schema and models:\n"
            f"  only migrated: {{k: v for k, v in migrated[table].items() if k not in expected[table]}}\n"
            f"  only in models: {{k: v for k, v in expected[table].items() if k not in migrated[table]}}\n"
            f"  differing columns: {{k: (migrated[table][k], expected[table][k]) for k in expected[table] "
            f"if k in migrated[table] and migrated[table][k] != expected[table][k]}}"
        )


def test_migration_chain_upgrades_to_head_on_scratch_db(tmp_path):
    db_path = tmp_path / "chain.db"
    result = _run_alembic(f"sqlite:///{db_path}", "upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr

    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert version is not None
        assert version[0] == "0024_audit_log_platform_scope"

        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "revoked_tokens" in tables
        assert "daily_task_entries" not in tables
        assert "daily_task_monthly_reviews" not in tables
        assert "attendance_settings" in tables
        assert "attendance_records" in tables
        # 0024: audit_logs.tenant_id is nullable (platform scope) —
        # PRAGMA table_info reports notnull=0 for a nullable column.
        cols = {r[1]: r[3] for r in conn.execute("PRAGMA table_info(audit_logs)")}
        assert cols["tenant_id"] == 0
    finally:
        conn.close()

    # The migrated schema must match the models — a model change without
    # its migration (or vice versa) fails here instead of on deploy.
    assert_schema_matches_models(
        create_engine(f"sqlite:///{db_path}"), label="SQLite migration chain"
    )

    # The head downgrade (batch mode) and re-upgrade must work too.
    result = _run_alembic(f"sqlite:///{db_path}", "downgrade", "-1")
    assert result.returncode == 0, result.stdout + result.stderr
    result = _run_alembic(f"sqlite:///{db_path}", "upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr
    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert version[0] == "0024_audit_log_platform_scope"
    finally:
        conn.close()


@pytest.mark.skipif(
    not os.environ.get("MIGRATION_CHAIN_POSTGRES_URL"),
    reason="Postgres chain check runs only where a Postgres URL is provided "
    "(CI postgres-migrations job sets MIGRATION_CHAIN_POSTGRES_URL).",
)
def test_migration_chain_matches_models_on_postgres():
    """Same chain + parity check against real Postgres.

    Runs in CI (postgres-migrations job). Skipped locally unless
    MIGRATION_CHAIN_POSTGRES_URL points at a throwaway database —
    never a real one: the test upgrades it to head.
    """
    url = os.environ["MIGRATION_CHAIN_POSTGRES_URL"]
    result = _run_alembic(url, "upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr
    assert_schema_matches_models(create_engine(url), label="Postgres migration chain")
