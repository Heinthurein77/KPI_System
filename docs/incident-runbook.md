# Incident Runbook — KPI System

**Production:** `http://167.172.80.99` (HTTP-only until the DuckDNS/TLS decision
lands). VPS, `docker compose` + Caddy, Neon Postgres. Server directory:
`~/kpi-app`. One page — if it is longer than this, it is not a runbook.

## 1. Site down (or "the page won't load")

Run on the server, in `~/kpi-app`:

```bash
# 1. Is the app itself up and can it reach the database?
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/readyz
#    200 = app + DB fine — the problem is Caddy or the network, skip to step 3.
#    000 / timeout / 503 = the app or the DB is down, continue.

# 2. What do the containers say?
docker compose ps
docker compose logs --tail 100 web      # container name: kpi-web
docker compose logs --tail 100 caddy    # container name: kpi-caddy
```

- `web` restarting in a loop / `connection refused` to Neon: check the DB
  first — Neon free tier suspends an idle compute; the first request after
  a suspend takes a few seconds (cold wake, known behaviour), but a
  persistent failure means the Neon project is suspended or the
  `DATABASE_URL` in `.env` is wrong. Check the Neon console before
  touching the server.
- `caddy` failing on startup: usually `DOMAIN` missing/wrong in `.env`
  (compose requires it) or ports 80/443 already in use.
- A bad deploy (the site worked before the last pull): roll back — see §2.

```bash
# 3. Restart the stack (safe: restart: always, no data loss)
docker compose restart web
# If that does not help, rebuild from the current checkout — use the one
# canonical deploy command (N5, 2026-11-12; same as README + CHANGELOG):
# a plain `docker compose up -d --build` would bake `dev` into the footer.
VITE_APP_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build
```

## 2. Bad deploy — roll back to the previous commit

```bash
cd ~/kpi-app
git log --oneline -5            # find the last known-good commit
git checkout <previous-commit>  # e.g. the commit before the bad one
# Canonical deploy command (N5, 2026-11-12; same as README + CHANGELOG):
# the prefix passes the rolled-back commit through, so the footer shows
# the commit that is actually live instead of `dev`.
VITE_APP_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build   # migrations run automatically on startup
curl -s http://localhost:8000/readyz   # expect 200 (or {"status":"ok"})
```

Then tell the agent which commit is live now, so the bad commit is not
re-deployed by the next pull. Do **not** run `git pull` again until the bad
commit is fixed or reverted in the repo.

## 3. Suspected session hijack / account compromise

1. Reset the affected user's password from the Tenant Admin → Users page
   (or the platform admin for a tenant admin). A password change bumps the
   account's token version, which kills every existing session token
   immediately — the attacker is logged out with the password change.
2. Check the Audit Log (Tenant Admin → Audit Log) for changes made by that
   account around the suspected time.
3. If a **tenant admin** account is suspected compromised, change the
   platform admin password too (in-app: Account → Change Password) and
   tell the agent — there is currently no second platform admin.

## 4. A cron job failed (no reminders, audit log growing, no backups)

```bash
crontab -l                                  # all three jobs must be listed (see docs/backup-and-restore.md)
tail -20 ~/kpi-app/deadline-reminders.log
tail -20 ~/kpi-app/prune-audit.log
tail -20 ~/kpi-app/backups/backup.log       # DB backup job (watched by scripts/check-cron-logs.sh)
ls -lt ~/kpi-app/backups | head             # DB dumps, newest first
```

Cron failures are only visible in these log files — nothing pages anyone.
If a log shows repeated failures, do not just re-run the job: read the error
first (a failed DB backup usually means `DATABASE_URL` changed or Docker
cannot reach Neon).

## Who to call

There is **one person** who holds server SSH access, the GitHub PAT, and the
platform super-admin account: the user (Hein Thu Rein). There is no second
platform admin and no break-glass note yet (review finding M6 — still open).
If he is unavailable, nobody can deploy, rotate a secret, or recover the
platform account. That is a known, accepted gap until a second admin and a
break-glass note exist — this runbook cannot fix it, only state it.
