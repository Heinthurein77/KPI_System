#!/usr/bin/env bash
# Nightly PostgreSQL backup for the KPI app.
#
# The production database is external (Neon.tech) via DATABASE_URL in .env,
# so this dumps through a throwaway postgres container — nothing to install
# on the host.
#
# Output goes to backups/backup.log — the script redirects itself there
# (N11, 2026-11-13), so the log always lands in <repo>/backups no matter
# where the caller runs it from. The cron line therefore needs no redirect:
#
# Required cron (daily 2 AM) — `crontab -e` on the server (N2, 2026-11-12):
# this is one of the three required cron jobs; see README "Server cron jobs".
#
#   0 2 * * * cd ~/kpi-app && ./scripts/backup-db.sh
#
# (An old cron line with `>> ./backups/backup.log 2>&1` still works — the
# redirect is redundant now, and the file it opens stays empty because
# every line is written after the redirect below. Check the run with
# `tail -5 ~/kpi-app/backups/backup.log`.)
#
# Restore (destructive — overwrites the target database):
#
#   printf 'DUMP_URI=%s\n' "${DATABASE_URL/postgresql+psycopg:/postgresql:}" > /tmp/kpi-restore-env
#   chmod 600 /tmp/kpi-restore-env
#   docker run --rm --env-file /tmp/kpi-restore-env -v "$PWD/backups:/backups:ro" \
#     postgres:16-alpine sh -c 'pg_restore --clean --if-exists -d "$DUMP_URI" /backups/<file>.dump'
#   rm /tmp/kpi-restore-env
#   (The credential travels via --env-file, never on a command line — N10.
#   pg_restore's -d argument accepts the URI as a connection string; do NOT
#   use PGDATABASE for it — libpq does not parse that as a URI.
#   Note this restore path is documented, not tested here — run the M5
#   restore drill against a scratch database first.)
set -euo pipefail

cd "$(dirname "$0")/.." # ~/kpi-app on the server

BACKUP_DIR="./backups"
mkdir -p "$BACKUP_DIR"

# N11 (2026-11-13): redirect inside the script so the log always lands in
# <repo>/backups/backup.log regardless of the caller's working directory.
# A cron redirect (>> ./backups/backup.log) is evaluated by the caller's
# shell *before* this cd, so it would land in the wrong directory when run
# from elsewhere. This must happen before any output (log_line / trap).
exec >> "$BACKUP_DIR/backup.log" 2>&1

# N1 (2026-11-12): same START / SUCCESS / FAILURE contract as the other
# cron jobs (app/jobs/send_deadline_reminders.py, app/jobs/prune_audit_logs.py
# — M20, 2026-11-10), so scripts/check-cron-logs.sh can watch this job too.
# Lines are timestamped "YYYY-MM-DD HH:MM:SS" to match the Python jobs'
# logging format, which is what the watcher greps for ("job starting",
# "job succeeded", "job FAILED"). The ERR trap covers any unexpected
# failure (e.g. pg_dump failing); known checks print FAILURE explicitly.
log_line() { echo "$(date '+%F %T') $1"; }
trap 'log_line "ERROR Database backup job FAILED."' ERR
log_line "INFO Database backup job starting."

DATABASE_URL=$(grep -E '^DATABASE_URL=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'" || true)
# (The `|| true` matters: with pipefail a missing .env would otherwise
# trip the ERR trap above before this check can print its FAILURE line.)
if [ -z "${DATABASE_URL:-}" ]; then
  log_line "ERROR Database backup job FAILED: DATABASE_URL not found in .env" >&2
  exit 1
fi
# pg_dump wants a plain postgres:// URL, not SQLAlchemy's postgresql+psycopg://
DUMP_URL="${DATABASE_URL/postgresql+psycopg:/postgresql:}"

STAMP=$(date +%F_%H%M)
FILE="$BACKUP_DIR/kpi-backup-$STAMP.dump"

# N10 (2026-11-13): the credential must never appear on a command line
# (host `ps`, support screenshots, shared terminals). The old code passed
# the full URI as pg_dump's dbname argument. libpq does NOT treat
# PGDATABASE as a connection string (verified against libpq.so.5.18 —
# it falls back to the local socket), so the URI travels in a 0600
# --env-file instead and is expanded only inside the throwaway container:
# `sh -c` expands $DUMP_URI once, as a literal argument (a value is never
# re-parsed, so a password containing $(...) or backticks is still inert).
# Residual: `docker inspect` on the running container shows env values —
# that needs docker access on the host, unlike `ps` which anyone can read.
ENV_FILE=$(mktemp "${TMPDIR:-/tmp}/kpi-backup-env.XXXXXX")
chmod 600 "$ENV_FILE"
printf 'DUMP_URI=%s\nDUMP_FILE=%s\n' "$DUMP_URL" "kpi-backup-$STAMP.dump" > "$ENV_FILE"
trap 'rm -f "$ENV_FILE"' EXIT

docker run --rm \
  --env-file "$ENV_FILE" \
  -v "$PWD/$BACKUP_DIR:/backups" \
  postgres:16-alpine \
  sh -c 'pg_dump "$DUMP_URI" -Fc -f "/backups/$DUMP_FILE"'

# N9 (2026-11-13): validate the dump before declaring SUCCESS — pg_dump
# exit 0 alone does not prove the file is usable. An empty file fails the
# size check; a truncated/corrupt custom-format dump fails `pg_restore
# --list` (a read-only TOC check, no database connection needed, so no
# credential is involved here).
if [ ! -s "$FILE" ]; then
  log_line "ERROR Database backup job FAILED: dump is empty ($FILE)" >&2
  exit 1
fi
if ! docker run --rm \
  -v "$PWD/$BACKUP_DIR:/backups:ro" \
  postgres:16-alpine \
  pg_restore --list "/backups/kpi-backup-$STAMP.dump" >/dev/null; then
  log_line "ERROR Database backup job FAILED: dump failed validation (pg_restore --list)" >&2
  exit 1
fi

# Keep the newest 14 backups (~2 weeks at one per day).
ls -t "$BACKUP_DIR"/kpi-backup-*.dump 2>/dev/null | tail -n +15 | xargs -r rm --

log_line "INFO Database backup job succeeded: wrote $FILE ($(du -h "$FILE" | cut -f1))"
