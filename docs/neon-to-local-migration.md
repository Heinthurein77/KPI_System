# Neon → Local Postgres Migration (2026-11-25)

**Why:** Neon Free hit its limits (1 GB storage / 100 CU-h compute per month).
The database moves into a Postgres container on the same VPS (`db` service in
`docker-compose.yml`). The app code needs no change — only `DATABASE_URL` in
the server `.env` switches from the Neon URL to the local one.

**What changes:** `docker-compose.yml` gains a `db` service (no published
port, 512 MB cap, data in the `kpi_prod_pg_data` volume); `.env.example`
documents the new variables. **What does NOT change:** the app, the
migrations (the dump carries the `alembic_version` table, so startup
migrations no-op), Caddy, or the Neon project — Neon is left untouched as
the instant rollback.

**Downtime:** one short window (dump + restore; the DB is only ~10 MB, so a
few minutes at most). Users see the login page fail to load during it. Do it
off-hours.

**Order matters:** the practice dump (Step 3) reads the Neon URL from `.env`,
so `DATABASE_URL` is switched to local only at cutover (Step 4). Do not
switch it early or the dump will read the empty local DB.

## Step 0 — Pre-checks (on the server, `cd ~/kpi-app`)

```bash
free -m            # need ~600 MB free: web 1g + caddy 256m + db 512m on a 2 GB box
df -h /            # need 2x the DB size free (dump file + restored data)
```

Neon version + DB size (dump client runs from a container, no local psql needed):

```bash
docker run --rm --env-file .env postgres:18-alpine sh -c \
  'psql "$DATABASE_URL" -c "SELECT version();" -c "SELECT pg_size_pretty(pg_database_size(current_database()));"'
```

Send the outputs to the agent. They decide `PG_VERSION` (18 for this Neon — must match
Neon's version) and confirm the box can take Postgres. (2026-10-16 the
self-hosted option was rejected for this 2 GB box; it is back on the table now
only because Neon Free is exhausted — if `free -m` shows under ~500 MB free,
stop and reconsider the paid Neon Launch plan instead.)

**Step 0 results (2026-11-25):** available 1011 MB, disk 31 GB free, Neon is
PostgreSQL 18.7, DB is 9,664 kB. Green light — `PG_VERSION=18`.

## Step 1 — Add the new variables (on the server, `DATABASE_URL` untouched)

```bash
cd ~/kpi-app
cp .env .env.neon-backup
DB_PASS=$(openssl rand -hex 24)
printf '%s\n' "PG_VERSION=18" "POSTGRES_USER=kpi" "POSTGRES_PASSWORD=$DB_PASS" "POSTGRES_DB=kpi_system" >> .env
```

Keep `.env` at `chmod 600`. Do **not** send the password anywhere — it lives
only in the server `.env`.

## Step 2 — Pull the new compose file and start only the DB

```bash
cd ~/kpi-app && git pull
docker compose up -d db
docker compose exec db pg_isready -U kpi   # must answer "accepting connections"
```

## Step 3 — Practice restore (no downtime, Neon still live)

`DATABASE_URL` in `.env` still points at Neon here — that is intentional.
Dump from Neon (client version 18 dumps this server version):

```bash
docker run --rm --env-file .env -v ~/kpi-app:/backup postgres:18-alpine sh -c \
  'pg_dump "$DATABASE_URL" -Fc --no-owner --no-privileges -f /backup/kpi-neon-2026-11-25.dump'
ls -lh ~/kpi-app/kpi-neon-2026-11-25.dump   # sanity: non-empty, roughly the DB size
```

Restore into the local container:

```bash
docker cp ~/kpi-app/kpi-neon-2026-11-25.dump kpi-db:/tmp/restore.dump
docker compose exec -T db pg_restore -U kpi -d kpi_system --no-owner --no-privileges /tmp/restore.dump
```

`pg_restore` may print warnings — what matters is the verification:

```bash
docker compose exec db psql -U kpi -d kpi_system -c "SELECT version FROM alembic_version;"
# must be: 0025_attendance_status_check
docker compose exec db psql -U kpi -d kpi_system -c \
  "SELECT (SELECT count(*) FROM users) AS users, (SELECT count(*) FROM kpi_submissions) AS submissions, (SELECT count(*) FROM attendance_records) AS attendance;"
```

Compare those three counts against Neon (same query via the Step 0 container).
If they match, the procedure works — proceed to cutover. If not, stop and report.

## Step 4 — Cutover (short downtime)

```bash
docker compose stop web
# Final dump — catches any writes made after the Step 3 dump. DATABASE_URL is
# still the Neon one here, so this reads Neon:
docker run --rm --env-file .env -v ~/kpi-app:/backup postgres:18-alpine sh -c \
  'pg_dump "$DATABASE_URL" -Fc --no-owner --no-privileges -f /backup/kpi-neon-final.dump'
# Fresh DB, then restore the final dump:
docker compose exec db psql -U kpi -d postgres -c "DROP DATABASE kpi_system;" -c "CREATE DATABASE kpi_system OWNER kpi;"
docker cp ~/kpi-app/kpi-neon-final.dump kpi-db:/tmp/final.dump
docker compose exec -T db pg_restore -U kpi -d kpi_system --no-owner --no-privileges /tmp/final.dump
# Re-verify counts + alembic_version as in Step 3, then switch the URL:
sed -i '/neon\.tech/s/^/# /' .env
DB_PASS=$(grep '^POSTGRES_PASSWORD=' .env | cut -d= -f2)
printf '%s\n' "DATABASE_URL=postgresql://kpi:$DB_PASS@db:5432/kpi_system" >> .env
grep -n "DATABASE_URL" .env   # exactly one uncommented line, pointing at @db:5432
# Start the app:
VITE_APP_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build
curl -s http://127.0.0.1:8000/readyz; curl -s http://127.0.0.1:8000/healthz
```

Then log in through the browser: login page renders, dashboard data is there,
footer shows the commit. If anything is wrong, roll back (Step 5) — do not
debug live on the new DB.

## Step 5 — Rollback (if the cutover fails)

Neon was never written to, so the old data is intact. In `.env`, delete the
local `DATABASE_URL` line and uncomment the Neon line (or `cp .env.neon-backup
.env` and re-add the `PG_VERSION`/`POSTGRES_*` lines), then:

```bash
docker compose up -d web
```

The app is back on Neon. The local `db` container can stay stopped until the
next attempt (`docker compose stop db`).

## After the cutover

- **Backups (replaces Neon PITR — S-14):** the 6-hour Neon PITR window is
  gone. Add a nightly `pg_dump -Fc` cron on the server plus an off-server
  copy, and run a restore drill (see `docs/backup-and-restore.md` — update
  it for the local DB).
- **Old Neon project:** leave it running for a week as a safety net, then
  delete it (or keep it — Free tier, no cost — as an archive).
- **Monitoring:** the `readyz` healthcheck now also proves the local DB
  answers; add a disk-space alert — a full disk stops Postgres writes the
  same way the Neon limit did.
- **Cleanup:** remove `kpi-neon-*.dump` from the server once verified, or
  move them off-server — they contain the full production data.
