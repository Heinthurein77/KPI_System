import { expect, test } from "@playwright/test";
import { accounts, loginAs } from "./helpers.js";

// Users happy path (PM review 2026-11-12, finding M9): the Add User /
// Add Employee modal is the other most-changed page, so it gets a real
// browser test. Both roles that may add users are covered — tenant
// admin ("Add User") and dept admin ("Add Employee", forced to their own
// department). Each test deletes the user it created, so reruns never
// collide with leftovers.

test("add user happy path: tenant admin creates an employee and deletes them", async ({
  page,
}) => {
  const stamp = Date.now();
  const name = `E2E User ${stamp}`;
  const email = `e2e-user-${stamp}@example.test`;

  await loginAs(page, accounts.tenantAdmin);
  await page.goto("/admin/users");
  await page.getByRole("button", { name: "Add User", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("Add User");
  await dialog.locator("#add-user-name").fill(name);
  await dialog.locator("#add-user-email").fill(email);
  await dialog.locator("#add-user-password").fill("TestPass123!");
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

test("add employee happy path: dept admin adds an employee to their own department", async ({
  page,
}) => {
  const stamp = Date.now();
  const name = `E2E Employee ${stamp}`;
  const email = `e2e-employee-${stamp}@example.test`;

  await loginAs(page, accounts.deptAdmin);
  await page.goto("/admin/users");
  await page
    .getByRole("button", { name: "Add Employee", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("Add Employee");
  await dialog.locator("#add-user-name").fill(name);
  await dialog.locator("#add-user-email").fill(email);
  await dialog.locator("#add-user-password").fill("TestPass123!");
  await dialog
    .getByRole("button", { name: "Add Employee", exact: true })
    .click();

  await expect(page.getByText("Employee added.")).toBeVisible();
  await expect(dialog).toHaveCount(0);
  await page.fill('input[placeholder="Search name or email…"]', email);
  await expect(page.locator("tr", { hasText: name })).toBeVisible();

  // Cleanup: dept admins have no delete button (tenant-admin-only on
  // the backend too), so sign out and re-login as tenant admin, then
  // delete via the UI. (loginAs starts from /login, which redirects an
  // already-logged-in session away — hence the sign-out first.)
  await page.getByTitle("Sign out").click();
  await page.waitForURL(/\/login/);
  await loginAs(page, accounts.tenantAdmin);
  await page.goto("/admin/users");
  await page.fill('input[placeholder="Search name or email…"]', email);
  await expect(page.locator("tr", { hasText: name })).toBeVisible();
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
