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

The deploy itself records the version in the footer at build time:

```sh
docker compose build --build-arg VITE_APP_COMMIT="$(git rev-parse --short HEAD)" web \
  && docker compose up -d \
  && printf '%s %s %s\n' "$(date '+%F %T')" "$(git rev-parse --short HEAD)" "$USER" \
       >> ~/kpi-app/DEPLOY_LOG.md
```

## [Unreleased]

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
