import { expect, test } from "@playwright/test";
import { accounts } from "./helpers.js";

// Users happy path (PM review 2026-11-12, finding M9): the Add User /
// Add Employee modal is the other most-changed page, so it gets a real
// browser test. Both roles that may add users are covered — tenant
// admin ("Add User") and dept admin ("Add Employee", forced to their own
// department). Each test deletes the user it created, so reruns never
// collide with leftovers.
//
// Each describe runs authenticated from the saved auth state
// (auth.setup.js) instead of logging in — the login endpoint is
// rate-limited and the suite has more logins than the limit allows.

test.describe("tenant admin", () => {
  test.use({ storageState: "e2e/.auth/tenant-admin.json" });

  test("add user happy path: tenant admin creates an employee and deletes them", async ({
    page,
  }) => {
    const stamp = Date.now();
    const name = `E2E User ${stamp}`;
    const email = `e2e-user-${stamp}@example.test`;

    await page.goto("/admin/users");
    await page.getByRole("button", { name: "Add User", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("Add User");
    await dialog.locator("#add-user-name").fill(name);
    await dialog.locator("#add-user-email").fill(email);
    await dialog.locator("#add-user-password").fill(accounts.password);
    await dialog.locator("#add-user-department").selectOption({ index: 1 });
    await dialog
      .getByRole("button", { name: "Create User", exact: true })
      .click();

    await expect(page.getByText("User created.")).toBeVisible();
    await expect(dialog).toHaveCount(0);
    // Filter the list to the new user, then the row is unambiguous. (The
    // name renders twice — desktop table row + mobile card — so assert on
    // the table row, not the bare text.)
    await page.fill('input[placeholder="Search name or email…"]', email);
    await expect(page.locator("tr", { hasText: name })).toBeVisible();

    // Cleanup: delete the user we just created.
    await page
      .getByTitle(`Delete ${name} (also removes their KPI history)`)
      .click();
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "Delete", exact: true })
      .click();
    await expect(page.getByText(`${name} deleted.`)).toBeVisible();
    await expect(page.locator("tr", { hasText: name })).toHaveCount(0);
  });
});

test.describe("dept admin", () => {
  test.use({ storageState: "e2e/.auth/dept-admin.json" });

  test("add employee happy path: dept admin adds an employee to their own department", async ({
    page,
    browser,
  }) => {
    const stamp = Date.now();
    const name = `E2E Employee ${stamp}`;
    const email = `e2e-employee-${stamp}@example.test`;

    await page.goto("/admin/users");
    await page
      .getByRole("button", { name: "Add Employee", exact: true })
      .click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("Add Employee");
    await dialog.locator("#add-user-name").fill(name);
    await dialog.locator("#add-user-email").fill(email);
    await dialog.locator("#add-user-password").fill(accounts.password);
    await dialog
      .getByRole("button", { name: "Add Employee", exact: true })
      .click();

    await expect(page.getByText("Employee added.")).toBeVisible();
    await expect(dialog).toHaveCount(0);
    await page.fill('input[placeholder="Search name or email…"]', email);
    await expect(page.locator("tr", { hasText: name })).toBeVisible();

    // Cleanup: dept admins have no delete button (tenant-admin-only on
    // the backend too), so delete via the UI in a separate tenant-admin
    // context. Signing out this page would revoke the dept-admin
    // session the saved auth state was built from, and every later spec
    // reusing that state would land on /login instead.
    const tenantAdminContext = await browser.newContext({
      storageState: "e2e/.auth/tenant-admin.json",
    });
    const tenantAdminPage = await tenantAdminContext.newPage();
    await tenantAdminPage.goto("/admin/users");
    await tenantAdminPage.fill('input[placeholder="Search name or email…"]', email);
    await expect(
      tenantAdminPage.locator("tr", { hasText: name }),
    ).toBeVisible();
    await tenantAdminPage
      .getByTitle(`Delete ${name} (also removes their KPI history)`)
      .click();
    await tenantAdminPage
      .getByRole("alertdialog")
      .getByRole("button", { name: "Delete", exact: true })
      .click();
    await expect(
      tenantAdminPage.getByText(`${name} deleted.`),
    ).toBeVisible();
    await expect(
      tenantAdminPage.locator("tr", { hasText: name }),
    ).toHaveCount(0);
    await tenantAdminContext.close();
  });
});
