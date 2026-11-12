"""Tests for the cron-log watch (scripts/check-cron-logs.sh) and the DB
backup script's log contract (scripts/backup-db.sh) — N1, 2026-11-12.

The watcher must cover all three required cron jobs (deadline reminders,
audit prune, DB backup) and exit 1 on a FAILURE line, a missing log, or a
log with no runs. The backup script must emit the same timestamped
START / SUCCESS / FAILURE lines as the two Python jobs (M20), or the
watcher has nothing to grep for.

The backup-script tests run a *copy* of the script in a temp dir (the
script cds to its own parent dir) with a stub `docker` on PATH, so no
real Docker or database is needed.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WATCHER = REPO_ROOT / "scripts" / "check-cron-logs.sh"
BACKUP_SCRIPT = REPO_ROOT / "scripts" / "backup-db.sh"


def _write_logs(base: Path, backup_lines=None, reminders=True, prune=True):
    """Fake the three cron logs under base/ (the watcher's $1 layout)."""
    if reminders:
        (base / "deadline-reminders.log").write_text(
            "2026-11-12 08:00:01 INFO Deadline reminders job starting.\n"
            "2026-11-12 08:00:02 INFO Deadline reminders job succeeded:"
            " window_open=True reminded=3 errors=0\n"
        )
    if prune:
        (base / "prune-audit.log").write_text(
            "2026-11-01 03:00:01 INFO Audit-log prune job starting.\n"
            "2026-11-01 03:00:02 INFO Audit-log prune job succeeded:"
            " pruned 12 audit log rows older than 180 days"
            " and 0 expired revoked-token rows.\n"
        )
    if backup_lines is not None:
        backup_dir = base / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / "backup.log").write_text("".join(backup_lines))


BACKUP_OK = [
    "2026-11-12 02:00:01 INFO Database backup job starting.\n",
    "2026-11-12 02:00:05 INFO Database backup job succeeded:"
    " wrote ./backups/kpi-backup-2026-11-12_0200.dump (1.2M)\n",
]

BACKUP_FAILED = [
    "2026-11-12 02:00:01 INFO Database backup job starting.\n",
    "2026-11-12 02:00:03 ERROR Database backup job FAILED.\n",
]


def _run_watcher(app_dir: Path):
    return subprocess.run(
        ["bash", str(WATCHER), str(app_dir)],
        capture_output=True, text=True, timeout=30,
    )


def test_watcher_all_three_ok(tmp_path):
    _write_logs(tmp_path, backup_lines=BACKUP_OK)
    result = _run_watcher(tmp_path)
    assert result.returncode == 0, result.stdout
    assert "deadline-reminders: OK" in result.stdout
    assert "prune-audit: OK" in result.stdout
    assert "db-backup: OK" in result.stdout


def test_watcher_backup_failure_exits_1(tmp_path):
    _write_logs(tmp_path, backup_lines=BACKUP_FAILED)
    result = _run_watcher(tmp_path)
    assert result.returncode == 1
    assert "db-backup: FAILURE" in result.stdout
    assert "Database backup job FAILED" in result.stdout


def test_watcher_backup_log_missing_exits_1(tmp_path):
    _write_logs(tmp_path, backup_lines=None)
    result = _run_watcher(tmp_path)
    assert result.returncode == 1
    assert "db-backup: MISSING" in result.stdout


def test_watcher_backup_log_empty_exits_1(tmp_path):
    _write_logs(tmp_path, backup_lines=[])
    result = _run_watcher(tmp_path)
    assert result.returncode == 1
    assert "db-backup: EMPTY" in result.stdout


# --- backup-db.sh itself: run a copy in a temp "app root" ----------

FAKE_DOCKER = """#!/bin/sh
# Stub docker: honour pg_dump's -f <path> by creating that file under
# ./backups, then exit with $FAKE_DOCKER_EXIT (default 0).
prev=""
for a in "$@"; do
  if [ "$prev" = "-f" ]; then f="$a"; fi
  prev="$a"
done
case "$f" in
  /backups/*) mkdir -p backups; printf 'fake dump' > "backups/${f#/backups/}" ;;
esac
exit ${FAKE_DOCKER_EXIT:-0}
"""


@pytest.fixture()
def staged_backup(tmp_path):
    """Copy backup-db.sh into tmp_path/app/scripts/ with a stub docker."""
    app_root = tmp_path / "app"
    scripts = app_root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(BACKUP_SCRIPT, scripts / "backup-db.sh")
    (scripts / "backup-db.sh").chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(0o755)
    (app_root / ".env").write_text(
        "DATABASE_URL=postgresql+psycopg://user:pass@host/db\n"
    )
    return app_root, bin_dir


def _run_backup(app_root, bin_dir, extra_env=None):
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(app_root / "scripts" / "backup-db.sh")],
        capture_output=True, text=True, timeout=30, env=env,
    )


def test_backup_script_emits_start_success_lines(staged_backup):
    app_root, bin_dir = staged_backup
    result = _run_backup(app_root, bin_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Database backup job starting." in result.stdout
    assert "Database backup job succeeded: wrote" in result.stdout
    assert "Database backup job FAILED" not in result.stdout
    assert list((app_root / "backups").glob("kpi-backup-*.dump"))


def test_backup_script_emits_failure_line_on_pg_dump_failure(staged_backup):
    app_root, bin_dir = staged_backup
    result = _run_backup(app_root, bin_dir, {"FAKE_DOCKER_EXIT": "1"})
    assert result.returncode != 0
    assert "Database backup job starting." in result.stdout
    assert "Database backup job FAILED." in result.stdout
    assert "job succeeded" not in result.stdout


def test_backup_script_emits_failure_line_when_database_url_missing(staged_backup):
    app_root, bin_dir = staged_backup
    (app_root / ".env").unlink()
    result = _run_backup(app_root, bin_dir)
    assert result.returncode != 0
    # The FAILURE line goes to stderr (like the Python jobs' logging).
    assert "Database backup job FAILED: DATABASE_URL not found in .env" in result.stderr
