# Neon Usage Check — Monthly

**Status:** documented 2026-11-12 (review finding m7). User-side check, once
a month (same day as the restore drill is a good habit — see
`docs/backup-and-restore.md`).

## Why

The production database is Neon Free tier. Its limits are hard: when they
are hit, the database stops working — there is no warning email from the
app and nothing in the repo watches usage. The main storage drivers are
audit-log growth (pruned to 180 days by the monthly cron, so it is bounded)
and notification growth (pruned to 90 days by the same monthly cron since
2026-11-20, senior review S-m9 — except `deadline_reminder` rows, which
the reminder idempotency check depends on and which are therefore kept;
they accumulate at roughly one row per user per period, so they are the
residual growth to watch if the table ever looks large).

## The limits (Neon Free tier)

| Resource | Limit | What happens at the limit |
|---|---|---|
| Storage | **1 GB** per project | Writes fail; the app 500s on anything that inserts. |
| Compute | **100 CU-hours / month** | Compute is suspended until the next month (or an upgrade). |
| Point-in-time recovery (PITR) | **6-hour** history window | Anything older than 6 hours is unrecoverable from Neon alone — the nightly dump is the only copy. |

## The check (5 minutes, in the Neon console)

1. Sign in at <https://console.neon.tech> and open the KPI project.
2. Open the project's **Usage** page (left sidebar) — it shows storage used
   and compute hours used for the current billing period.
3. Compare against the limits above and record the numbers:

| Date | Storage used / 1 GB | Compute used / 100 CU-h | PITR window | Notes |
|---|---|---|---|---|
| _e.g. 2026-11-12_ | _—_ | _—_ | 6 h | _first check — fill in_ |

4. Also confirm the project still shows an active branch and that the
   compute is not suspended (a suspended compute shows as such on the
   project dashboard; the first request after a suspend is slow — cold
   wake — which is normal, not an error).

## Thresholds — when to act

- **Storage > 700 MB or compute > 70 CU-h mid-month:** tell the agent.
  Likely causes are audit-log growth (check the prune cron ran — see
  `docs/backup-and-restore.md`) or notification growth (same cron now
  prunes notifications older than 90 days, except `deadline_reminder`
  rows) or a runaway import. Do not wait for the limit.
- **Storage > 900 MB or compute nearly exhausted:** decide on the Neon
  **Launch plan** (paid — larger storage, more compute, **7-day** PITR
  instead of 6 hours). That decision is the user's; the agent can do the
  plan change only after the user approves the cost.

## Why there is no script for this

The Neon API requires an API key (`Authorization: Bearer <key>` on
`https://api.neon.tech/api/v2/...`) — verified 2026-11-12: without a key it
answers 401. No key is stored in this repo or on the server, and no script
here can query usage without live credentials. This check is therefore
docs-only by design: a human opens the console once a month. If the user
later stores a Neon API key, a scripted check becomes possible — until
then, do not invent one.
