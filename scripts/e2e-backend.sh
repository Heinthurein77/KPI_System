#!/usr/bin/env bash
# Boot the backend for the Playwright e2e suite (T-M1).
#
# Playwright's webServer runs this (see frontend/playwright.config.js):
# it seeds the dev tenant/users via scripts/seed_dev.py — which itself
# runs `alembic upgrade head` first — then serves the API on :8000 with
# uvicorn in the foreground, so Playwright's `url:` probe
# (GET /healthz) knows when the backend is up.
#
# Env overrides:
#   E2E_DATABASE_URL   database to seed + serve (default:
#                      sqlite:///./kpi-e2e.db in the repo root — a
#                      scratch file, gitignored via *.db, never the
#                      developer's kpi_system.db)
#   E2E_BACKEND_PORT   port to serve on (default: 8000)
#   SEED_PASSWORD      seed-user password (default: TestPass123!,
#                      local dev only — see scripts/seed_dev.py)
set -euo pipefail

cd "$(dirname "$0")/.."

export DATABASE_URL="${E2E_DATABASE_URL:-sqlite:///./kpi-e2e.db}"
export SECRET_KEY="${SECRET_KEY:-local-development-only-not-a-real-secret}"
export ENVIRONMENT="${ENVIRONMENT:-development}"
# The browser calls the API from the vite origin — the default must
# allow it. (Do NOT copy the test suite's http://testserver here: that
# origin is for TestClient, and a browser on :5173 would be CORS-blocked.)
export CORS_ORIGINS="${CORS_ORIGINS:-http://localhost:5173,http://127.0.0.1:5173}"
PORT="${E2E_BACKEND_PORT:-8000}"

# Prefer the repo venv; fall back to whatever python3 is on PATH.
PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
    PY="$(command -v python3 || command -v python)"
fi

"$PY" scripts/seed_dev.py
exec "$PY" -m uvicorn app.main:app --port "$PORT"
