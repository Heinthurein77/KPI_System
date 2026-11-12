# KPI Approval System

An Automated KPI Calculation and Approval System: a FastAPI JSON API backend with a
separately-deployed React (Vite) single-page frontend.

## Stack

- **Backend:** FastAPI (Python 3.11+), APIRouter-based modular layout, JSON API only
- **Frontend:** React (Vite, JavaScript) + Tailwind CSS, in `frontend/` — deployed independently
- **Database:** SQLAlchemy ORM — SQLite locally, PostgreSQL (Neon.tech) in production, selected automatically via `DATABASE_URL`
- **Auth:** httpOnly cookie session (signed JWT in an `access_token` cookie, `Secure` flag on HTTPS), bcrypt password hashing, role-based access control, logout revocation via token version. The JWT is readable by JS nowhere — only the `tenant_slug` (which org's login form to show) lives in `localStorage`.
- **Deployment:** Docker Compose + Caddy on a VPS (single container serving API + frontend, external Neon Postgres); see "Production (VPS + Docker Compose + Caddy)" below

## Project Layout

```
app/
  core/          # settings, security (hashing/JWT), auth & RBAC dependencies
  models/        # SQLAlchemy models: User, Department, KPITemplate, KPISubmission
  routers/       # auth, dashboard, kpi (workflow actions), admin (CRUD), attendance, importer (bulk import), platform (multi-tenant admin) — all under /api
  services/      # kpi_service.py — all workflow/business logic
  schemas/       # Pydantic request/response schemas
  database.py    # engine/session setup, SQLite <-> Postgres switch
  main.py        # app factory, CORS, router wiring, startup table creation
  seed.py        # bootstrap Super Admin seeder
frontend/
  src/
    api/         # axios client + per-resource API calls
    context/     # AuthContext (token + user state)
    components/  # layout (Sidebar/Topbar/AppShell), shared UI, dashboard boards
    pages/       # LoginPage, DashboardPage, MyKpiPage, admin/*
    routes/      # ProtectedRoute, AppRoutes
```

## Roles & Workflow

1. **Employee** self-assesses KPI metrics for the current month (draft, editable) → submits → status `pending_dept_approval` (now read-only for the employee).
2. **Department Admin** reviews submissions scoped to their own department only, can edit the score, then **Approve & Forward** → status `pending_final_approval`. Can also **Reject**. A Dept Admin's own custom KPI skips this stage and goes straight to the Tenant Admin.
3. **Tenant Admin** gives final approval (optionally adjusting the score) → status `approved`. Tenant Admin can also **override** the score and force-approve at any stage, and has visibility across all departments. The platform Super Admin never approves KPIs — platform administration (tenants only) is a separate layer, reachable only by direct URL at `/platform/login` (unlinked from the user login page).

## Local Setup

### Backend (SQLite, no Docker)

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt

python -m app.seed            # creates tables + the one bootstrap Super Admin account
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev                   # http://localhost:5173, talks to http://localhost:8000 by default (see .env)
```

### Bootstrap account

`python -m app.seed` creates **only** a Super Admin — no sample departments, employees,
or KPI metrics. Everything else is created from the admin UI after logging in.

| Role | Email | Password |
|---|---|---|
| Super Admin | `admin@kpi.com` | `Password123!` |

Override the bootstrap identity via env vars before running the seed (recommended for
any real deployment): `SUPER_ADMIN_NAME`, `SUPER_ADMIN_EMAIL`, `SUPER_ADMIN_PASSWORD`.
Change the password after first login regardless.

## Local Setup with Docker (Postgres)

```bash
docker compose -f docker-compose.local.yml up --build
docker compose -f docker-compose.local.yml exec web python -m app.seed
```

App (API + UI) available at http://localhost:8000.

### Production stack with Docker (web only, external Neon database)

`docker-compose.yml` is the production stack: the web app behind Caddy (automatic HTTPS,
see `Caddyfile`), no Postgres service. Copy `.env.example` to `.env` (gitignored — never
commit real credentials), fill in `SECRET_KEY`, `DATABASE_URL` and `DOMAIN`, point the
domain's DNS A record at the server, then:

```bash
# Canonical deploy command (N5, 2026-11-12): the VITE_APP_COMMIT=… prefix
# is what makes the UI footer show the real commit instead of `dev`
# (see "Production" step 3 and CHANGELOG.md "Release process").
VITE_APP_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build
docker compose exec web python -m app.seed
```

Only Caddy is exposed (ports 80/443); the app container is reachable only through it.
Because the stack forces `ENVIRONMENT=production`, it refuses to start without a real
`SECRET_KEY` and a `DATABASE_URL`.

Note: since `.env` is auto-loaded by `pydantic-settings`, once it contains a real
`DATABASE_URL`, **any** local command (including a bare `uvicorn app.main:app --reload`
outside Docker) will also connect to that database, not local SQLite. Remove or comment
out `DATABASE_URL` in `.env` to go back to local SQLite for casual dev.

## Production (VPS + Docker Compose + Caddy)

Production is a **single Docker Compose stack on a VPS** (`~/kpi-app` on the
server, `http://167.172.80.99` until a domain + TLS lands — see C1 in the
project reviews): one `web` container serving the API and the built frontend,
behind Caddy (automatic HTTPS once `DOMAIN` is set), with the database
external on Neon.tech. There is deliberately no Postgres service in
`docker-compose.yml`.

1. Create a Postgres database on [Neon.tech](https://neon.tech) and copy its connection string.
2. On the server, copy `.env.example` to `.env` (gitignored — never commit real credentials) and fill in `SECRET_KEY`, `DATABASE_URL` and `DOMAIN` (the site hostname, no `https://`); point the domain's DNS A record at the server.
3. Deploy (pull + rebuild; Alembic migrations run automatically on startup):
   ```bash
   cd ~/kpi-app && git pull && VITE_APP_COMMIT=$(git rev-parse --short HEAD) docker compose up -d --build
   curl -s http://localhost:8000/readyz   # expect 200 — SELECT 1 against the DB
   ```
   The `VITE_APP_COMMIT=…` prefix is what makes the UI footer show the real
   commit instead of `dev` — without it the Docker build has no `.git` to
   read the commit from (see `Dockerfile`, M3). This is the one canonical
   deploy command (N5, 2026-11-12): the same command is printed in
   `CHANGELOG.md` and `docs/incident-runbook.md` — do not invent a variant.
   Release cadence: one release train per day — batch the day's commits,
   verify once, push once, deploy once (see `CHANGELOG.md`, "Release
   process", N6).
4. Run the seed script once to bootstrap the Super Admin account (required — there is no other way to create the first login):
   ```bash
   docker compose exec web python -m app.seed
   ```
   Set `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_PASSWORD` / `SUPER_ADMIN_NAME` as env vars first if you don't want the default demo credentials (`admin@kpi.com` / `Password123!` — change the password after first login regardless).

Only Caddy is exposed (ports 80/443); the app container is reachable only through it. Because the stack forces `ENVIRONMENT=production`, it refuses to start without a real `SECRET_KEY` and a `DATABASE_URL`.

> Note: `railway.json` files still exist at the repo root and in `frontend/`, but Railway is **not** the production path anymore — they are legacy and unused by the current deploy. Do not follow them to set up a deployment.

## Attendance

A standalone module inside the KPI system (same login, no KPI linkage, no KPI writes — a guard test asserts no KPI imports). Office-WiFi-only check-in/out via a per-tenant public-IP whitelist; office hours 09:00–17:00, working days Mon–Fri (per-tenant settings, midday = midpoint of office hours, 13:00 by default). Late is recorded but never displayed and never deducts — only present/absent counts. Half-day = 0.5 day (check-in at/after midday, or check-out before midday). Approved leave counts as absent. Tenant admins see the monthly report + settings only — no personal Attendance dashboard. Holidays can be entered in bulk, including date ranges. See `docs/polling.md` for the Attendance `/today` refresh cadence.

## Grace period & rollover (7-day grace + day-8 rollover)

`SUBMISSION_GRACE_DAYS = 7` (`app/core/config.py`). A past month stays fillable until 7 days after month-end (drafts only — a rejected submission may still be corrected and resubmitted after grace, since the delay was the reviewer's). The next month's whole KPI set only starts on day 8 (`recurring_rollover_open`, `app/services/submission_grace.py`); days 1–7 belong to closing the previous month. During days 1–7, tenant admins and dept admins (own department only) may add the previous month's KPI; the current month before day 8 and past-grace months are blocked. The dashboard returns `rollover_on` and shows a grace banner; "no metrics" before day 8 is normal, not an error. The board shows one month at a time — use the Month selector + Apply to see another month. A KPI created for a future month appears in that month right away; the recurring set for that month only materialises from day 8.

## One-off vs recurring KPIs

Every custom KPI is either **Repeat every month** (recurring, the default) or **One month only** (one-off, locked to one year/period). The Add Custom KPI form has a segmented control for the choice, and bulk import takes an optional 7th `recurring` column (`yes`/`true`/`recurring` = recurring; empty = recurring; a bad value is a per-row error). One-offs are excluded from the rebalancer and labelled with an amber "One-off" pill in the list. Recurring custom templates can be rebalanced (future months only — approved submissions are untouched).

## Period-weight endpoint

`GET /api/admin/employees/{employee_id}/period-weight?year=YYYY&period=Month` — the true weight budget for adding a KPI to one employee in one period (dept admin or tenant admin; dept admins only their own department's employees). `snapshot_total` is the frozen submission weights the backend guard enforces for the current period; `applicable_total` is the template total for a future period; `future_*` totals feed the rebalancer. The full rule — which number is the source of truth per period type, and the one open edge case — is in `docs/weight-budget.md`.

## Notes

- `KPISubmission` rows are per employee, per metric (`KPITemplate`), per period (`year` + `month_or_quarter`, e.g. `January`) — an employee's monthly KPI is the set of submissions for that period.
- Data isolation is enforced in `app/services/kpi_service.py` (`visible_submissions_query`, `get_submission_scoped`) and `app/core/deps.py` (`RoleChecker`), not just in the UI.
- Auth is a signed JWT in an httpOnly `access_token` cookie (`app/core/security.py:61-82`), set with `Secure` only over HTTPS and `SameSite=Lax`. Login moved from `localStorage` Bearer tokens to httpOnly cookies on 2026-10-15 — the frontend no longer stores any token. Sessions are revoked by bumping the account's token version (password change / logout).

## Server cron jobs (required)

On the production server (`~/kpi-app`), these three jobs must exist in the
server user's crontab — all three are required, including the DB backup
(N2, 2026-11-12: the backup is the highest-stakes job, so it is not
"suggested"). Install with `crontab -e` — **do not paste a cron
line directly into the shell** (the shell will try to run it as a
command and fail with `0: command not found`). Verify with `crontab -l`.
All are idempotent, so running them more often than scheduled is
safe; missing them silently stops reminders, lets audit logs grow, and
leaves no backups to restore from. `scripts/check-cron-logs.sh` watches
all three (see `docs/monitoring.md` §2); the full backup/restore story is
in `docs/backup-and-restore.md`.

```cron
# KPI deadline reminders — daily (the job itself only sends inside the
# reminder window / grace week, and each user is reminded at most once
# per period). Since the 2026-11 Major-fix pass the cron path sends
# email synchronously: a failed email's notification row is deleted so
# the next run retries it instead of losing it silently.
0 8 * * * cd ~/kpi-app && docker compose exec -T web python -m app.jobs.send_deadline_reminders >> ~/kpi-app/deadline-reminders.log 2>&1

# Audit-log pruning — monthly (keeps 180 days; see app/jobs/prune_audit_logs.py).
0 3 1 * * cd ~/kpi-app && docker compose exec -T web python -m app.jobs.prune_audit_logs >> ~/kpi-app/prune-audit.log 2>&1

# Database backup — daily 02:00 (see scripts/backup-db.sh).
0 2 * * * cd ~/kpi-app && ./scripts/backup-db.sh >> ./backups/backup.log 2>&1
```
