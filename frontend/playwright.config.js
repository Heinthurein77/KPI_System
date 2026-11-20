// Committed end-to-end suite for the KPI frontend (Playwright).
//
// Run from frontend/ with no servers up:
//
//   npm run test:e2e
//
// The config boots BOTH servers itself (scripts/e2e-backend.sh: runs
// `alembic upgrade head`, seeds the dev tenant/users via
// scripts/seed_dev.py, then serves the API on :8000) and the vite dev
// server on :5173, reusing either if already running. The backend
// serves a scratch SQLite file (kpi-e2e.db in the repo root, gitignored
// via *.db) — never the developer's kpi_system.db.
//
// The specs log in with the local dev seed accounts (tenant slug
// "abcmib": admin@local.test = tenant admin, manager@local.test =
// dept admin, aung@local.test = employee; password from SEED_PASSWORD,
// default TestPass123! for local dev only — see scripts/seed_dev.py).
// Override with E2E_TENANT_SLUG / E2E_*_EMAIL / E2E_PASSWORD.
// The backend is NOT reset between specs: specs must use
// timestamp-unique values and clean up after themselves,
// so reruns never collide with leftovers.
//
// Browser: Playwright's default full Chromium when installed
// (`npx playwright install chromium`); otherwise the headless shell
// already present on this machine is used automatically. Override
// with PLAYWRIGHT_CHROMIUM_PATH.
import { existsSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { defineConfig } from "@playwright/test";

function chromiumExecutable() {
  if (process.env.PLAYWRIGHT_CHROMIUM_PATH) return process.env.PLAYWRIGHT_CHROMIUM_PATH;
  const cache = join(homedir(), ".cache", "ms-playwright");
  try {
    for (const dir of readdirSync(cache)) {
      if (dir.startsWith("chromium-") && !dir.includes("headless")) {
        const exe = join(cache, dir, "chrome-linux", "chrome");
        if (existsSync(exe)) return undefined; // default full build
      }
    }
    for (const dir of readdirSync(cache)) {
      if (dir.startsWith("chromium_headless_shell-")) {
        const exe = join(cache, dir, "chrome-headless-shell-linux64", "chrome-headless-shell");
        if (existsSync(exe)) return exe;
      }
    }
  } catch {
    // No cache dir — let Playwright report the missing browser itself.
  }
  return undefined;
}

const executablePath = chromiumExecutable();

export default defineConfig({
  testDir: "./e2e",
  timeout: 30 * 1000,
  expect: { timeout: 10 * 1000 },
  fullyParallel: false,
  workers: 1, // specs share one dev database; keep them serial.
  reporter: "list",
  // Auth setup logs in once per role and saves the browser state
  // (e2e/.auth/, gitignored); specs reuse it instead of logging in
  // from scratch — the login endpoint is rate-limited to 10 attempts
  // per minute, and the suite needs more logins than that. login.spec.js
  // tests login itself and never uses the saved state.
  projects: [
    { name: "setup", testMatch: /auth\.setup\.js/ },
    { name: "e2e", testMatch: /.*\.spec\.js/, dependencies: ["setup"] },
  ],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://localhost:5173",
    trace: "retain-on-failure",
    ...(executablePath ? { launchOptions: { executablePath } } : {}),
  },
  webServer: [
    {
      // Backend: seed the dev tenant/users, then serve the API.
      // Reused when already running (e.g. a dev backend on :8000).
      command: "bash ../scripts/e2e-backend.sh",
      url: "http://localhost:8000/healthz",
      reuseExistingServer: true,
      timeout: 120 * 1000,
    },
    {
      command: "npm run dev",
      url: "http://localhost:5173",
      reuseExistingServer: true,
      timeout: 60 * 1000,
    },
  ],
});
