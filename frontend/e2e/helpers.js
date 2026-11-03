// Shared helpers for the e2e specs. Defaults are the local dev seed
// accounts (see playwright.config.js header); override via env vars.
export const accounts = {
  slug: process.env.E2E_TENANT_SLUG || "abcmib",
  password: process.env.E2E_PASSWORD || "TestPass123!",
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

export function uniqueWork(prefix = "E2E diary") {
  return `${prefix} ${Date.now()}`;
}
