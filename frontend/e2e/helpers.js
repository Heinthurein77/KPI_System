// Shared helpers for the e2e specs. Defaults are the local dev seed
// accounts (see playwright.config.js header); override via env vars.
//
// The seed password is NEVER hardcoded here: it comes from E2E_PASSWORD
// (or SEED_PASSWORD, the same variable scripts/seed_dev.py reads), so
// the value lives in exactly one place — the seed script, which
// documents the local-dev default. Running the suite needs it set, e.g.
//   E2E_PASSWORD=TestPass123! npm run test:e2e
// (or export E2E_PASSWORD once in the shell). A missing value fails
// fast with instructions instead of silently logging in with the
// wrong password.
const seedPassword = process.env.E2E_PASSWORD || process.env.SEED_PASSWORD;
if (!seedPassword) {
  throw new Error(
    "E2E_PASSWORD (or SEED_PASSWORD) is not set. Set it to the password " +
      "scripts/seed_dev.py seeded the dev accounts with " +
      "(local-dev default TestPass123!), e.g. " +
      "E2E_PASSWORD=TestPass123! npm run test:e2e",
  );
}

export const accounts = {
  slug: process.env.E2E_TENANT_SLUG || "abcmib",
  password: seedPassword,
  employee: process.env.E2E_EMPLOYEE_EMAIL || "aung@local.test",
  deptAdmin: process.env.E2E_DEPT_ADMIN_EMAIL || "manager@local.test",
  tenantAdmin: process.env.E2E_TENANT_ADMIN_EMAIL || "admin@local.test",
};

export async function loginAs(page, email, password = accounts.password) {
  await page.goto("/login");
  await page.fill("#orgSlug", accounts.slug);
  await page.fill("#email", email);
  await page.fill("#password", password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL(/\/dashboard/, { timeout: 15 * 1000 });
}

/** The (year, period) the KPI set is currently open for, by the same
 * rule as the backend (current month from day 8, else the previous
 * month inside its 7-day grace window). The dashboard defaults to the
 * current month, so specs that need the fillable period navigate to
 * /dashboard?year=&period= with this. */
export function openFillablePeriod(now = new Date()) {
  const MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
  ];
  if (now.getDate() >= 8) return { year: now.getFullYear(), period: MONTHS[now.getMonth()] };
  if (now.getMonth() === 0) return { year: now.getFullYear() - 1, period: "December" };
  return { year: now.getFullYear(), period: MONTHS[now.getMonth() - 1] };
}

/** M-37: headers for raw API calls from a spec. The backend rejects a
 * headerless cookie-authenticated mutation without an X-CSRF-Token, and
 * Playwright's page.request sends no Origin — so a raw POST/DELETE
 * carrying the session cookie 403s unless the double-submit token goes
 * with it. Read the csrf_token cookie from the page's context and send
 * it back as the header (the value only has to match the cookie — the
 * server keeps no copy). GETs don't need it, but the same headers are
 * harmless for them. */
export async function apiHeaders(page) {
  const cookies = await page.context().cookies();
  const csrf = cookies.find((c) => c.name === "csrf_token");
  const headers = { "X-Tenant-Slug": accounts.slug };
  if (csrf) headers["X-CSRF-Token"] = csrf.value;
  return headers;
}
