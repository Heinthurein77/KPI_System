# Changelog — KPI Approval System

One line per release: date, commit, what shipped. The version shown in the
UI footer is the short commit hash (plus `package.json` version); this file
is the human-readable record of what each release contained.

## Deploy log (on the server, not in this file)

After every production deploy, append one line to `~/kpi-app/DEPLOY_LOG.md`
on the server — date + commit + who deployed — so "what is live?" always has
an answer without asking in chat:

```text
2026-11-12 12:31 a2acb06 Hein Thu Rein  one-off/recurring + fast add-user
```

The deploy itself records the version in the footer at build time. This
is the one canonical deploy command (N5, 2026-11-12) — the same command
is printed in the README ("Production", step 3) and in
`docs/incident-runbook.md` (§1–2). Do not use a variant: a plain
`docker compose up -d --build` bakes `dev` into the footer, and a
separate `docker compose build --build-arg …` step is the same deploy
done in two commands.

```sh
cd ~/kpi-app && git pull && VITE_APP_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build \
  && printf '%s %s %s\n' "$(date '+%F %T')" "$(git rev-parse --short HEAD)" "$USER" \
       >> ~/kpi-app/DEPLOY_LOG.md
```

## Release process — one release train per day (N6, 2026-11-12)

Batch the day's commits, verify once, push once, deploy once.

- **Batch:** the day's fixes land as local commits through the day; they
  ship together in one push, not one push per fix.
- **Verify once:** run the full backend suite (plus the frontend build,
  and the e2e suite where the UI changed) against the batched commits
  before pushing — one green run covers the whole train.
- **Push once, deploy once:** one `git push`, then one deploy with the
  canonical command above and one line in the deploy log. A second push
  or deploy the same day means the train left early — the next batch
  waits for the next day unless it is a production-down hotfix.

## [Unreleased]

> **Status (2026-11-13): local only — the commits in this section
> (`dfec917`, `1a0e732`) are not yet pushed, have had no CI run (N13),
> and are not deployed. They become a release only after the push +
> deploy (see the release process above). Do not read done-tense entries
> here as "live" — that is the N14 fix, and it stays in place until the
> push lands, when this note comes off and the section gets a dated
> release heading.

- Backup job watched: `scripts/backup-db.sh` now emits the same timestamped START / SUCCESS / FAILURE lines as the other cron jobs, and `scripts/check-cron-logs.sh` watches all three jobs (N1). New tests: `app/tests/test_check_cron_logs.py`.
- Platform audit-log reader: new `GET /api/platform/audit-log` endpoint (super_admin-only, platform-scoped rows only) + `/platform/audit-log` page in the platform portal, linked from the sidebar and the Tenants page (N3).
- One canonical cron list: all three jobs (deadline reminders 08:00 daily, audit prune monthly, DB backup daily 02:00) are required everywhere — README, `docs/backup-and-restore.md`, `docs/incident-runbook.md`, `docs/monitoring.md` (N2).
- One canonical deploy command (env-prefix form) in README, CHANGELOG deploy log, and the incident runbook (N5).
- Release process written down: one release train per day — batch the day's commits, verify once, push once, deploy once (N6).
- CI gate: GitHub Actions runs backend pytest + `vite build` + `oxlint` on every push (M1).
- Pre-deploy smoke test: `scripts/smoke-test.sh` boots the stack against a scratch DB and checks `/readyz` + `/login` (M2).
- Release record: this CHANGELOG, commit hash + version in the UI footer, deploy-log format above (M3).
- Monitoring: `scripts/check-cron-logs.sh` (daily cron-log watch) + `docs/monitoring.md` (external uptime check on `/readyz`) (M4).

## 2026-11-12 — `a2acb06`

- One-off / recurring KPI choice in the form + bulk import; welcome email moved to background (add-user 3.66s → 0.67s); future-only rebalance prompt.

## 2026-11-12 — `309cc98`

- Rebalancing budgets against the period's true snapshot total (+ `period-weight` endpoint).

## 2026-11-12 — `445a4fd`

- Add User / Add Custom KPI forms typable (modal focus fix, weight input, labels, password show/hide).

## 2026-11-11 — `3e61a5e` (+ `fd0f6ec`, `3863dcf`, `cd69f59`, `0821d3b`)

- Drafts bucket opens when it has items; login fixes (platform super_admin no longer bounced from `/login`; logged-out visitors go to `/login`); Attendance bulk holiday entry; personal dashboard hidden from tenant admins.

## 2026-11-10 — `835e1d0` (17-commit review fix pass, `4e8b4a2..835e1d0`)

- Whole-project review fixes: atomic approval claim (C2), CSRF origin check, `/readyz` readiness endpoint, migration retry, KPI draft autosave, Attendance audit trail, sole-admin guard, revoked-token prune, cron-job START/SUCCESS/FAILURE logging.
- Attendance rebuilt as a standalone dashboard (no KPI auto-calc, `56a6932`); Daily Task dashboard removed, code + data (`c88aea0`).

## 2026-11-06 — `abcc599`

- Platform admin login hidden from the tenant login page (direct URL only).

## 2026-11-03 — `38e1015` (16-commit review fix pass)

- Previous-month bulk import during grace week, M6 admin window (days 1–7), Playwright login suite, migration-chain test, crontab docs.

## 2026-11-01 — `fe72bb1` (committed 2026-10-28)

- 7-day submission grace after month-end + next month's KPIs start on day 8, for every KPI type. First cycle closed 2026-11-08.

## 2026-10-23 — `2aa2534`

- Mobile + PWA: responsive fixes and installable app (install still waits on HTTPS).

## 2026-10-19 — `ec00dac` (+ `637083a`)

- Annual KPI appraisal PDF report; department admins can reset passwords for own-department employees.

## 2026-10-16

- Bulk approve-all with per-item remarks and editable scores; deadline reminders 3 → 7 days before month-end; attainment % on score tiles; bigger self-score input.

## 2026-10-15

- httpOnly cookie sessions (replacing Bearer tokens in localStorage); audit-log retention job; self-service KPI trend; nightly DB backup script.

## 2026-09-11 — `3af0ed2`

- Early iteration on the KPI approval workflow.

## 2026-07-17 — `022bd12`

- Initial commit: KPI approval system with FastAPI and Neon config.
