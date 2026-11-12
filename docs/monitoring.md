# Monitoring — KPI Approval System

Two layers, both cheap. The uptime check pages someone when the site goes
down; the cron-log watch catches silent job failures (a missed reminder
run is invisible until a user complains).

## 1. External uptime check on `/readyz`

`/readyz` returns `{"status": "ok"}` (HTTP 200) only when the app can run
`SELECT 1` against the database; a dead or suspended database returns HTTP
503 `{"status": "unavailable"}`. Check that URL — not `/` or `/healthz`
(`/healthz` never touches the DB and reports healthy while every API call
500s). The Dockerfile HEALTHCHECK and Caddy's `service_healthy` gate use
the same endpoint, so the external check mirrors what the stack itself
trusts.

- **URL:** `http://167.172.80.99/readyz` — switch to
  `https://<domain>/readyz` once TLS lands (C1, DuckDNS decision).
- **What counts as down:** any status other than 200, or a body that is
  not `{"status": "ok"}`.
- **Alert channel:** email at minimum; add Telegram/Slack if the checker
  supports it on the free tier.

The user picks the checker — no account has been created. Three free
options (free-tier details as published in Oct–Nov 2026; re-check the
pricing page before signing up, they change):

### Option A — UptimeRobot (free)

- Free tier: 50 monitors, checks every 5 minutes, email + push alerts,
  1 basic status page, no credit card required.
- Setup: add an **HTTP(s)** monitor, URL `http://167.172.80.99/readyz`,
  interval 5 minutes (the free-plan interval), alert contact = your email.
  Optionally add a **Keyword** monitor on the same URL that requires the
  response to contain `"status": "ok"`.
- Limitation: 5 minutes is the detection delay — a brief outage under
  5 minutes may not be noticed. Good enough for this system.

### Option B — Better Stack (free tier)

- Free tier: 10 monitors, checks every 3 minutes, email/Slack/webhook
  alerts, public status page included.
- Setup: add a monitor for `http://167.172.80.99/readyz`, interval 3
  minutes, expected status 200, alert via email (or a Slack webhook if
  the team uses one). Better Stack also keeps an incident timeline, which
  is useful when the user asks "when did it go down?".
- Limitation: 10 monitors on the free tier — one is used here, so there
  is room for a second check (e.g. the login page) if wanted.

### Option C — Uptime Kuma (self-hosted, free)

- Open-source uptime monitor you run yourself; no account, no monitor
  cap, checks as fast as every 20 seconds. Runs as one more container on
  the VPS (or any always-on box) — it must NOT live on the same host it
  watches if the goal is to notice the host going down, so prefer a
  second box or accept that a dead VPS also silences the watcher.
- Setup: `docker run -d -p 3001:3001 louislam/uptime-kuma`, add an HTTP
  monitor for `http://167.172.80.99/readyz` expecting status 200 and
  keyword `"status": "ok"`, notification = email/Telegram.
- Limitation: you maintain it; the hosted options above are less work.

## 2. Cron-log watch — `scripts/check-cron-logs.sh`

The two required cron jobs log to `~/kpi-app/deadline-reminders.log` and
`~/kpi-app/prune-audit.log` on the server, with distinct timestamped
START / SUCCESS / FAILURE lines (M20 fix, 2026-11-10). The script prints
one summary line per job and exits non-zero if any FAILURE line is found,
or if a log is missing/empty (job never ran / cron never installed):

```text
$ ./scripts/check-cron-logs.sh
deadline-reminders: OK — 14 run(s), 14 succeeded, 0 failed (last run 2026-11-12 08:00:01)
prune-audit: OK — 1 run(s), 1 succeeded, 0 failed (last run 2026-11-01 03:00:02)
```

Suggested daily cron on the server (after the 08:00 reminders run):

```cron
30 8 * * * cd ~/kpi-app && ./scripts/check-cron-logs.sh || echo "KPI cron failure — see ~/kpi-app/*.log" | mail -s "KPI cron check failed" you@example.com
```

Replace the `mail` line with the alert channel actually in use. If a job
shows FAILURE, the failing run's line is printed by the script; the full
traceback is in the job's log file. A FAILURE on `deadline-reminders`
means reminders did not go out that day — re-run the job by hand after
fixing the cause (`docker compose exec -T web python -m
app.jobs.send_deadline_reminders`); it is idempotent, so a re-run does not
double-send.

## 3. What is NOT monitored (yet)

- Neon free-tier usage (1 GB storage, 100 CU-hours/month, 6 h PITR) —
  no usage check exists; see finding m7. Check the Neon console monthly.
- The backup script (`scripts/backup-db.sh`) has no FAILURE marker in its
  log yet — a failed backup only shows as a missing new file in the
  backup directory. Worth adding to the log-watch when the backup cron is
  confirmed running (M5). (There is no uploads backup script anymore:
  the `kpi_uploads` volume was removed with finding m1 — nothing writes
  to `/app/uploads` — so there is nothing to back up there.)
- Application errors beyond the cron jobs: container logs are `json-file`
  rotation (10 MB × 3) via `docker compose logs web`. No Sentry or
  Prometheus is wired up.
