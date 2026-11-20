// Playwright auth setup: log in once per role and save the browser
// state (cookies + localStorage tenant slug) for the specs.
//
// Why this exists: the login endpoint is rate-limited to 10 attempts
// per minute per IP, and the whole suite performs more logins than
// that when every spec logs in from scratch — the suite failed its
// own later specs with 429s. Logging in once here (3 attempts) and
// reusing the saved state keeps the full run under the limit, and it
// also makes the specs faster. Specs that test login itself
// (login.spec.js) still use loginAs() and never touch this state.
//
// Runs automatically as the "setup" project (see
// playwright.config.js); the state files live in e2e/.auth/,
// gitignored — they are local throwaway sessions for the scratch
// e2e database, never committed.
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { test as setup } from "@playwright/test";

import { accounts, loginAs } from "./helpers.js";

const authDir = join(dirname(fileURLToPath(import.meta.url)), ".auth");
mkdirSync(authDir, { recursive: true });

setup("authenticate as tenant admin", async ({ page }) => {
  await loginAs(page, accounts.tenantAdmin);
  await page.context().storageState({ path: join(authDir, "tenant-admin.json") });
});

setup("authenticate as dept admin", async ({ page }) => {
  await loginAs(page, accounts.deptAdmin);
  await page.context().storageState({ path: join(authDir, "dept-admin.json") });
});

setup("authenticate as employee", async ({ page }) => {
  await loginAs(page, accounts.employee);
  await page.context().storageState({ path: join(authDir, "employee.json") });
});
