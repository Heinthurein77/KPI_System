"""Scratch-DB migration-chain test: the whole Alembic chain must apply
cleanly to an empty database and reach head.

This is the test the review asked for: migrations were never tested, so
a broken migration (e.g. a Postgres-only statement, or a constraint
that fails on existing data) would only surface on deploy. The chain
runs in a subprocess against a throwaway SQLite file — never against
the test suite's own database — with DATABASE_URL pointed at the
scratch file.

Covers: upgrade head from empty, the head downgrade (batch mode) and
re-upgrade, and the artifacts the Major fixes depend on (the
revoked_tokens table). The Daily Task tables were removed by 0021 and
must NOT exist at head.
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run_alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
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


def test_migration_chain_upgrades_to_head_on_scratch_db(tmp_path):
    db_path = tmp_path / "chain.db"
    result = _run_alembic(db_path, "upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr

    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert version is not None
        assert version[0] == "0021_remove_daily_tasks"

        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert "revoked_tokens" in tables
        assert "daily_task_entries" not in tables
        assert "daily_task_monthly_reviews" not in tables
        assert "attendance_settings" in tables
        assert "attendance_records" in tables
    finally:
        conn.close()

    # The head downgrade (batch mode) and re-upgrade must work too.
    result = _run_alembic(db_path, "downgrade", "-1")
    assert result.returncode == 0, result.stdout + result.stderr
    result = _run_alembic(db_path, "upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr
    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert version[0] == "0021_remove_daily_tasks"
    finally:
        conn.close()
