#!/bin/sh
# Back up the kpi_uploads Docker volume (diary evidence files) to a
# timestamped tar.gz. Evidence files live ONLY in this volume — not in
# the Neon database — so without this backup a VPS/volume loss deletes
# every attachment while the diary rows themselves survive.
#
# Usage (on the server, or anywhere with docker access):
#   ./scripts/backup-uploads.sh
#   BACKUP_DIR=/mnt/backups/kpi ./scripts/backup-uploads.sh
#   VOLUME=kpi_uploads RETENTION_DAYS=30 ./scripts/backup-uploads.sh
#
# Restore:
#   cd ~/kpi-app && docker compose down
#   docker run --rm -v kpi_uploads:/data -v ~/kpi-backups:/backup alpine \
#     sh -c "tar xzf /backup/kpi_uploads-<timestamp>.tar.gz -C /data"
#   docker compose up -d
#   # If restored files are root-owned and uploads 500, fix ownership once:
#   docker compose exec -T web chown -R appuser:appuser /app/uploads
#
# Suggested cron (weekly, Sunday 02:00, on the server):
#   0 2 * * 0 cd ~/kpi-app && ./scripts/backup-uploads.sh >> ~/kpi-app/uploads-backup.log 2>&1
set -eu

VOLUME="${VOLUME:-kpi_uploads}"
BACKUP_DIR="${BACKUP_DIR:-$HOME/kpi-backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/kpi_uploads-$STAMP.tar.gz"

docker run --rm -v "$VOLUME:/data:ro" -v "$BACKUP_DIR:/backup" alpine \
    tar czf "/backup/kpi_uploads-$STAMP.tar.gz" -C /data .

# Prune backups older than the retention window (keeps the newest even
# if everything is old — -mtime only matches files, never the dir).
find "$BACKUP_DIR" -maxdepth 1 -name 'kpi_uploads-*.tar.gz' -type f \
    -mtime "+$RETENTION_DAYS" -delete

echo "Backed up volume '$VOLUME' to $OUT"
ls -la "$OUT"
