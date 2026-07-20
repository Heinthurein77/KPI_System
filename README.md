# KPI Approval System

An Automated KPI Calculation and Approval System: a FastAPI JSON API backend with a
separately-deployed React (Vite) single-page frontend.

## Stack

- **Backend:** FastAPI (Python 3.11+), APIRouter-based modular layout, JSON API only
- **Frontend:** React (Vite, JavaScript) + Tailwind CSS, in `frontend/` — deployed independently
- **Database:** SQLAlchemy ORM — SQLite locally, PostgreSQL (Neon.tech) in production, selected automatically via `DATABASE_URL`
- **Auth:** Bearer-token session (signed JWT, sent via `Authorization` header, stored client-side), bcrypt password hashing, role-based access control
- **Deployment:** Docker, Railway-ready (`railway.json` at repo root for the backend, `frontend/railway.json` for the frontend)

## Project Layout

```
app/
  core/          # settings, security (hashing/JWT), auth & RBAC dependencies
  models/        # SQLAlchemy models: User, Department, KPITemplate, KPISubmission
  routers/       # auth, dashboard, kpi (workflow actions), admin (CRUD) — all under /api
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
2. **Department Admin** reviews submissions scoped to their own department only, can edit the score, then **Approve & Forward** → status `pending_final_approval`. Can also **Reject**. A Dept Admin's own custom KPI skips this stage and goes straight to the Super Admin.
3. **Super Admin** gives final approval (optionally adjusting the score) → status `approved`. Super Admin can also **override** the score and force-approve at any stage, and has visibility across all departments.

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
docker compose up --build
docker compose exec web python -m app.seed
```

Backend available at http://localhost:8000. Run the frontend separately with `npm run dev`.

### Local Setup with Docker against Neon (no local Postgres)

```bash
# .env (gitignored — never commit real credentials)
DATABASE_URL=postgresql://user:password@ep-xxxx-pooler.region.aws.neon.tech/dbname?sslmode=require&channel_binding=require

docker compose -f docker-compose.neon.yml up --build
docker compose -f docker-compose.neon.yml exec web python -m app.seed
```

Note: since `.env` is auto-loaded by `pydantic-settings`, once it contains a real
`DATABASE_URL`, **any** local command (including a bare `uvicorn app.main:app --reload`
outside Docker) will also connect to that database, not local SQLite. Remove or comment
out `DATABASE_URL` in `.env` to go back to local SQLite for casual dev.

## Production (Railway + Neon.tech)

This is a **two-service** deploy — the backend and frontend are separate Railway
services (each with its own Dockerfile and public URL).

1. Create a Postgres database on [Neon.tech](https://neon.tech) and copy its connection string.
2. **Backend service** — new Railway service from this repo, root directory `/` (repo root). Railway auto-detects `railway.json` + `Dockerfile`. Set env vars:
   - `DATABASE_URL` — your Neon connection string (`postgresql://...`)
   - `SECRET_KEY` — a long random string
   - `ENVIRONMENT=production`
   - `CORS_ORIGINS` — the frontend service's public URL once you have it (e.g. `https://kpi-frontend-production.up.railway.app`); comma-separate multiple origins if needed
3. **Frontend service** — new Railway service from the same repo, root directory `frontend`. Railway auto-detects `frontend/railway.json` + `frontend/Dockerfile`. Set a **build** variable:
   - `VITE_API_BASE_URL` — the backend service's public URL (e.g. `https://kpi-backend-production.up.railway.app`). This is baked into the JS bundle at build time, so it must be set *before* the first deploy, and the frontend must be redeployed if the backend's URL ever changes.
4. Deploy both services. Once the backend is up, run the seed script once via Railway's shell to bootstrap the Super Admin account — required, since there's no other way to create the first login:
   ```bash
   python -m app.seed
   ```
   Set `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_PASSWORD` / `SUPER_ADMIN_NAME` as backend env
   vars first if you don't want the default demo credentials.
5. Update the backend's `CORS_ORIGINS` with the frontend's actual Railway domain (and redeploy the backend) if you set it before the frontend had a domain assigned.

## Notes

- `KPISubmission` rows are per employee, per metric (`KPITemplate`), per period (`year` + `month_or_quarter`, e.g. `January`) — an employee's monthly KPI is the set of submissions for that period.
- Data isolation is enforced in `app/services/kpi_service.py` (`visible_submissions_query`, `get_submission_scoped`) and `app/core/deps.py` (`RoleChecker`), not just in the UI.
- Auth is a signed JWT sent as `Authorization: Bearer <token>`, stored client-side (`localStorage`) by the React app — not a cookie, since the frontend and backend are separate origins.
