# Backup and Restore — User-Side Steps

**Status: USER-SIDE, AWAITING EXECUTION.** Prepared 2026-11-12 (review
finding M5). Nothing in this document has been run — the agent writing it
cannot reach the production server or the Neon console, so every step below
is for the **user to run on the server**. Do not mark this finding closed
until the restore drill in §3 has actually been done and its time recorded.

## The backup story (what exists)

- **Database:** `scripts/backup-db.sh` — `pg_dump` of the Neon database
  through a throwaway `postgres:16-alpine` container, into
  `~/kpi-app/backups/kpi-backup-<date>_<time>.dump`, keeps the newest 14
  (about 2 weeks at one per day). Required cron: daily 02:00. The script
  emits timestamped START / SUCCESS / FAILURE lines into
  `backups/backup.log`, and `scripts/check-cron-logs.sh` watches it
  alongside the other two jobs (N1, 2026-11-12).
- **Neon itself:** Free-tier point-in-time recovery ("Instant restore")
  covers only the **last 6 hours**. Anything older needs the nightly dump.

> Note (2026-11-12, review finding m1): there used to be a fourth piece
> here — a weekly tarball backup of the `kpi_uploads` Docker volume
> (`scripts/backup-uploads.sh`). It was removed with the volume itself:
> nothing in the app writes to `/app/uploads` anymore (bulk import reads
> the file into memory and never writes to disk), so there are no uploaded
> files to back up. The three cron jobs in §1 are the complete set.

## 1. Confirm the server crontab

On the server, in `~/kpi-app`:

```bash
crontab -l
```

All three jobs must be listed. Install any missing one with `crontab -e`
— **do not paste a cron line directly into the shell** (it will try to run
it as a command and fail). All three are required (N2, 2026-11-12) — the
backup job included: it is the highest-stakes job, and without it there
is nothing to restore from.

```cron
# KPI deadline reminders — daily 08:00
0 8 * * * cd ~/kpi-app && docker compose exec -T web python -m app.jobs.send_deadline_reminders >> ~/kpi-app/deadline-reminders.log 2>&1

# Audit-log pruning — monthly, keeps 180 days
0 3 1 * * cd ~/kpi-app && docker compose exec -T web python -m app.jobs.prune_audit_logs >> ~/kpi-app/prune-audit.log 2>&1

# Database backup — daily 02:00 (see scripts/backup-db.sh header)
0 2 * * * cd ~/kpi-app && ./scripts/backup-db.sh >> ./backups/backup.log 2>&1
```

Then verify the backups are actually being written (not just scheduled):

```bash
ls -lt ~/kpi-app/backups | head -5        # newest .dump first — is today's there?
tail -5 ~/kpi-app/backups/backup.log       # last backup run: success or error?
```

If a log shows repeated failures, read the error before re-running — a
failed DB backup usually means `DATABASE_URL` changed or Docker cannot
reach Neon.

## 2. Decide: is 6-hour PITR enough?

Neon Free PITR only reaches back 6 hours. If the nightly dump ever fails
silently for a few days, data older than 6 hours is unrecoverable. The
Neon **Launch plan** (paid) extends PITR to 7 days. That is the user's
call — cost vs. risk — and it is separate from this drill.

## 3. Restore drill (run once, then yearly)

Do this against **scratch targets only** — never restore over the live
database.

### 3a. Database — Neon branch from point-in-time

1. In the Neon console (<https://console.neon.tech>), open the KPI project
   → **Branches** → pick the production branch → **Restore** (point-in-time)
   and choose a time within the last 6 hours. Neon creates a new branch
   holding the database as of that moment.
2. Copy the new branch's connection string.
3. Prove the branch is real data, not an empty database — e.g. connect with
   `psql` and count rows in a table you know is non-empty:
   ```bash
   psql "<new-branch-connection-string>" -c "SELECT count(*) FROM users;"
   ```
   (Any non-zero count on a table you recognise proves the restore.)
4. Delete the scratch branch when done, so it does not consume storage.

### 3b. Database — restore the nightly dump to a scratch database

This proves the `.dump` files themselves work, which the Neon branch does
not test:

```bash
# Strip the +psycopg part from DATABASE_URL first (see scripts/backup-db.sh):
docker run --rm -v "$PWD/backups:/backups:ro" postgres:16-alpine \
  pg_restore --clean --if-exists -d "<scratch-database-url>" /backups/<latest-file>.dump
```

Use a scratch database (a throwaway Neon branch, or a local Postgres) —
**never** the live `DATABASE_URL`: `pg_restore --clean` overwrites the
target database.

## 4. Record the drill

| Date | DB branch restore (min) | Dump restore (min) | Problems found |
|---|---|---|---|---|
| _(fill in after the drill)_ | | | | |

If any step fails, the backup story is broken until it is fixed — say so
plainly and do not call M5 closed. The drill is the finding; the scripts
existing was never the finding.
