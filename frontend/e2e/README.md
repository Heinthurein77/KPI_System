# End-to-end tests (Playwright)

Committed specs for login + daily-task confirm flows + dept-admin /
tenant-admin views. They run against the **local dev servers** — the
backend is not started by the config (it needs the dev database), the
vite dev server is started automatically (or reused if already up).

```sh
# Terminal 1 — backend (from the repo root):
DATABASE_URL=sqlite:///./kpi_system.db .venv/bin/python -m uvicorn app.main:app --port 8000

# Terminal 2 — frontend:
cd frontend
npm run test:e2e
```

Accounts default to the local dev seed (tenant slug `abcmib`,
password `TestPass123!`): employee `aung@local.test`, dept admin
`manager@local.test`, tenant admin `admin@local.test`. Override with
`E2E_TENANT_SLUG`, `E2E_EMPLOYEE_EMAIL`, `E2E_DEPT_ADMIN_EMAIL`,
`E2E_TENANT_ADMIN_EMAIL`, `E2E_PASSWORD`. Point at another frontend
with `PLAYWRIGHT_BASE_URL`.

The diary spec creates one entry with a timestamp-unique work string
and deletes it again in the same test, so reruns never collide with
leftovers. Do not run this suite against production: it writes real
diary rows into whichever backend the frontend points at.
