// Committed end-to-end suite for the KPI frontend (Playwright).
//
// Run from frontend/ with the dev backend running:
//
//   cd ~/kpi-system && DATABASE_URL=sqlite:///./kpi_system.db \
//     .venv/bin/python -m uvicorn app.main:app --port 8000 &
//   cd frontend && npm run dev &        # port 5173
//   npm run test:e2e
//
// The specs log in with the local dev seed accounts (tenant slug
// "abcmib": admin@local.test = tenant admin, manager@local.test =
// dept admin, aung@local.test = employee; password TestPass123!).
// Override with E2E_TENANT_SLUG / E2E_*_EMAIL / E2E_PASSWORD.
// The backend is NOT reset between specs: the diary spec uses a
// timestamp-unique work string and deletes its own entry afterwards,
// so reruns never collide with leftovers.
//
// The config starts the vite dev server itself (and reuses it if it
// is already running); the backend must already be up on :8000.
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
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://localhost:5173",
    trace: "retain-on-failure",
    ...(executablePath ? { launchOptions: { executablePath } } : {}),
  },
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: true,
    timeout: 60 * 1000,
  },
});
