# KPI Approval System

A production-ready Automated KPI Calculation and Approval System built with FastAPI, SQLAlchemy, and Jinja2 + Tailwind CSS.

## Stack

- **Backend:** FastAPI (Python 3.11+), APIRouter-based modular layout
- **Frontend:** Jinja2 server-rendered templates + Tailwind CSS (CDN)
- **Database:** SQLAlchemy ORM — SQLite locally, PostgreSQL (Neon.tech) in production, selected automatically via `DATABASE_URL`
- **Auth:** Cookie-based session (signed JWT), bcrypt password hashing, role-based access control
- **Deployment:** Docker / docker-compose, Render-ready (`render.yaml`)

## Project Layout

```
app/
  core/          # settings, security (hashing/JWT), auth & RBAC dependencies
  models/        # SQLAlchemy models: User, Department, KPITemplate, KPISubmission
  routers/       # auth, dashboard, kpi (workflow actions), admin (CRUD)
  services/      # kpi_service.py — all workflow/business logic
  schemas/       # Pydantic input schemas
  templates/     # Jinja2 templates (base layout, login, role dashboards, admin)
  static/        # css/js assets
  database.py    # engine/session setup, SQLite <-> Postgres switch
  main.py        # app factory, router wiring, startup table creation
  seed.py        # demo data seeder
```

## Roles & Workflow

1. **Employee** self-assesses KPI metrics for the current month (draft, editable) → submits → status `pending_dept_approval` (now read-only for the employee).
2. **Department Admin** reviews submissions scoped to their own department only, can edit the score, then **Approve & Forward** → status `pending_final_approval`. Can also **Reject**.
3. **Super Admin** gives final approval (optionally adjusting the score) → status `approved`. Super Admin can also **override** the score and force-approve at any stage, and has visibility across all departments.

## Local Setup (SQLite)

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt

copy .env.example .env        # optional, defaults already work locally

python -m app.seed            # creates tables + the one bootstrap Super Admin account
uvicorn app.main:app --reload
```

Visit http://127.0.0.1:8000 — you'll be redirected to `/login`.

### Bootstrap account

`python -m app.seed` creates **only** a Super Admin — no sample departments, employees,
or KPI metrics. Everything else (departments, dept admins, employees, KPI metrics) is
created from the admin UI after logging in.

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

App available at http://localhost:8000.

### Local Setup with Docker against Neon (no local Postgres)

To run the app in Docker but connect to a real Neon database instead of the local
Postgres container — useful for testing your production DB before deploying:

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

## Production (Render + Neon.tech)

1. Create a Postgres database on [Neon.tech](https://neon.tech) and copy its connection string.
2. In Render, create a new **Web Service** from this repo (Docker environment) — `render.yaml` is included as a Blueprint.
3. Set environment variables:
   - `DATABASE_URL` — your Neon connection string (`postgresql://...`)
   - `SECRET_KEY` — a long random string
   - `ENVIRONMENT=production`
4. Deploy. Tables are created automatically on startup. Run the seed script once via
   Render's shell to bootstrap the Super Admin account — required, since there's no
   other way to create the first login:
   ```bash
   python -m app.seed
   ```
   Set `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_PASSWORD` / `SUPER_ADMIN_NAME` as Render env
   vars first if you don't want the default demo credentials.

## Notes

- `KPISubmission` rows are per employee, per metric (`KPITemplate`), per period (`year` + `month_or_quarter`, e.g. `January`) — an employee's monthly KPI is the set of submissions for that period.
- Data isolation is enforced in `app/services/kpi_service.py` (`visible_submissions_query`, `get_submission_scoped`) and `app/core/deps.py` (`RoleChecker`), not just in the UI.
- Session auth uses an HttpOnly cookie holding a signed JWT (not OAuth2 bearer), since this is a server-rendered app rather than a JSON API client.
