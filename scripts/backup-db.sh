#!/usr/bin/env bash
# Nightly PostgreSQL backup for the KPI app.
#
# The production database is external (Neon.tech) via DATABASE_URL in .env,
# so this dumps through a throwaway postgres container — nothing to install
# on the host. Run from ~/kpi-app:
#
#   ./scripts/backup-db.sh
#
# Suggested cron (daily 2 AM) — `crontab -e` on the server:
#
#   0 2 * * * cd ~/kpi-app && ./scripts/backup-db.sh >> ./backups/backup.log 2>&1
#
# Restore (destructive — overwrites the target database):
#
#   docker run --rm -v "$PWD/backups:/backups:ro" postgres:16-alpine \
#     pg_restore --clean --if-exists -d "$DATABASE_URL" /backups/<file>.dump
#   (strip the +psycopg part from DATABASE_URL first, as below)
set -euo pipefail

cd "$(dirname "$0")/.." # ~/kpi-app on the server

DATABASE_URL=$(grep -E '^DATABASE_URL=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
if [ -z "${DATABASE_URL:-}" ]; then
  echo "backup-db: DATABASE_URL not found in .env" >&2
  exit 1
fi
# pg_dump wants a plain postgres:// URL, not SQLAlchemy's postgresql+psycopg://
DUMP_URL="${DATABASE_URL/postgresql+psycopg:/postgresql:}"

BACKUP_DIR="./backups"
mkdir -p "$BACKUP_DIR"
STAMP=$(date +%F_%H%M)
FILE="$BACKUP_DIR/kpi-backup-$STAMP.dump"

docker run --rm \
  -v "$PWD/$BACKUP_DIR:/backups" \
  postgres:16-alpine \
  pg_dump "$DUMP_URL" -Fc -f "/backups/kpi-backup-$STAMP.dump"

# Keep the newest 14 backups (~2 weeks at one per day).
ls -t "$BACKUP_DIR"/kpi-backup-*.dump 2>/dev/null | tail -n +15 | xargs -r rm --

echo "backup-db: wrote $FILE ($(du -h "$FILE" | cut -f1))"
