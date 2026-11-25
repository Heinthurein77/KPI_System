"""Tests for the cron-log watch (scripts/check-cron-logs.sh) and the DB
backup script's log contract (scripts/backup-db.sh) — N1, 2026-11-12;
N8–N11, 2026-11-13.

The watcher must cover all three required cron jobs (deadline reminders,
audit prune, DB backup) and exit 1 on a FAILURE line, a missing log, or a
log with no runs. Since N8 it must also: only count failures in the last
24h and after the most recent SUCCESS; flag a START with no outcome older
than 2h as STALE; and flag a job with no run inside its max age (daily
36h, monthly prune 35d) as STALE.

The backup script must emit the same timestamped START / SUCCESS /
FAILURE lines as the two Python jobs (M20), or the watcher has nothing to
grep for. Since N11 the script redirects itself into backups/backup.log,
so tests read that file, not the script's stdout.

Since the 2026-11-25 Neon → server migration the script dumps the local
`db` container (`docker compose exec -T db pg_dump`), encrypts the dump
(AES-256-CBC, BACKUP_PASSPHRASE from .env, via `openssl -pass env:`),
and every run restore-verifies it into a scratch database, comparing the
migration version + row counts with live. That replaces the old N9
`pg_restore --list` TOC check and the old N10 `--env-file` credential
path (the Neon DATABASE_URL is no longer used at all).

Log fixtures use times relative to now — the watcher judges recency
against the wall clock, so fixed dates would rot. The backup-script
tests run a *copy* of the script in a temp dir (the script cds to its
own parent dir) with a stub `docker` on PATH, so no real Docker or
database is needed. `openssl` is the real host binary (the encryption
path is exercised for real).
"""

import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WATCHER = REPO_ROOT / "scripts" / "check-cron-logs.sh"
BACKUP_SCRIPT = REPO_ROOT / "scripts" / "backup-db.sh"


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _write_logs(base: Path, backup_lines=None, reminders=True, prune=True):
    """Fake the three cron logs under base/ (the watcher's $1 layout)."""
    now = datetime.now()
    if reminders:
        (base / "deadline-reminders.log").write_text(
            f"{_fmt(now - timedelta(hours=1))} INFO Deadline reminders job starting.\n"
            f"{_fmt(now - timedelta(hours=1) + timedelta(seconds=60))} INFO Deadline reminders job succeeded:"
            " window_open=True reminded=3 errors=0\n"
        )
    if prune:
        (base / "prune-audit.log").write_text(
            f"{_fmt(now - timedelta(days=2))} INFO Audit-log prune job starting.\n"
            f"{_fmt(now - timedelta(days=2) + timedelta(seconds=60))} INFO Audit-log prune job succeeded:"
            " pruned 12 audit log rows older than 180 days"
            " and 0 expired revoked-token rows.\n"
        )
    if backup_lines is not None:
        backup_dir = base / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / "backup.log").write_text("".join(backup_lines))


def _backup_ok(now=None):
    now = now or datetime.now()
    return [
        f"{_fmt(now - timedelta(hours=3))} INFO Database backup job starting.\n",
        f"{_fmt(now - timedelta(hours=3) + timedelta(seconds=5))} INFO Database backup job succeeded:"
        " wrote ./backups/kpi-backup-2026-11-12_0200.dump.enc (1.2M), restore-verified against live\n",
    ]


def _backup_failed(now=None):
    now = now or datetime.now()
    return [
        f"{_fmt(now - timedelta(hours=3))} INFO Database backup job starting.\n",
        f"{_fmt(now - timedelta(hours=3) + timedelta(seconds=2))} ERROR Database backup job FAILED.\n",
    ]


def _run_watcher(app_dir: Path):
    return subprocess.run(
        ["bash", str(WATCHER), str(app_dir)],
        capture_output=True, text=True, timeout=30,
    )


def test_watcher_all_three_ok(tmp_path):
    _write_logs(tmp_path, backup_lines=_backup_ok())
    result = _run_watcher(tmp_path)
    assert result.returncode == 0, result.stdout
    assert "deadline-reminders: OK" in result.stdout
    assert "prune-audit: OK" in result.stdout
    assert "db-backup: OK" in result.stdout


def test_watcher_backup_failure_exits_1(tmp_path):
    _write_logs(tmp_path, backup_lines=_backup_failed())
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


# --- N8: recency window, hung runs, stale schedules ---------------

def test_watcher_old_failure_then_success_is_ok(tmp_path):
    # The reviewer's fabricated case: FAILURE on 2026-11-01, SUCCESS on
    # 2026-11-12 — the daily watch must not fail forever on history.
    now = datetime.now()
    lines = [
        f"{_fmt(now - timedelta(days=3))} INFO Database backup job starting.\n",
        f"{_fmt(now - timedelta(days=3) + timedelta(seconds=2))} ERROR Database backup job FAILED.\n",
    ] + _backup_ok(now)
    _write_logs(tmp_path, backup_lines=lines)
    result = _run_watcher(tmp_path)
    assert result.returncode == 0, result.stdout
    assert "db-backup: OK" in result.stdout
    assert "ignoring 1 older failure" in result.stdout


def test_watcher_failure_before_last_success_is_recovered(tmp_path):
    # Failure inside the window, but a later SUCCESS means the job
    # recovered — the watch must not alarm on it.
    now = datetime.now()
    lines = [
        f"{_fmt(now - timedelta(hours=5))} INFO Database backup job starting.\n",
        f"{_fmt(now - timedelta(hours=5) + timedelta(seconds=2))} ERROR Database backup job FAILED.\n",
    ] + _backup_ok(now)
    _write_logs(tmp_path, backup_lines=lines)
    result = _run_watcher(tmp_path)
    assert result.returncode == 0, result.stdout
    assert "db-backup: OK" in result.stdout


def test_watcher_recent_failure_after_success_exits_1(tmp_path):
    now = datetime.now()
    lines = _backup_ok(now) + [
        f"{_fmt(now - timedelta(hours=1))} INFO Database backup job starting.\n",
        f"{_fmt(now - timedelta(hours=1) + timedelta(seconds=2))} ERROR Database backup job FAILED.\n",
    ]
    _write_logs(tmp_path, backup_lines=lines)
    result = _run_watcher(tmp_path)
    assert result.returncode == 1
    assert "db-backup: FAILURE" in result.stdout
    assert "Database backup job FAILED" in result.stdout


def test_watcher_hung_run_is_stale(tmp_path):
    # START 3h ago, no SUCCESS/FAILURE after it — killed mid-run.
    now = datetime.now()
    lines = _backup_ok(now) + [
        f"{_fmt(now - timedelta(hours=3))} INFO Database backup job starting.\n",
    ]
    _write_logs(tmp_path, backup_lines=lines)
    result = _run_watcher(tmp_path)
    assert result.returncode == 1
    assert "db-backup: STALE" in result.stdout
    assert "no SUCCESS/FAILURE" in result.stdout


def test_watcher_recently_started_run_is_running_not_failed(tmp_path):
    # START 10 minutes ago, no outcome yet — the job may legitimately
    # still be going when the watch runs.
    now = datetime.now()
    lines = _backup_ok(now) + [
        f"{_fmt(now - timedelta(minutes=10))} INFO Database backup job starting.\n",
    ]
    _write_logs(tmp_path, backup_lines=lines)
    result = _run_watcher(tmp_path)
    assert result.returncode == 0, result.stdout
    assert "db-backup: RUNNING" in result.stdout


def test_watcher_no_recent_run_is_stale(tmp_path):
    # Daily job whose last success was 3 days ago — the cron stopped.
    now = datetime.now()
    lines = [
        f"{_fmt(now - timedelta(days=3))} INFO Database backup job starting.\n",
        f"{_fmt(now - timedelta(days=3) + timedelta(seconds=5))} INFO Database backup job succeeded:"
        " wrote ./backups/kpi-backup-old.dump.enc (1.2M), restore-verified against live\n",
    ]
    _write_logs(tmp_path, backup_lines=lines)
    result = _run_watcher(tmp_path)
    assert result.returncode == 1
    assert "db-backup: STALE" in result.stdout
    assert "no run in the last 36h" in result.stdout


# --- backup-db.sh itself: run a copy in a temp "app root" ----------
# (rewritten 2026-11-25 for the local-DB script: `docker compose exec`
# instead of `docker run`, BACKUP_PASSPHRASE instead of DATABASE_URL,
# scratch-restore verification instead of `pg_restore --list`.)

FAKE_DOCKER = """#!/bin/sh
# Stub docker: emulate `docker compose exec -T db <cmd>` for backup-db.sh.
# pg_dump: print 'fake dump' to stdout (the script redirects it to the
#   dump file), or nothing when FAKE_DUMP_EMPTY=1; exit $FAKE_DOCKER_EXIT.
# pg_restore (reads the decrypted dump on stdin): consume stdin, exit
#   $FAKE_VALIDATE_EXIT when set, else 0.
# psql -t -A -c "SELECT ...": print the fingerprint
#   0025_attendance_status_check|19|87|11, or $FAKE_RESTORE_COUNTS when
#   the target database is kpi_restore_test (lets a test fake a mismatch).
# psql DROP/CREATE DATABASE: exit 0.
# Records every invocation's argv to $FAKE_ARGV_LOG when set.
if [ -n "${FAKE_ARGV_LOG:-}" ]; then
  echo "$@" >> "$FAKE_ARGV_LOG"
fi
case "$*" in
  *pg_dump*)
    if [ "${FAKE_DUMP_EMPTY:-0}" = 1 ]; then
      exit ${FAKE_DOCKER_EXIT:-0}
    fi
    printf 'fake dump'
    exit ${FAKE_DOCKER_EXIT:-0}
    ;;
  *pg_restore*)
    cat >/dev/null
    if [ -n "${FAKE_VALIDATE_EXIT:-}" ]; then exit "$FAKE_VALIDATE_EXIT"; fi
    exit 0
    ;;
  *psql*)
    db=""
    prev=""
    for a in "$@"; do
      if [ "$prev" = "-d" ]; then db="$a"; fi
      prev="$a"
    done
    case "$*" in
      *SELECT*)
        if [ "$db" = "kpi_restore_test" ] && [ -n "${FAKE_RESTORE_COUNTS:-}" ]; then
          printf '%s' "$FAKE_RESTORE_COUNTS"
        else
          printf '0025_attendance_status_check|19|87|11'
        fi
        exit 0
        ;;
    esac
    exit 0
    ;;
esac
exit 0
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
        "POSTGRES_USER=kpi\n"
        "POSTGRES_DB=kpi_system\n"
        "BACKUP_PASSPHRASE=test-backup-passphrase\n"
    )
    return app_root, bin_dir


def _run_backup(app_root, bin_dir, extra_env=None, cwd=None):
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(app_root / "scripts" / "backup-db.sh")],
        capture_output=True, text=True, timeout=30, env=env, cwd=cwd,
    )


def _backup_log(app_root):
    """The script redirects itself here (N11) — read the log, not stdout."""
    return (app_root / "backups" / "backup.log").read_text()


def test_backup_script_emits_start_success_lines(staged_backup):
    app_root, bin_dir = staged_backup
    result = _run_backup(app_root, bin_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    log = _backup_log(app_root)
    assert "Database backup job starting." in log
    assert "Database backup job succeeded: wrote" in log
    assert "restore-verified against live" in log
    assert "Database backup job FAILED" not in log
    assert list((app_root / "backups").glob("kpi-backup-*.dump.enc"))
    # No plaintext dump left behind (encryption is not optional).
    assert not list((app_root / "backups").glob("kpi-backup-*.dump"))


def test_backup_script_emits_failure_line_on_pg_dump_failure(staged_backup):
    app_root, bin_dir = staged_backup
    result = _run_backup(app_root, bin_dir, {"FAKE_DOCKER_EXIT": "1"})
    assert result.returncode != 0
    log = _backup_log(app_root)
    assert "Database backup job starting." in log
    assert "Database backup job FAILED: pg_dump failed" in log
    assert "job succeeded" not in log


def test_backup_script_emits_failure_line_when_backup_passphrase_missing(staged_backup):
    app_root, bin_dir = staged_backup
    (app_root / ".env").write_text("POSTGRES_USER=kpi\nPOSTGRES_DB=kpi_system\n")
    result = _run_backup(app_root, bin_dir)
    assert result.returncode != 0
    log = _backup_log(app_root)
    assert "Database backup job FAILED: BACKUP_PASSPHRASE not set in .env" in log


# --- verification: the scratch restore must match live -------------

def test_backup_script_fails_on_empty_dump(staged_backup):
    app_root, bin_dir = staged_backup
    result = _run_backup(app_root, bin_dir, {"FAKE_DUMP_EMPTY": "1"})
    assert result.returncode != 0
    log = _backup_log(app_root)
    assert "Database backup job FAILED: dump is empty" in log
    assert "job succeeded" not in log


def test_backup_script_fails_when_scratch_restore_fails(staged_backup):
    app_root, bin_dir = staged_backup
    result = _run_backup(app_root, bin_dir, {"FAKE_VALIDATE_EXIT": "1"})
    assert result.returncode != 0
    log = _backup_log(app_root)
    assert "Database backup job FAILED: scratch restore failed" in log
    assert "job succeeded" not in log


def test_backup_script_fails_when_scratch_counts_differ(staged_backup):
    app_root, bin_dir = staged_backup
    result = _run_backup(
        app_root, bin_dir,
        {"FAKE_RESTORE_COUNTS": "0025_attendance_status_check|19|87|10"},
    )
    assert result.returncode != 0
    log = _backup_log(app_root)
    assert "Database backup job FAILED: scratch DB does not match live" in log
    assert "job succeeded" not in log


# --- passphrase never on a command line ----------------------------

def test_backup_script_keeps_credentials_off_the_command_line(staged_backup, tmp_path):
    app_root, bin_dir = staged_backup
    argv_log = tmp_path / "argv.log"
    result = _run_backup(app_root, bin_dir, {"FAKE_ARGV_LOG": str(argv_log)})
    assert result.returncode == 0, result.stdout + result.stderr
    argv = argv_log.read_text()
    # The passphrase must never appear in any docker invocation.
    assert "test-backup-passphrase" not in argv
    # ...and the backup itself still worked (encrypted, verified).
    assert list((app_root / "backups").glob("kpi-backup-*.dump.enc"))


# --- N11: log lands in the repo's backups/ from any cwd ------------

def test_backup_script_logs_to_repo_backups_from_other_cwd(staged_backup, tmp_path):
    app_root, bin_dir = staged_backup
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    result = _run_backup(app_root, bin_dir, cwd=str(elsewhere))
    assert result.returncode == 0, result.stdout + result.stderr
    log = _backup_log(app_root)
    assert "Database backup job starting." in log
    assert "Database backup job succeeded: wrote" in log
    # No stray log file in the invocation directory.
    assert not (elsewhere / "backups").exists()
