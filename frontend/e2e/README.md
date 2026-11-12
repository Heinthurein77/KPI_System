# End-to-end tests (Playwright)

Committed specs, run against the **local dev servers** — the backend
is not started by the config (it needs the dev database), the vite dev
server is started automatically (or reused if already up).

| Spec | What it covers |
|---|---|
| `login.spec.js` (3 tests) | Valid login lands on the dashboard; wrong password stays on login with an error; logout returns to login. |
| `templates.spec.js` (1 test) | Dept admin opens the Templates page, adds a custom KPI to a fresh employee via the Add Custom KPI modal, sees it listed; cleans the template and the employee up afterwards. |
| `users.spec.js` (2 tests) | Tenant admin adds a user via the Add User modal and deletes them; dept admin adds an employee via the Add Employee modal (tenant admin deletes them afterwards — dept admins have no delete). |

```sh
# Terminal 1 — backend (from the repo root, scratch DB copy — never
# run the suite against the dev kpi_system.db in place):
cp kpi_system.db /tmp/kpi-e2e.db
DATABASE_URL=sqlite:////tmp/kpi-e2e.db .venv/bin/python -m uvicorn app.main:app --port 8000

# Terminal 2 — frontend:
cd frontend
VITE_API_BASE_URL=http://localhost:8000 npm run dev &  # port 5173
npm run test:e2e
```

Accounts default to the local dev seed (tenant slug `abcmib`,
password `TestPass123!`): employee `aung@local.test`, dept admin
`manager@local.test`, tenant admin `admin@local.test`. Override with
`E2E_TENANT_SLUG`, `E2E_EMPLOYEE_EMAIL`, `E2E_DEPT_ADMIN_EMAIL`,
`E2E_TENANT_ADMIN_EMAIL`, `E2E_PASSWORD`. Point at another frontend
with `PLAYWRIGHT_BASE_URL`, and at another backend API origin with
`E2E_API_BASE_URL` (used by `templates.spec.js` for its setup/cleanup
calls; defaults to `http://localhost:8000`).

Specs use timestamp-unique values and clean up after themselves, so
reruns never collide with leftovers. Do not run this suite against
production: it writes real rows into whichever backend the frontend
points at.
