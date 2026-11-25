#!/usr/bin/env bash
# Nightly PostgreSQL backup for the KPI app (local database).
#
# Since the 2026-11-25 Neon → server migration, the production database is
# the `db` service in docker-compose.yml (Postgres 18), NOT Neon. This dumps
# from the running container — no throwaway container, no DATABASE_URL
# credential handling, and pg_dump always matches the server version.
#
# What one run does:
#   1. pg_dump -Fc from kpi-db into backups/kpi-backup-<date>_<time>.dump
#   2. Encrypts it with openssl (AES-256-CBC, passphrase BACKUP_PASSPHRASE
#      from .env) into kpi-backup-<date>_<time>.dump.enc; the plaintext
#      dump is deleted immediately. (S-14: backups must be encrypted.)
#   3. Verifies the backup by decrypting it and restoring it into a
#      scratch database (kpi_restore_test), then comparing the migration
#      version + row counts against the live database. This is the restore
#      drill, run automatically every night instead of once a year.
#   4. Prunes encrypted backups older than the newest 14 (~2 weeks).
#
# Output goes to backups/backup.log — the script redirects itself there
# (N11, 2026-11-13), so the log always lands in <repo>/backups no matter
# where the caller runs it from. The cron line therefore needs no redirect:
#
# Required cron (daily 2 AM) — `crontab -e` on the server (N2, 2026-11-12):
# this is one of the required cron jobs; see README "Server cron jobs".
#
#   0 2 * * * cd ~/kpi-app && ./scripts/backup-db.sh
#
# Setup (once, on the server): add a passphrase to .env — never commit it:
#   echo "BACKUP_PASSPHRASE=$(openssl rand -hex 24)" >> .env
# Without it the script FAILS loudly (encryption is not optional).
# Lose the passphrase and the backups are unreadable — keep a copy of it
# somewhere safe, off the server.
#
# Manual restore (destructive — overwrites the target database):
#   openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE \
#     -in backups/<file>.dump.enc | \
#     docker compose exec -T db pg_restore -U kpi -d <target-db> \
#       --no-owner --no-privileges
#   (Run with BACKUP_PASSPHRASE exported in your shell; the passphrase
#   travels via env, never on a command line. Restore into a scratch
#   database first — never straight over the live one.)
set -euo pipefail

cd "$(dirname "$0")/.." # ~/kpi-app on the server

BACKUP_DIR="./backups"
mkdir -p "$BACKUP_DIR"

# N11 (2026-11-13): redirect inside the script so the log always lands in
# <repo>/backups/backup.log regardless of the caller's working directory.
exec >> "$BACKUP_DIR/backup.log" 2>&1

# N1 (2026-11-12): same START / SUCCESS / FAILURE contract as the other
# cron jobs, so scripts/check-cron-logs.sh can watch this job too.
log_line() { echo "$(date '+%F %T') $1"; }
trap 'log_line "ERROR Database backup job FAILED."' ERR
log_line "INFO Database backup job starting."

PGUSER=$(grep -E '^POSTGRES_USER=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'" || true)
PGUSER="${PGUSER:-kpi}"
PGDB=$(grep -E '^POSTGRES_DB=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'" || true)
PGDB="${PGDB:-kpi_system}"
BACKUP_PASSPHRASE=$(grep -E '^BACKUP_PASSPHRASE=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'" || true)
if [ -z "${BACKUP_PASSPHRASE:-}" ]; then
  log_line "ERROR Database backup job FAILED: BACKUP_PASSPHRASE not set in .env (see scripts/backup-db.sh header)" >&2
  exit 1
fi

STAMP=$(date +%F_%H%M)
PLAIN="$BACKUP_DIR/kpi-backup-$STAMP.dump"
FILE="$PLAIN.enc"

# 1. Dump from the running container (exec as the container's postgres user;
#    local-socket auth needs no password — nothing secret on a command line).
if ! docker compose exec -T db pg_dump -U "$PGUSER" -d "$PGDB" -Fc > "$PLAIN"; then
  log_line "ERROR Database backup job FAILED: pg_dump failed" >&2
  rm -f "$PLAIN"
  exit 1
fi
if [ ! -s "$PLAIN" ]; then
  log_line "ERROR Database backup job FAILED: dump is empty ($PLAIN)" >&2
  rm -f "$PLAIN"
  exit 1
fi

# 2. Encrypt; the passphrase travels via env (openssl -pass env:), never
#    as a command-line argument (visible in `ps`). Delete plaintext at once.
if ! BACKUP_PASSPHRASE="$BACKUP_PASSPHRASE" openssl enc -aes-256-cbc -salt -pbkdf2 \
    -pass env:BACKUP_PASSPHRASE -in "$PLAIN" -out "$FILE"; then
  log_line "ERROR Database backup job FAILED: encryption failed" >&2
  rm -f "$PLAIN" "$FILE"
  exit 1
fi
rm -f "$PLAIN"
if [ ! -s "$FILE" ]; then
  log_line "ERROR Database backup job FAILED: encrypted backup is empty ($FILE)" >&2
  exit 1
fi

# 3. Verify: decrypt + restore into a scratch database, then compare the
#    migration version and row counts with the live database. This is the
#    restore drill — a backup that cannot be restored is not a backup.
#    pg_restore reads the archive from stdin (no temp file on disk).
SCRATCH_DB="kpi_restore_test"
docker compose exec -T db psql -U "$PGUSER" -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS $SCRATCH_DB;" -c "CREATE DATABASE $SCRATCH_DB OWNER $PGUSER;" >/dev/null
VERIFY_FAILED=0
if ! BACKUP_PASSPHRASE="$BACKUP_PASSPHRASE" openssl enc -d -aes-256-cbc -pbkdf2 \
    -pass env:BACKUP_PASSPHRASE -in "$FILE" | \
    docker compose exec -T db pg_restore -U "$PGUSER" -d "$SCRATCH_DB" --no-owner --no-privileges; then
  log_line "ERROR Database backup job FAILED: scratch restore failed (backup may be corrupt)" >&2
  VERIFY_FAILED=1
fi
if [ "$VERIFY_FAILED" -eq 0 ]; then
  CHECK_SQL="SELECT (SELECT version_num FROM alembic_version) || '|' || (SELECT count(*) FROM users) || '|' || (SELECT count(*) FROM kpi_submissions) || '|' || (SELECT count(*) FROM attendance_records);"
  LIVE=$(docker compose exec -T db psql -U "$PGUSER" -d "$PGDB" -t -A -c "$CHECK_SQL" | tr -d '[:space:]')
  RESTORED=$(docker compose exec -T db psql -U "$PGUSER" -d "$SCRATCH_DB" -t -A -c "$CHECK_SQL" | tr -d '[:space:]')
  if [ "$LIVE" != "$RESTORED" ]; then
    log_line "ERROR Database backup job FAILED: scratch DB does not match live (live=$LIVE restored=$RESTORED)" >&2
    VERIFY_FAILED=1
  fi
fi
docker compose exec -T db psql -U "$PGUSER" -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS $SCRATCH_DB;" >/dev/null
if [ "$VERIFY_FAILED" -ne 0 ]; then
  exit 1
fi

# 4. Keep the newest 14 encrypted backups (~2 weeks at one per day).
ls -t "$BACKUP_DIR"/kpi-backup-*.dump.enc 2>/dev/null | tail -n +15 | xargs -r rm --

log_line "INFO Database backup job succeeded: wrote $FILE ($(du -h "$FILE" | cut -f1)), restore-verified against live"
