# End-to-end tests (Playwright)

Committed specs, run against **local dev servers that the config boots
itself** — no servers need to be up first:

```sh
cd frontend
E2E_PASSWORD=TestPass123! npm run test:e2e
```

The config starts both servers (or reuses them if already running):

- **Backend** — `scripts/e2e-backend.sh` runs `alembic upgrade head`,
  seeds the dev tenant/users via `scripts/seed_dev.py`, then serves the
  API on :8000 against a scratch SQLite file (`kpi-e2e.db` in the repo
  root, gitignored via `*.db`) — never the developer's `kpi_system.db`.
- **Frontend** — the vite dev server on :5173.

Before the specs, the `setup` project (`e2e/auth.setup.js`) logs in
once per role and saves the browser state to `e2e/.auth/`
(gitignored). The specs reuse that state instead of logging in from
scratch: the login endpoint is rate-limited to 10 attempts per minute,
and the suite performs more logins than that when every spec logs in
itself. `login.spec.js` tests login itself and never uses the saved
state.

| Spec | What it covers |
|---|---|
| `login.spec.js` (3 tests) | Valid login lands on the dashboard; wrong password stays on login with an error; logout returns to login. |
| `templates.spec.js` (1 test) | Dept admin opens the Templates page, adds a custom KPI to a fresh employee via the Add Custom KPI modal, sees it listed; cleans the template and the employee up afterwards. |
| `users.spec.js` (2 tests) | Tenant admin adds a user via the Add User modal and deletes them; dept admin adds an employee via the Add Employee modal (tenant admin deletes them afterwards — dept admins have no delete). |
| `workflow.spec.js` (1 test) | Core KPI workflow on the seeded accounts: employee self-scores and submits → dept admin approves → tenant admin finally approves; the submission ends `approved`. Resets the seeded template first and deletes it afterwards, so reruns start clean. |

Accounts default to the local dev seed (tenant slug `abcmib`,
password from `SEED_PASSWORD`, local-dev default `TestPass123!`):
employee `aung@local.test`, dept admin `manager@local.test`, tenant
admin `admin@local.test`. The specs read the password from
`E2E_PASSWORD` (or `SEED_PASSWORD`) — it is never hardcoded in the
specs; set it to whatever the seed was run with. Override the accounts
with `E2E_TENANT_SLUG`, `E2E_EMPLOYEE_EMAIL`, `E2E_DEPT_ADMIN_EMAIL`,
`E2E_TENANT_ADMIN_EMAIL`. Point at another frontend with
`PLAYWRIGHT_BASE_URL`, and at another backend API origin with
`E2E_API_BASE_URL` (used by `templates.spec.js` and `workflow.spec.js`
for their setup/cleanup calls; defaults to `http://localhost:8000`).

To re-seed by hand (or point the suite at a different database):

```sh
# From the repo root:
SEED_PASSWORD=... DATABASE_URL=sqlite:///./kpi-e2e.db python scripts/seed_dev.py
```

Specs use timestamp-unique values and clean up after themselves, so
reruns never collide with leftovers. Do not run this suite against
production: it writes real rows into whichever backend the frontend
points at.
