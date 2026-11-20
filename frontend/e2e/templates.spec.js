import { expect, test } from "@playwright/test";
import { accounts } from "./helpers.js";

// Runs authenticated as the dept admin from the saved auth state
// (auth.setup.js) instead of logging in — the login endpoint is
// rate-limited and the suite has more logins than the limit allows.
test.use({ storageState: "e2e/.auth/dept-admin.json" });

// Templates happy path (PM review 2026-11-12, finding M9): the Add
// Custom KPI modal is the most-changed flow in the app, so it gets a
// real browser test, not just the backend suite.
//
// A fresh employee is created first (via the API, reusing the logged-in
// dept admin's session) because the weight budget is the period's
// submission-snapshot total: a seeded employee's department templates
// already total 100%, so no new KPI could fit for them, while a fresh
// employee starts at 0% and a 10% KPI always fits. The spec deletes
// both the template and the employee afterwards, so reruns never collide.

const API_BASE = process.env.E2E_API_BASE_URL || "http://localhost:8000";

test("add custom KPI happy path: dept admin assigns a KPI to a fresh employee", async ({
  page,
  browser,
}) => {
  const stamp = Date.now();
  const metricName = `E2E Custom KPI ${stamp}`;
  const employeeName = `E2E KPI Employee ${stamp}`;
  const employeeEmail = `e2e-kpi-emp-${stamp}@example.test`;

  // The page starts authenticated (storageState above), so no loginAs.

  // The API client sends X-Tenant-Slug on every request (from
  // localStorage); page.request does not, so pass it explicitly or the
  // backend cannot resolve the tenant (400).
  const tenantHeaders = { "X-Tenant-Slug": accounts.slug };
  const created = await page.request.post(`${API_BASE}/api/admin/users`, {
    headers: tenantHeaders,
    data: {
      name: employeeName,
      email: employeeEmail,
      password: accounts.password,
      role: "employee",
    },
  });
  expect(created.ok()).toBeTruthy();
  const employee = await created.json();

  let templateId = null;
  try {
    await page.goto("/admin/templates");
    await page
      .getByRole("button", { name: "Add Custom KPI", exact: true })
      .click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("Add Custom KPI");
    await dialog.locator("#custom-person").selectOption(String(employee.id));
    await dialog.locator("#custom-metric-name").fill(metricName);
    await dialog.locator("#custom-target").fill("100");
    await dialog.locator("#custom-weight").fill("10");
    await dialog
      .getByRole("button", { name: "Add Custom KPI", exact: true })
      .click();

    // Success toast (only the toast carries this sentence — the list
    // row shows the bare metric name), then the modal closes and the
    // new metric appears in the Templates table.
    await expect(
      page.getByText(`Custom KPI "${metricName}" added`),
    ).toBeVisible();
    await expect(dialog).toHaveCount(0);
    await expect(
      page.locator("tr", { hasText: metricName }),
    ).toBeVisible();
  } finally {
    // Cleanup: delete the template (dept admin may), then the employee
    // (tenant-admin-only on the backend). The employee delete runs in a
    // separate tenant-admin context: signing out this page would revoke
    // the dept-admin session the saved auth state was built from, and
    // every later spec reusing that state would land on /login instead.
    const listed = await page.request.get(`${API_BASE}/api/admin/templates`, {
      headers: tenantHeaders,
    });
    if (listed.ok()) {
      const templates = await listed.json();
      const mine = (templates.kpi_templates || templates).find(
        (t) => t.metric_name === metricName,
      );
      if (mine) {
        templateId = mine.id;
        await page.request.delete(
          `${API_BASE}/api/admin/templates/${templateId}`,
          { headers: tenantHeaders },
        );
      }
    }
    const tenantAdminContext = await browser.newContext({
      storageState: "e2e/.auth/tenant-admin.json",
    });
    const tenantAdminPage = await tenantAdminContext.newPage();
    await tenantAdminPage.request.delete(`${API_BASE}/api/admin/users/${employee.id}`, {
      headers: tenantHeaders,
    });
    await tenantAdminContext.close();
  }

  // The template is gone from the list after cleanup.
  if (templateId !== null) {
    await page.goto("/admin/templates");
    await expect(
      page.locator("tr", { hasText: metricName }),
    ).toHaveCount(0);
  }
});
