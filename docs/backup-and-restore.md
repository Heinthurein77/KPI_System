# Backup and Restore — User-Side Steps

**Status: UPDATED 2026-11-25 for the local database.** The Neon → server
migration (2026-11-25) changed the backup story: the database is now the
local `db` container, Neon PITR is gone, and `scripts/backup-db.sh` dumps
from the container, encrypts the dump, and restore-verifies it every night.
**Backup cron: VERIFIED INSTALLED 2026-11-25** — the first manual run
succeeded that day at 12:02 (`kpi-backup-2026-11-25_1202.dump.enc`,
restore-verified; see §4). **Rollover cron: VERIFIED INSTALLED
2026-11-20 ~15:13.** What is still open (§1): the user confirming on
the server with `crontab -l` that all four jobs are listed (reminders
08:00, rollover 07:00, prune monthly, backup 02:00) plus the daily
08:30 log watch — the agent cannot verify the server crontab itself,
so M-16 stays PARTIAL. Still user-side: the off-server copy and the
full disaster drill (§3b). Do not mark the backup finding (S-14) closed
until the off-server copy exists and the drill has been run and timed.

## The backup story (what exists)

- **Database:** `scripts/backup-db.sh` — `pg_dump -Fc` from the running
  `kpi-db` container into `~/kpi-app/backups/kpi-backup-<date>_<time>.dump.enc`
  (AES-256-CBC encrypted with `BACKUP_PASSPHRASE` from `.env`), keeps the
  newest 14 (about 2 weeks at one per day). Every run restore-verifies the
  fresh backup into a scratch database and compares migration version +
  row counts with live — the restore drill, automated nightly. Required
  cron: daily 02:00. The script emits timestamped START / SUCCESS /
  FAILURE lines into `backups/backup.log`, and
  `scripts/check-cron-logs.sh` watches it alongside the other jobs
  (N1, 2026-11-12).
- **Off-server copy: NOT YET DONE.** All 14 backups live on the same VPS
  as the database. If the VPS itself dies, the backups die with it. Until
  an off-server copy exists, copy the newest `.dump.enc` off the server
  weekly (phone/SSH download, or Drive upload) and keep `BACKUP_PASSPHRASE`
  somewhere safe off the server — without it the encrypted backups are
  unreadable. The Neon project is kept for one week after the migration
  (until ~2026-12-02) as a safety net, then deleted.
- **Neon PITR: GONE.** The 6-hour point-in-time window ended with the
  migration. Recovery is now: the newest verified nightly dump (≤ 24 h
  old), kept 14 days back.

> Note (2026-11-12, review finding m1): there used to be a fourth piece
> here — a weekly tarball backup of the `kpi_uploads` Docker volume
> (`scripts/backup-uploads.sh`). It was removed with the volume itself:
> nothing in the app writes to `/app/uploads` anymore (bulk import reads
> the file into memory and never writes to disk), so there are no uploaded
> files to back up.
>
> Note (2026-11-20, senior review S-M1): a FOURTH cron job was added —
> the daily recurring-KPI rollover (below). The day-8 rollover used to
> rely only on lazy per-employee materialisation when each employee
> opened their dashboard; the cron is now the real automation and the
> lazy path is only a fallback. The three jobs in §1 are therefore now
> four; `scripts/check-cron-logs.sh` watches all four since the M-21
> fix on 2026-11-25 (the rollover log was the blind spot until then).

## 1. Confirm the server crontab

On the server, in `~/kpi-app`:

```bash
crontab -l
```

All four jobs must be listed — **backup (02:00) and rollover (07:00)
were verified installed on 2026-11-25 and 2026-11-20 respectively,
but only `crontab -l` on the server proves all four are still there**
(the agent cannot check the server crontab). Install any missing one
with `crontab -e`
— **do not paste a cron line directly into the shell** (it will try to run
it as a command and fail). All four are required (N2, 2026-11-12) — the
backup job included: it is the highest-stakes job, and without it there
is nothing to restore from.

```cron
# KPI deadline reminders — daily 08:00
0 8 * * * cd ~/kpi-app && docker compose exec -T web python -m app.jobs.send_deadline_reminders >> ~/kpi-app/deadline-reminders.log 2>&1

# Recurring KPI rollover — daily 07:00 (S-M1, 2026-11-20). Materialises
# the current month's recurring KPI set for all tenants once the day-8
# rollover opens; before day 8 it does nothing, and re-running it on
# the same day creates nothing twice (idempotent). Runs before the
# 08:00 reminders so the new month's drafts exist when employees are
# nudged about them.
0 7 * * * cd ~/kpi-app && docker compose exec -T web python -m app.jobs.run_recurring_rollover >> ~/kpi-app/recurring-rollover.log 2>&1

# Audit-log pruning — monthly, keeps 180 days (audit logs) and 90 days
# (notifications, except deadline_reminder rows — see docs/neon-usage-check.md)
0 3 1 * * cd ~/kpi-app && docker compose exec -T web python -m app.jobs.prune_audit_logs >> ~/kpi-app/prune-audit.log 2>&1

# Database backup — daily 02:00 (see scripts/backup-db.sh header). The
# script redirects itself into backups/backup.log, so no redirect is needed.
# Setup first (once): echo "BACKUP_PASSPHRASE=$(openssl rand -hex 24)" >> .env
0 2 * * * cd ~/kpi-app && ./scripts/backup-db.sh

# Cron-log watch — daily 08:30 (see scripts/check-cron-logs.sh). One
# summary line per job; exit 1 (and the mail below) if any job failed,
# went stale, or never ran. All four jobs above are watched, including
# the rollover log.
30 8 * * * cd ~/kpi-app && ./scripts/check-cron-logs.sh || echo "KPI cron failure — check the logs" | mail -s "KPI cron check failed" you@example.com
```

Then verify the backups are actually being written (not just scheduled):

```bash
ls -lt ~/kpi-app/backups | head -5        # newest .dump.enc first — is today's there?
tail -5 ~/kpi-app/backups/backup.log       # last backup run: success or error?
```

If a log shows repeated failures, read the error before re-running — a
failed DB backup now usually means the `db` container is down or
`BACKUP_PASSPHRASE` is missing from `.env`. Run the script once by hand
(`./scripts/backup-db.sh`, then `tail -5 backups/backup.log`) to see the
full error.

## 2. Recovery window (what the nightly dump covers)

The nightly encrypted dump is the only recovery path — Neon PITR ended
with the migration. The newest verified dump is at most 24 h old and 14
days are kept, so the worst case is losing up to one day of writes. If a
dump ever fails silently for a few days, the gap grows — that is what
`scripts/check-cron-logs.sh` (daily 08:30 watch) is for: it fails loudly
if the backup job has not succeeded in the last 36 h. The remaining gap
is the missing off-server copy (see the backup story above) — that is
still the user's call.

## 3. Restore drill (automatic nightly + manual check)

The nightly backup run **is** the drill: every run decrypts the fresh
backup, restores it into a scratch database (`kpi_restore_test`), and
compares the migration version + row counts with live. A run that logs
`job succeeded` has proven that night's backup restores. The manual
steps below are only for proving a restore by hand (e.g. after a real
incident, or to practice).

Do this against **scratch targets only** — never restore over the live
database.

### 3a. Restore a backup file by hand

```bash
# From ~/kpi-app, with BACKUP_PASSPHRASE exported in your shell:
export BACKUP_PASSPHRASE='<passphrase from .env>'
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE \
  -in backups/<file>.dump.enc | \
  docker compose exec -T db pg_restore -U kpi -d kpi_restore_test \
    --no-owner --no-privileges
# Then prove it is real data:
docker compose exec db psql -U kpi -d kpi_restore_test -c "SELECT count(*) FROM users;"
# (Any non-zero count on a table you recognise proves the restore.)
# Clean up when done:
docker compose exec db psql -U kpi -d postgres -c "DROP DATABASE kpi_restore_test;"
unset BACKUP_PASSPHRASE
```

(Before the migration this section described a Neon-branch restore —
that path ended with Neon. The scratch database on the local container
replaces it.)

### 3b. Full disaster drill (once, then yearly)

Simulate losing the live database: stop the app, drop `kpi_system`,
re-create it empty, and restore the newest verified `.dump.enc` into
it with the §3a commands (target `kpi_system` instead of the scratch
database), then start the app and log in. Record the time it took in §4.

## 4. Record the drill

| Date | Cron installed | Manual run OK | Nightly verify passing | Full disaster drill (min) | Problems found |
|---|---|---|---|---|---|
| 2026-11-25 | backup 02:00 yes (first manual run 12:02, `kpi-backup-2026-11-25_1202.dump.enc`, restore-verified); rollover 07:00 yes (installed 2026-11-20 ~15:13) — confirm all four with `crontab -l` | yes | not yet observed (first nightly run due 02:00 2026-11-26) | not done | Off-server copy still missing; full drill still to do |

If any step fails, the backup story is broken until it is fixed — say so
plainly and do not call S-14 closed. The drill is the finding; the scripts
existing was never the finding.
